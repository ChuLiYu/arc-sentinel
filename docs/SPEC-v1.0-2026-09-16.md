# Arc Sentinel — OCI Always Free 版 MVP 規劃書

**以取得 Arc Microgrants 補助為目標的零成本部署方案**

文件版本：v1.0　基準日期：2026-09-16（溫哥華時間）
文件用途：本文件可直接交給負責實作的 AI coding agent（Claude Code、Cursor、Codex 等）作為完整規格書執行。凡標記【需人類決策】的項目，Agent 執行到該處應停下詢問使用者，不可自行假設後繼續。

---

## 0. 先讀重點：兩個會影響整個計畫可行性的最新事實

1. **OCI Always Free 的 Arm 運算額度已在 2026 年 6 月被砍半。** 網路上很多教學仍寫「4 OCPU / 24GB」，但 Oracle 已在 2026-06-15 悄悄把 Always Free 專屬帳號的 Ampere A1 額度降為 **2 OCPU / 12GB**（合計 1,500 OCPU-hours + 9,000 GB-hours／月），並自 2026-08-18 起對超額的 Always Free 執行個體強制關機。本規劃書全部以 **2 OCPU / 12GB 單台 VM** 為預算上限設計，不採用舊版 4/24 的數字。若使用者的帳號是 Pay-As-You-Go（已綁信用卡但消費在免費額度內），理論上可能仍保有 4/24，但這點各方說法不一致，Agent 不應假設，應先在 OCI Console 的 *Governance → Limits* 或建立執行個體時的 Shape 設定畫面確認實際可分配上限。
2. **【已更新 2026-09-16】RPC provider 已定案：Alchemy 為主、dRPC 為備援，且主網現在就能直接用。** 之前擔心 Arc 主網 RPC「還沒開放」是誤解——那只適用於 Circle 自家直營的 `https://rpc.mainnet.arc.io`（這條線目前仍標示 permissioned，需另外申請憑證），跟第三方節點商是兩回事。已直接查證 Arc 官方「Connect to Arc」文件頁，Alchemy／dRPC／QuickNode／Blockdaemon 的主網端點都已公開列出、可自助申請，Alchemy 官方 Arc 產品頁也明講免費方案涵蓋「All mainnets & testnets」。因此本專案**從一開始就直接對接 Arc Mainnet（chain ID 5042）**，不需要先卡在 testnet 等主網開放；唯一還是用 testnet 的地方是 CI 整合測試（理由是免費水龍頭方便重複測試，不是因為主網不能用，見 §10）。
   - Alchemy 主網 RPC：`https://arc-mainnet.g.alchemy.com/v2/YOUR_API_KEY`（WebSocket：`wss://arc-mainnet.g.alchemy.com/v2/YOUR_API_KEY`）
   - dRPC 主網 RPC（備援）：`https://rpc.drpc.mainnet.arc.io`
   - 這一步需要人類註冊 Alchemy 帳號並產生金鑰，Agent 不能代為完成，必須在 Day 1 提示使用者去申請，並將金鑰以環境變數方式提供給服務（詳見 §17 憑證總表）。
3. **【已更新 2026-09-16】【主網實戰教訓】** 用主網真實活躍錢包跑過一次端對端後發現兩件「測試網測不出來」的事：(a) Alchemy **Free tier** 的 `eth_getLogs` 最多只能查 10 個 block，且高頻查詢會吃 Compute Units（429），因此 RPC client 的 log 抓取要**小區塊切割**（預設 10）並靠 dRPC fallback 吸收尖峰；(b) 很多主網活躍錢包的 USDC 是由 **relayer 代提交**的，`tx.from/tx.to` 根本不含 agent 錢包，必須用 **Transfer log 的 from/to** 判定 agent 與方向（已修，見 §20.1/§20.2）。主網驗證務必挑「真的會動的地址」，單靠測試網綠燈不代表主網沒問題。

---

## 1. 專案目標與非目標

**目標（本 MVP 唯一要交付的東西）：**
一個開源、非託管（non-custodial）、唯讀的 Arc 主網監控服務 **Arc Sentinel**：持續索引指定的 Agent 錢包在 Arc 主網上的 USDC 流動，做預算比對與異常偵測，並透過 API／簡易 Dashboard 呈現。整台服務跑在 OCI Always Free 的一台 VM 上，成本為 0。

**非目標（MVP 明確不做，避免範圍膨脹拖垮 14 天期程）：**
- 不自動下單、不做套利、不代客交易、不簽署任何鏈上交易。
- 不保管任何私鑰或使用者資產（完全唯讀）。
- 不做多鏈支援，僅支援 Arc 主網（EVM-compatible，chain ID 5042）。
- 不做完整前端 SPA；Dashboard 用 FastAPI + Jinja2/HTMX 的伺服器端渲染頁面即可。
- ARC 代幣經濟、空投相關功能一律不做（目前無官方發行計畫）。

**為什麼選這個範圍**：Arc Microgrants 只要求「已部署並能在 Arc 正式主網運作」＋公開 GitHub repo＋專案說明＋公開開發者檔案，不要求營收或既有客戶。唯讀監控服務不涉及資金保管、不需要通過交易所/MSB 相關的高門檻合規，是在 14 天內能做到「主網上真的在跑」最務實的路徑。

---

## 2. Arc 網路技術基準參數（2026-09-16 查證）

| 參數 | 主網 (Mainnet) | 測試網 (Testnet) |
|---|---|---|
| Chain ID | `5042` | `5042002` |
| Gas / 手續費幣別 | USDC（原生記帳採 18 位小數） | USDC |
| USDC ERC-20 合約位址 | `0x3600000000000000000000000000000000000000`（6 位小數） | 依測試網公告 |
| 平均出塊時間 | 約 506ms（Malachite 共識，近乎即時最終性） | 約 480ms |
| 官方主要 RPC (HTTP) | `https://rpc.mainnet.arc.io`（主網階段標示 permissioned） | `https://rpc.testnet.arc.io` |
| 官方主要 RPC (WS) | 官方文件未列出 WS，需用下方 node provider | `wss://rpc.testnet.arc.io` |
| Node provider（皆提供 mainnet 金鑰申請） | Alchemy / QuickNode / dRPC / Blockdaemon | 同左 |
| 區塊瀏覽器 | `explorer.arc.io`（標示 permissioned） | `explorer.testnet.arc.io` |
| Faucet | 無主網水龍頭 | `faucet.circle.com` |
| RPC 介面 | 標準 Ethereum JSON-RPC（`eth_getLogs`、`eth_getBlockByNumber`、`eth_call`、`eth_subscribe` 等） | 同左 |

**重要細節：** 原生記帳（gas 相關欄位）用 18 位小數，但 USDC 這個 ERC-20 token 本身用 **6 位小數**。資料庫與程式碼裡任何金額欄位都要清楚標明「原始整數（wei-level）」還是「換算後的人類可讀金額」，兩者絕不能混用同一個欄位。

---

## 3. Arc Microgrants 資格對照表

| 官方要求 | 本專案對應設計 |
|---|---|
| 已部署並能在 Arc 正式主網運作 | Indexer 與 API 皆連線 Arc Mainnet（chain ID 5042），且會用一筆真實的小額 USDC 轉帳做端對端驗證（見 §14 Day 10-11） |
| 提供公開 GitHub Repository | Repo 全程開源（建議 MIT license），README 含架構圖、Demo 連結、如何自行部署 |
| 提供專案說明及公開開發者檔案 | README 含專案說明段落；GitHub profile／個人網站連結公開 |
| 可以是原型，不需要既有客戶或營收 | MVP 定位為「可運作原型」，商業驗證留到補助後的第二階段 |
| 申請截止 10/14 23:59 ET，滾動審查 | §14 的 14 天排程完整跑完後至少留 3-5 天緩衝再送件，越早送越好（滾動審查） |

---

## 4. 系統架構

```
┌───────────────────────────── OCI Always Free VM (Ampere A1, 2 OCPU / 12GB, Ubuntu 24.04) ─────────────────────────────┐
│                                                                                                                        │
│   ┌────────────┐      ┌───────────────┐      ┌───────────────┐      ┌────────────┐                                   │
│   │  Caddy     │◄────►│  FastAPI App   │◄────►│  PostgreSQL   │◄────►│  Indexer    │◄──── Arc Mainnet RPC            │
│   │ (TLS/反向  │      │ (API+Dashboard)│      │  (單一實例)    │      │  Worker     │      (Alchemy/QuickNode 等)     │
│   │  代理)     │      └───────────────┘      └───────┬───────┘      │ (背景常駐)   │                                   │
│   └────────────┘                                     │              └────────────┘                                   │
│                                                       │                                                               │
│                                              ┌────────▼────────┐                                                      │
│                                              │  Backup Cron    │──► OCI Object Storage (10GB free, 每日/每週備份)      │
│                                              │ (pg_dump排程)   │                                                      │
│                                              └─────────────────┘                                                      │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
             ▲
             │ GitHub Actions：lint → test → build image → push GHCR → SSH 觸發 docker compose pull && up -d
             │
        GitHub Repo (public)
```

**元件角色：**
- **Indexer Worker**：獨立 Python 常駐程序，用 HTTP 輪詢（非 WebSocket，理由見下）方式每 2-3 秒呼叫一次 `eth_getLogs` / `eth_getBlockByNumber`，只索引 `agents` 表中登記的錢包位址相關的交易與 USDC transfer log，寫入 Postgres，並維護 `indexer_state.last_indexed_block` 做斷點續傳。
- **FastAPI App**：對外唯一入口，提供 REST API + 一個唯讀的 Dashboard 頁面（Jinja2 模板，不需要前端建置流程，省資源也省開發時間）。
- **PostgreSQL**：單一實例，直接跑在同一台 VM 的 Docker 容器裡（不用 OCI Autonomous DB，因為那是 Oracle DB 不是 Postgres，且免費額度也才 20GB，自架 Postgres 更符合需求且完全免費）。
- **Caddy**：自動 HTTPS（Let's Encrypt），設定檔比 Nginx+Certbot 簡單很多，資源占用也小。
- **Backup Cron**：每日 `pg_dump` → gzip → 上傳到 OCI Object Storage，保留 7 日 + 4 週。

**為什麼用輪詢而不是 WebSocket 訂閱**：WebSocket 需要維護長連線與斷線重連邏輯，在 2 OCPU/12GB 的資源預算與 14 天工期下，輪詢的可靠性/複雜度比值更好。Arc 的共識機制（Malachite）提供近乎即時最終性，重組（reorg）風險極低，但仍保留「只索引到 head-2 個區塊」的安全緩衝，避免極端情況下索引到未最終確認的資料。

---

## 5. OCI Always Free 資源配置

| 資源 | 免費額度（2026-09） | 本專案用法 |
|---|---|---|
| Ampere A1 運算 | 2 OCPU / 12GB（合計上限，可切成 1 台或多台） | 單台 VM 全部吃滿，跑上述所有容器 |
| AMD Micro VM ×2 | 各 1/8 OCPU / 1GB | 選用：其中一台可跑輕量的外部健康檢查/狀態頁，或當作跳板機，不建議塞主要服務（記憶體太小） |
| Block Volume | 合計 200GB | 開 50GB boot volume + 50GB data volume 給主 VM 即可，留餘裕 |
| Object Storage | 10GB | 存 pg_dump 備份 |
| 對外流量 (Egress) | 10TB/月 | 本專案流量以 API 呼叫為主，遠低於此上限 |
| Load Balancer | 10Mbps flexible ×1 | MVP 不用，直接在 VM 上跑 Caddy＋公網 IP 即可，省一層複雜度 |
| Autonomous Database ×2 | 各 20GB（Oracle DB） | 不採用，因為需要 Postgres 而非 Oracle DB |

**已知風險與因應：**
- **區域容量不足（"Out of host capacity"）**：Ampere A1 在熱門區域（如美東）常搶不到，建議優先嘗試法蘭克福（`eu-frankfurt-1`）或新加坡（`ap-singapore-1`），通常較快申請成功；申請失敗就刷新重試或換區域。
- **資源天花板變動**：Oracle 過去半年沒公告就改免費額度，Agent 部署前應先到 OCI Console 實際確認當下數字，不要完全信任本文件寫死的數字。

---

## 6. 專案目錄結構

```
arc-sentinel/
├── README.md
├── LICENSE                      # MIT
├── docker-compose.yml
├── docker-compose.prod.yml
├── .env.example
├── Caddyfile
├── alembic.ini
├── pyproject.toml
├── app/
│   ├── main.py                  # FastAPI entrypoint
│   ├── config.py                # Pydantic Settings，讀 .env
│   ├── db/
│   │   ├── models.py
│   │   ├── session.py
│   │   └── migrations/          # alembic versions
│   ├── indexer/
│   │   ├── worker.py            # 主迴圈
│   │   ├── rpc_client.py        # JSON-RPC 呼叫封裝 + 重試/backoff
│   │   ├── decode.py            # log/transaction 解碼、decimal 換算
│   │   └── state.py             # indexer_state 讀寫
│   ├── rules/
│   │   ├── budget.py            # 預算比對規則（確定性規則，非 LLM）
│   │   └── anomaly.py           # 異常偵測規則
│   ├── api/
│   │   ├── routes_agents.py
│   │   ├── routes_transactions.py
│   │   ├── routes_alerts.py
│   │   ├── routes_stats.py
│   │   └── deps.py              # API Key 驗證
│   └── dashboard/
│       └── templates/
├── scripts/
│   ├── backup.sh
│   └── restore.sh
├── tests/
│   ├── unit/
│   └── integration/
└── .github/workflows/
    ├── ci.yml
    └── deploy.yml
```

---

## 7. 資料庫 Schema

所有金額欄位一律用 `NUMERIC`，禁止用 float。原始鏈上整數（wei-level，如 gas 相關欄位用 18 位、USDC 用 6 位）與換算後的人類可讀金額要分開存欄位，避免顯示層自己重算造成誤差累積。

```sql
-- 已登記要追蹤的 Agent 及其錢包
CREATE TABLE agents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    framework       TEXT,                      -- e.g. "Claude Code", "LangGraph", "custom"
    wallet_address  TEXT NOT NULL UNIQUE,       -- checksum EVM address
    owner_label     TEXT,                       -- 使用者自訂的擁有者/部門標籤
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active       BOOLEAN NOT NULL DEFAULT true
);

-- 索引到的每一筆交易（不限於 USDC transfer，也含合約呼叫）
CREATE TABLE transactions (
    tx_hash         TEXT PRIMARY KEY,
    block_number    BIGINT NOT NULL,
    block_time      TIMESTAMPTZ NOT NULL,
    from_address    TEXT NOT NULL,
    to_address      TEXT,
    agent_id        UUID REFERENCES agents(id),
    direction       TEXT NOT NULL,              -- 'outbound' | 'inbound'
    status          TEXT NOT NULL,              -- 'success' | 'failed'
    method_selector TEXT,                       -- 前 4 bytes of input data
    gas_used        NUMERIC(38,0),
    gas_fee_wei     NUMERIC(38,0),               -- 原生 18 位小數計價
    gas_fee_usdc    NUMERIC(20,6),               -- 換算後人類可讀
    raw_input_head  TEXT,                        -- 只存前 200 字元，避免大 blob 塞爆表
    indexed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_tx_agent_time ON transactions(agent_id, block_time DESC);

-- USDC ERC-20 Transfer event 明細（同一筆 tx 可能有多筆 transfer）
CREATE TABLE token_transfers (
    id              BIGSERIAL PRIMARY KEY,
    tx_hash         TEXT NOT NULL REFERENCES transactions(tx_hash),
    log_index       INT NOT NULL,
    token_address   TEXT NOT NULL,              -- USDC: 0x3600...0000
    from_address    TEXT NOT NULL,
    to_address      TEXT NOT NULL,
    amount_raw      NUMERIC(38,0) NOT NULL,      -- 6 位小數整數表示
    amount_usdc     NUMERIC(20,6) NOT NULL,
    UNIQUE (tx_hash, log_index)
);
CREATE INDEX idx_transfer_agent_time ON token_transfers(from_address, to_address);

-- 使用者設定的預算政策（對照 Circle CLI 的 spending policy，用來做「政策漂移」比對）
CREATE TABLE budgets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        UUID NOT NULL REFERENCES agents(id),
    period          TEXT NOT NULL,               -- 'daily' | 'weekly' | 'monthly'
    limit_usdc      NUMERIC(20,6) NOT NULL,
    effective_from  TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active       BOOLEAN NOT NULL DEFAULT true
);

-- 位址允許/封鎖清單（鏡射 Circle Agent Wallet 的 allow/block list，用來偵測不一致）
CREATE TABLE address_policies (
    id              BIGSERIAL PRIMARY KEY,
    agent_id        UUID NOT NULL REFERENCES agents(id),
    address         TEXT NOT NULL,
    list_type       TEXT NOT NULL               -- 'allow' | 'block'
);

-- 告警
CREATE TABLE alerts (
    id              BIGSERIAL PRIMARY KEY,
    agent_id        UUID REFERENCES agents(id),
    tx_hash         TEXT REFERENCES transactions(tx_hash),
    alert_type      TEXT NOT NULL,               -- 'budget_exceeded' | 'unknown_recipient' | 'failed_tx_spike' | 'gas_spike'
    severity        TEXT NOT NULL,               -- 'info' | 'warning' | 'critical'
    message         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    acked_at        TIMESTAMPTZ
);

-- 每日彙總（給 Dashboard 用，避免每次都全表掃描）
CREATE TABLE daily_agent_summary (
    agent_id        UUID NOT NULL REFERENCES agents(id),
    day             DATE NOT NULL,
    tx_count        INT NOT NULL DEFAULT 0,
    outbound_usdc   NUMERIC(20,6) NOT NULL DEFAULT 0,
    inbound_usdc    NUMERIC(20,6) NOT NULL DEFAULT 0,
    gas_fee_usdc    NUMERIC(20,6) NOT NULL DEFAULT 0,
    failed_tx_count INT NOT NULL DEFAULT 0,
    PRIMARY KEY (agent_id, day)
);

-- Indexer 斷點續傳狀態
CREATE TABLE indexer_state (
    chain_id        INT PRIMARY KEY,
    last_indexed_block BIGINT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- API 存取金鑰（MVP 用簡單雜湊比對即可，不需要完整 OAuth）
CREATE TABLE api_keys (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash        TEXT NOT NULL UNIQUE,
    label           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active       BOOLEAN NOT NULL DEFAULT true
);
```

用 Alembic 管理 migration，第一版直接用上面的 DDL 產生 baseline migration。

---

## 8. API 規格

Base path：`/api/v1`。寫入類端點需要 `X-API-Key` header；讀取類端點對公開 Dashboard 開放（展示透明度是差異化賣點之一），但仍建議加基本 rate limit 防濫用。

| Method | Path | 說明 | 認證 |
|---|---|---|---|
| GET | `/health` | 存活檢查，回傳 indexer 落後區塊數 | 無 |
| GET | `/agents` | 列出已登記的 agent | 無 |
| POST | `/agents` | 新增要追蹤的 agent／錢包 | API Key |
| GET | `/agents/{id}/summary?period=today\|7d\|30d` | 支出彙總 | 無 |
| GET | `/agents/{id}/transactions?since=&cursor=&limit=` | 交易明細（游標分頁） | 無 |
| GET | `/wallets/{address}/balance` | 即時查鏈上餘額（呼叫 RPC，含 5-10 秒快取） | 無 |
| POST | `/budgets` | 建立/更新預算政策 | API Key |
| GET | `/alerts?status=open&severity=` | 告警列表 | 無 |
| POST | `/alerts/{id}/ack` | 標記告警已處理 | API Key |
| GET | `/stats/network` | 全網彙總統計（總索引交易數、追蹤中 agent 數、累積 USDC 流量）— 用於首頁展示 | 無 |
| GET | `/export/ledger.csv?agent_id=&from=&to=` | 匯出對帳用 CSV | API Key |
| GET | `/docs` | FastAPI 自動產生的 OpenAPI 文件 | 無 |

MVP 階段不做 WebSocket 即時推播；告警改用「Dashboard 每次載入時查詢 open alerts」即可滿足展示需求，留給補助後的第二階段擴充。

---

## 9. 核心規則引擎（全部是確定性規則，不是 LLM 判斷）

**這一點是安全設計上的硬性要求**：任何會觸發告警、影響資金判斷的邏輯，一律用可測試、版本控制的規則程式碼實作，不能讓 LLM 自由判斷一筆交易是否異常或是否超支。理由：資金相關的判斷需要可重現、可稽核、可單元測試，LLM 的機率性輸出不適合作為金流控管的最終判準。

規則清單（皆在 `app/rules/` 實作，皆有對應單元測試）：
1. **budget_exceeded**：某 agent 在其設定的 period（日/週/月）內，`outbound_usdc` 累積超過 `budgets.limit_usdc` → 產生 `warning` 等級告警；超過 120% → `critical`。
2. **unknown_recipient**：交易的 `to_address` 不在該 agent 的 `address_policies` allow list 中（若該 agent 有設定 allow list）→ `warning`。
3. **failed_tx_spike**：同一 agent 在滾動 10 分鐘窗口內失敗交易數 ≥ 5 筆 → `warning`（可能代表 agent 邏輯出錯或私鑰/nonce 有問題）。
4. **gas_spike**：單筆 `gas_fee_usdc` 超過該 agent 過去 30 天平均值的 5 倍 → `info`。

每條規則都是純函式：輸入一組交易資料＋歷史彙總，輸出 0 或多筆 `alerts` 記錄，方便寫單元測試與之後好維護。

---

## 10. 測試計畫

| 層級 | 工具 | 內容 |
|---|---|---|
| 單元測試 | `pytest` + `pytest-asyncio` | decimal 換算函式、規則引擎（§9 四條規則）、log 解碼邏輯 |
| RPC 模擬 | `respx`（mock httpx）或錄製好的固定 JSON fixture | 不需要真的打鏈上請求就能測 indexer 的解析與寫入邏輯 |
| 整合測試 | `docker-compose.test.yml` 起一個乾淨 Postgres + 指向 **Arc Testnet**（chain ID 5042002，用 `faucet.circle.com` 領測試幣） | 驗證「送一筆測試網 USDC 轉帳 → indexer 抓到 → API 查得到 → 規則正確觸發」整條路徑 |
| CI | GitHub Actions | 每次 PR 自動跑單元＋整合測試，測試網金鑰用 GitHub Secrets 存，不進 repo |

**為什麼 CI/開發用測試網、正式環境才切主網**：整合測試需要頻繁送交易，用測試網的水龍頭幣完全免費且無限制；主網 USDC 是真錢，只在 §14 Day 10-11 做一次性、小額（例如 US$0.5-1）的端對端驗證即可。

---

## 11. CI/CD 與部署

### 11.1 docker-compose.yml（骨架）

```yaml
services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: arc_sentinel
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 10s
      retries: 5

  indexer:
    image: ghcr.io/${GITHUB_REPOSITORY}/indexer:latest
    restart: unless-stopped
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy

  api:
    image: ghcr.io/${GITHUB_REPOSITORY}/api:latest
    restart: unless-stopped
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
    expose:
      - "8000"

  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data

volumes:
  pgdata:
  caddy_data:
```

### 11.2 GitHub Actions（`ci.yml` 概念）

1. `on: pull_request` → ruff lint → pytest（單元＋整合，用臨時 Postgres service container＋Arc Testnet 金鑰）。
2. `on: push to main`（測試通過後）→ 用 `docker/build-push-action` 建置 image → push 到 `ghcr.io`（公開 repo 免費、無容量限制）。
3. 部署 job：用 `appleboy/ssh-action` SSH 進 OCI VM → `docker compose pull && docker compose up -d --remove-orphans` → 打 `/health` 端點確認起來。

SSH 私鑰、DB 密碼、RPC API Key 一律放 GitHub Actions Secrets，絕不進 repo；`.env.example` 只放欄位名稱不放值。

### 11.3 部署步驟（Day 9 執行）

1. 建立 OCI Ampere A1 VM（Ubuntu 24.04，2 OCPU/12GB，法蘭克福或新加坡區域優先），開一個 Reserved Public IP。
2. 資安基本盤：UFW 只開 22/80/443，22 限制來源 IP 或改用 OCI Bastion；建立非 root 使用者跑 docker。
3. 安裝 Docker Engine + Compose plugin。
4. `git clone` repo，複製 `.env.example` 為 `.env` 並填入實際值（DB 密碼、RPC API Key 等）。
5. 設定域名（可用免費的 DuckDNS/nip.io 起步，或用自己已有的網域），寫進 `Caddyfile` 讓其自動申請 TLS 憑證。
6. `docker compose up -d`，確認 `curl https://<domain>/health` 正常。
7. 把 SSH 金鑰、VM IP／域名補進 GitHub Actions Secrets，之後每次 push main 就自動部署。

---

## 12. 監控、備份與成本控管

- **健康檢查**：`/health` 回傳 indexer 落後區塊數；用免費的 GitHub Actions 排程 workflow（`schedule: cron`）每 5 分鐘打一次，失敗就開 GitHub Issue 或發 Discord webhook（零成本告警，不需要額外註冊付費監控服務）。
- **資源監控**：OCI 內建 Monitoring 對 Always Free 資源本身也提供基本 CPU/記憶體/磁碟告警，設定 disk > 80% 或 CPU 持續 > 90% 時發信。
- **備份**：`scripts/backup.sh` 由 host 上的 cron 每日跑 `pg_dump | gzip`，用 `oci-cli` 或 `rclone` 上傳到 Object Storage bucket，保留策略：7 個每日 + 4 個每週。
- **成本控管**：所有資源選型都卡在 Always Free 範圍內（見 §5 表格）；GitHub Actions 對公開 repo 完全免費；GHCR 對公開 image 完全免費。整個 MVP 期間預期支出為 **US$0**，唯一需要付費風險是 OCI 帳號驗證時的信用卡授權（僅為驗證用途，非扣款）。

---

## 13. 安全與合規邊界

- **完全唯讀、非託管**：Arc Sentinel 不持有、不接觸任何私鑰。追蹤的錢包是 Circle Agent Wallet（2-of-2 MPC，金鑰片段從不暴露），本服務只讀取公開鏈上資料與（選用）Circle API 的唯讀 scope，不具備簽署交易能力。
- **為什麼這樣設計能降低監管疑慮**：依加拿大 FINTRAC 對 MSB 的定義，代客保管、兌換或轉移資產才明確落入需註冊的範疇；純唯讀查詢/監控/分析通常不直接構成代客轉移資金，但仍建議在 README 明確聲明服務性質與定位，且不要把「非託管」當成完全免責的保證。
- **金鑰管理**：所有第三方 API Key（RPC provider、Circle 唯讀 API 如有使用）一律走環境變數／GitHub Secrets，不進版控；正式環境的 `.env` 檔案權限設 600。
- **依賴的第三方服務降級處理**：RPC provider 若回應錯誤或逾時，indexer 要有指數退避重試機制，並在連續失敗超過閾值時觸發自身的 `internal_rpc_down` 告警，而不是讓整個服務靜默卡死。

---

## 14. 14 天任務分解（對照申請截止 10/14 23:59 ET）

| Day | 主題 | 產出 |
|---|---|---|
| 1 | 帳號與存取準備 | OCI 帳號＋VM 規劃、【需人類決策】向 Alchemy 申請 Arc Mainnet 免費 API Key、GitHub repo 建立（含 README 骨架、MIT License） |
| 2-3 | 資料層 | Postgres schema（§7）+ Alembic baseline migration、`rpc_client.py`（含重試/backoff）、`decode.py`（decimal 換算單元測試先寫） |
| 4-5 | Indexer 核心 | 輪詢主迴圈、`indexer_state` 斷點續傳、先接 testnet 跑通端對端流程 |
| 6 | 規則引擎 | §9 四條規則 + 對應單元測試全綠 |
| 7-8 | API 與 Dashboard | FastAPI 路由（§8）+ Jinja2 簡易 Dashboard（agent 列表、支出圖、告警列表） |
| 9 | CI/CD 與部署 | GitHub Actions（lint/test/build/push）、OCI VM 起機、docker-compose + Caddy 部署上線 |
| 10-11 | 主網切換與驗證 | 【需人類決策：小額真實資金】切換 RPC 至 Arc Mainnet，從個人錢包送一筆小額 USDC 測試轉帳，驗證 indexer 在主網上正確索引、API 查得到、告警規則正確觸發 |
| 12 | 韌性與資安加固 | 備份腳本上線並驗證可還原、rate limit、結構化 log、UFW/SSH 檢查一次 |
| 13 | 文件與定位 | README 完稿（架構圖、Demo GIF、Why this matters）、公開開發者檔案更新 |
| 14 | 最終檢查與送件 | 對照 §3 資格表逐項打勾、提早送出 Arc Microgrants 申請（滾動審查，越早越好，不必等到最後一天） |

---

## 15. 給執行 Agent 的任務指令包（可直接複製貼上分階段執行）

> 使用方式：把下面每個 Phase 區塊依序貼給執行任務的 coding agent，一次做完一個 Phase 再進下一個。每個 Phase 都寫明輸入、產出與驗收標準，Agent 完成後應自我檢查驗收標準是否達成，未達成不要宣稱完成。

**Phase 0 — 環境確認【需人類先完成】**
- 輸入：OCI 帳號、Alchemy 的 Arc Mainnet API Key、一個網域或 DuckDNS 子網域。
- 產出：`.env` 檔案填妥（範本見 §11.1、§7 涉及的 DB 帳密）。
- 驗收：`curl` 用該 API Key 打一次 `eth_blockNumber` 到 Arc Mainnet RPC，能拿到目前區塊高度。

**Phase 1 — 資料層**
- 依 §7 建立 SQLAlchemy models + Alembic migration。
- 撰寫 `decode.py`：把 raw log 的 `data`／`topics` 解析成 `token_transfers` 需要的欄位，18 位與 6 位小數換算函式要各自有單元測試，且必須用 `Decimal`，不可用 float。
- 驗收：`pytest tests/unit/test_decode.py` 全綠。

**Phase 2 — Indexer（先打測試網）**
- 實作 `rpc_client.py`（含指數退避重試）與 `worker.py` 輪詢主迴圈，指向 Arc Testnet。
- 用 `faucet.circle.com` 的測試幣自己送一筆轉帳，確認能被索引進 `transactions` 與 `token_transfers`。
- 驗收：`indexer_state.last_indexed_block` 會持續前進，服務重啟後能從斷點續傳而不重複寫入（用 `tx_hash` 的 UNIQUE constraint 保證幂等）。

**Phase 2.5 — RPC 備援（dRPC fallback）**
- 實作主 RPC（Alchemy）與備援 RPC（dRPC）的自動切換：主 RPC 連續失敗超過閾值時，自動改用備援；備援也失敗時視為整體 RPC down（觸發 `internal_rpc_down`）。
- 驗收：兩個 RPC URL 都設定的情況下，模擬主 RPC 掛掉，indexer 仍能繼續索引；主 RPC 恢復後自動切回。

**Phase 3 — 規則引擎**
- 依 §9 實作四條規則，每條規則獨立檔案、獨立單元測試，測試需覆蓋「剛好在邊界值」與「明顯超過」兩種情境。
- 驗收：`pytest tests/unit/test_rules.py` 全綠，覆蓋率 ≥ 90%。

**Phase 4 — API 與 Dashboard**
- 依 §8 實作所有路由，寫入類路由要驗證 `X-API-Key`（從 `api_keys` 表比對，用 `SENTINEL_API_KEY_SALT` 雜湊）。
- Dashboard 首頁至少呈現：追蹤中 agent 數、近 7 日總支出、open alerts 列表。
- 驗收：`/docs` 可正常開啟 OpenAPI 文件；手動打每支 API 都回傳預期格式。

**Phase 5 — CI/CD**
- 依 §11.2 建立 `ci.yml`、`deploy.yml`。
- 驗收：開一個 PR，CI 自動跑測試；merge 進 main 後，image 自動 build 並 push 到 GHCR。

**Phase 6 — 部署上線（測試網）**
- 依 §11.3 步驟把服務部署到 OCI VM，先接測試網跑幾天觀察穩定性（CPU/記憶體是否在預算內、有無記憶體洩漏）。
- 驗收：`/health` 連續 24 小時查詢皆正常，落後區塊數維持在個位數。

**Phase 7 — 主網切換【需人類決策：牽涉真實資金】**
- 把 `.env` 的 RPC 端點與 chain ID 換成主網（5042），從人類提供的個人錢包送一筆小額（US$0.5-1）USDC 到某個測試接收位址，驗證 indexer 正確抓到這筆主網交易。
- 驗收：該筆交易出現在 API `/agents/{id}/transactions`，且 `explorer.arc.io`（若可存取）或所用 node provider 的 explorer 上能對上同一筆 tx hash。

**Phase 8 — 文件與送件材料**
- 補完 README：專案動機、架構圖（可直接用 §4 的文字圖）、如何本機跑起來、Demo 網址、License。
- 產出一份 1 頁的申請用專案說明（可另存 `SUBMISSION.md`），對照 §3 資格表逐項寫清楚已滿足。

---

## 16. 開放待決事項（需要使用者本人決定，Agent 不應自行假設）

1. ~~RPC provider 選哪家~~ 【已決定：Alchemy 為主、dRPC 為備援】——唯一還沒完成的動作是使用者本人去申請帳號＋Arc Mainnet API Key（見 §0 第 2 點）。
2. 部署用的網域名稱（買一個網域，或先用 DuckDNS/nip.io 起步）。
3. Phase 7 用哪一個人類擁有的錢包送測試轉帳、金額多少。
4. ~~是否要在 MVP 就整合 Circle CLI 的唯讀查詢~~ 【已決定：MVP 不整合，只靠鏈上資料；列入 README 的 Roadmap，日後有需求再加】。
5. ~~Open-source license 選 MIT 還是 Apache-2.0~~ 【已決定：MIT】。
6. ~~Dashboard 要不要公開~~ 【已決定：讀取端點公開（含 Dashboard 首頁與 `/stats/network`），寫入端點依原規劃維持 API Key 保護，不受影響】。

---

## 17. 憑證總表：哪些是真值、哪些是內部假值、放哪裡

**判斷原則只有一句話：只要這個值是拿去打「專案以外的真實系統」（RPC、未來的 Circle API），本機和正式環境必須用同一份真值，沒有「本機用假的」這個選項；只要是純粹內部接線用的值（本機 DB 密碼、本機自己雜湊用的鹽值），本機愛怎麼設都行。**

| 變數名稱 | 是什麼 | 敏感度 | 取得方式 | 存放位置 |
|---|---|---|---|---|
| `ARC_MAINNET_RPC_URL` | Alchemy 主網 RPC Key | 高 | Alchemy Dashboard（人類申請） | GitHub Secrets；部署時寫進 VM 的 `.env`；本機開發也用同一把真值 |
| `ARC_TESTNET_RPC_URL` | 同帳號另建一個 Testnet App | 高 | Alchemy Dashboard | GitHub Secrets（CI 整合測試用） |
| `ARC_MAINNET_RPC_FALLBACK_URL` | dRPC 主網端點，當 Alchemy 失敗時的備援 | 中 | dRPC 官網申請 | GitHub Secrets；VM `.env` |
| `CHAIN_ID` | `5042`（主網）／`5042002`（測試網） | 無，純設定值 | 直接寫死 | 程式碼或 docker-compose 預設值，不當 Secret |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | 自架 Postgres 帳密 | 中（純內部接線） | 自己設一組隨機密碼 | 本機/CI 可用任意假密碼；正式環境的真密碼放 GitHub Secrets → 寫進 VM `.env` |
| `DATABASE_URL` | 上面三個組成的連線字串 | 中 | 依上組出 | 同上 |
| `SENTINEL_API_KEY_SALT` | 服務自己雜湊 API Key 用的鹽值 | 中（純內部接線） | `openssl rand -hex 32` | GitHub Secrets → VM `.env`；本機測試可用任意假值 |
| 服務自己發的 API Key（`api_keys` 表） | 誰能打寫入端點 | 高 | 服務啟動後自行產生 | 存密碼管理器，不是環境變數 |
| `DEPLOY_SSH_PRIVATE_KEY` | GitHub Actions SSH 進 VM 部署用 | 極高 | 人類自行產生 key pair，公鑰放 VM | 只放 GitHub Secrets，絕不放進 VM 或 repo |
| `DEPLOY_HOST` / `DEPLOY_USER` | VM 的 IP/網域與 SSH 使用者 | 低 | OCI Console | GitHub Secrets 或 Actions Variables |
| `DUCKDNS_TOKEN` | 若用 DuckDNS 動態更新 IP | 高 | duckdns.org（人類申請） | 只需放 VM，不用進 GitHub |
| OCI Object Storage 存取金鑰（備份用） | rclone/oci-cli 憑證 | 高 | OCI Console → Identity → Customer Secret Keys | 只需放 VM，不用進 GitHub |
| `CIRCLE_API_KEY` | 只有選擇整合 Circle 唯讀查詢時才需要（§16） | 高 | Circle Developer Console（人類申請） | GitHub Secrets → VM |

任何私鑰（錢包簽章用的私鑰）**永遠不會、也不應該**出現在這個專案的任何檔案、環境變數或資料庫裡——Phase 7 的主網測試轉帳是人類自己在錢包 App 裡簽署送出，跟這份憑證表完全無關。

---

## 18. 補助評分角度的差異化強化（2026-09-16，依官方原文更新）

**這次是官方原文，不是推估。** 使用者直接取得了 Arc Microgrants 在 DoraHacks 上的完整頁面內容，「What we look for」原文是：

> Relevance to Arc, technical credibility, the quality of what you built, and whether the project is worth taking further. Promise counts for more than traction here.

四個維度：**跟 Arc 的相關性、技術可信度、做出來的東西本身的品質、專案值不值得繼續投資（潛力比現有牽引力更重要）**。以下調整對照這四個維度，並取代先前用 DoraHacks 通用框架反推的猜測（先前猜的「Profile Completion」等維度不是官方標準，已移除）：

1. **Relevance to Arc**：`alerts.message` 用人類可讀敘述（例如「Agent X 今天花了 $12.40，是每日 $10 預算的 124%」），並在 README 開頭第一段就直接回答官方要求的那句話——「這個專案做什麼、用 Arc 的什麼特性」（USDC 原生 gas、Agent Stack 錢包、sub-second finality），不要讓評審自己去猜。
2. **Technical credibility**：正確處理 USDC 的 18/6 位小數陷阱、CI 綠燈徽章、單元＋整合測試、resumable indexer——這些原本就在規劃裡，現在有了官方原文對應，優先順序不變。
3. **Quality of what you built**：必須是「已部署且真的在 Arc 主網上運作」，這點官方原文明講排除 testnet-only build，跟本規劃「一律以主網為終點」的方向完全一致，不需調整。
4. **Worth taking further / Promise > Traction**：README 的 Roadmap 段落（Circle CLI 唯讀整合、政策漂移偵測等尚未做的功能）要寫得有說服力，讓評審看到這是一個可以長出去的基礎設施，不是一次性的展示腳本。

**其他官方原文帶出的確認事項：**
- 明確排除 testnet-only build、design mockup、slide deck、已被 Circle/Arc 其他計畫資助過的專案。
- 可以化名／匿名提交，只有入選後才需要驗證身份，僅用於撥款。
- 公開建造者檔案不限 GitHub，X 或 Farcaster 帳號也算數。
- 撥款是付到「一個能在 Arc 上收 USDC 的錢包」，跟 Phase 7 用的測試錢包性質相同，屆時可以直接用同一個。
- 揭露聲明證實 Arc 是「permissioned validator set」運作——確認先前釐清的：受管控的是誰能出塊驗證，不是誰能讀 RPC。
- 提交表單要準備「一段話說明做了什麼、用 Arc 做什麼」，建議草稿：*"Arc Sentinel is an open-source, non-custodial monitoring layer for AI agent wallets on Arc. It indexes USDC flows in real time, enforces deterministic spend-policy and anomaly rules, and gives builders an audit trail their wallet provider doesn't."*（送件前可再打磨）

## 19. 參考來源（查證於 2026-09-16）

- Circle / Arc 官方開發者文件：`docs.arc.io/arc/references/rpc-endpoints`（Mainnet chain ID 5042、Testnet chain ID 5042002、node provider 清單）
- Arc Mainnet 上線報導與 USDC 合約位址（6 位小數 vs 原生 18 位小數）、驗證者與共識機制說明
- Oracle Cloud Always Free 額度變動報導（2026 年 6 月調降、8 月 18 日起強制關機超額執行個體）
- Circle Developers：`developers.circle.com/agent-stack`、`agent-stack/agent-wallets`（Agent Wallet 的 2-of-2 MPC、spending policy CLI 語法）
- 使用者先前對話中已查證之 Arc Microgrants 官方申請規則（500 USDC/專案、20 個名額、10/14 23:59 ET 截止、滾動審查）
- Arc Microgrants 收件平台 DoraHacks（`dorahacks.io/hackathon/arc-microgrants/detail`）及其通用評審框架說明（`dorahacks.io/blog/guides/judging-organizers`）

## 20. 開發實戰遇到的問題與解法（累積紀錄，持續更新）

> 這是開發/驗證過程中實際踩過的坑與對應解法，目的在讓未來的執行 Agent（或接手的人）不要重蹈覆轍。每一條都記錄：現象 → 根因 → 解法 → 驗證。

### 20.1 Alchemy Free tier 限制（2026-09-16 主網驗證時發現）

| 項目 | 限制 | 應對 |
|---|---|---|
| `eth_getLogs` block range | **最多 10 個 block**，超過回 `-32600` | 預設 `rpc_logs_max_range_size` 由 2000 改為 **10**（`app/config.py`）；環境變數 `RPC_LOGS_MAX_RANGE_SIZE` 可調 |
| Compute Units / 秒 | 高頻 polling（2-3s）+ 高吞吐 agent 錢包會 **429（CU/s 超限）** | RPC client 已有 retry + dRPC fallback（`ARC_MAINNET_RPC_FALLBACK_URL`）；本機測試期間 Alchemy↔dRPC 自動切換運作正常。正式環境建議維持 `POLL_INTERVAL_SECONDS=3.0` 並靠 fallback 吸收尖峰 |

**結論**：部署用 Alchemy Free tier 是可行但吃緊的，遇 429 是預期內（failover 設計就是為此），不是 bug。

### 20.2 Relayer-mediated 交易的 agent 歸屬 bug（2026-09-16 主網端對端測試抓到，commit `fab9de4` 修復）

**現象**：主網選的測試 agent 錢包（`0x8366...`，一個經 relayer 提交 USDC 的活躍錢包）全部交易被索引成 `agent_id=NULL` 且 `direction='inbound'`。

**根因**：Agent 的 USDC 是由 **relayer 代提交**的（`tx.from/tx.to` 是 relayer，不是 agent 錢包）。Block scan 永遠抓不到這類交易，全數由「Transfer log 補抓」路徑（`_parse_missing_tx`）進來——而舊實作用 `tx.from/tx.to` 判定 agent/direction，於是全部解析失敗。

**修法**：`_parse_missing_tx` 改吃已解碼的 `ParsedTransfer`，用 **Transfer log 的 from/to**（才是資金真正觸及的帳戶）判定 `agent_id` 與 `direction`；`tx` 層的 `from/to` 仍保留鏈上提交者原樣。加回歸測試（inbound relayer 補 agent_id 斷言 + 新增 outbound relayer case）。

**驗證**：修正後主網重跑，194 筆交易全數 `agent_id` 正確、`inbound=97/outbound=97`、agent 當 sender 的交易 167 筆全標 `outbound`（修正前為 0）。

**經驗教訓**：主網活躍地址很多是 relayer-driven；**測試網驗證通過不代表主網沒問題**，一定要用主網真實活躍錢包跑一次端對端。

### 20.3 官方 RPC 端點擋某些 HTTP User-Agent（2026-09-16 testnet 探測時發現）

**現象**：用 `urllib`（預設 UA `Python-urllib/...`）打 `rpc.testnet.arc.io` / `rpc.drpc.testnet.arc.io` 回 **403**，同一請求用 `curl` 或 `httpx` 則 200。

**根因**：provider 有 UA 過濾，擋掉常見程式庫預設 UA。

**應對**：正式程式（worker / rpc_client）用 httpx 不受影響；**探測腳本一律用 curl 或明確設定 UA**，不要用 urllib 裸請求。

### 20.4 `.env` 的 `API_KEY_HASH` 是「sha256 hex」不是明文（2026-09-16）

**現象**：使用者把明文 X-API-Key 填進 `API_KEY_HASH=`，寫入端點全部 403。

**根因**：`deps.py` legacy 認證路徑用 `hmac.compare_digest(_hash_api_key(key), api_key_hash)`，期望 64 字元 sha256 hex；放明文永遠比對失敗。

**應對**：`KEY=$(openssl rand -hex 24); printf '%s' "$KEY" | sha256sum` 產生 hash 填入 `.env`；明文 key 由使用者保管，送請求時放 `X-API-Key` header。正規多-key 路徑走 `SENTINEL_API_KEY_SALT` + `api_keys` 表（§17）。

### 20.5 Dashboard 路由是 `/api/v1/dashboard` 不是 `/`（2026-09-16）

**現象**：`curl /` 拿到 index.html，`curl /dashboard` 是 404。

**根因**：dashboard route 註冊在 `api = APIRouter(prefix="/api/v1")` 底下。

**應對**：Dashboard 完整路徑為 `/api/v1/dashboard`（`/` 只會拿到入口 index.html）。

### 20.6 規則引擎沒有執行者（2026-09-16 過夜 b' 檢查發現，未修）

**現象**：alerts 表永遠為空；dashboard 的「Open alerts」卡永遠 0。check_budget 等純函式只在測試與手動腳本中被呼叫。

**根因**：SPEC §9 只定義了規則的「純函式」形態，但**沒有定義誰在何時呼叫它們**。Indexer Worker 只做索引（寫 transactions/transfers），FastAPI 只做讀取，兩者都不呼叫 `app/rules/`。

**應對（待辦）**：需在規格補一個執行點，例如：Worker 每個 index cycle 結束後跑一次 `check_budget`（對昨日/今日 outbound 彙總），或獨立的排程 runner（頂多每 60s 跑一次即可，因為規則只對「已索引週期」的資料運算、成本極低）。這是 MVP 完成前必須補上的缺件，否則核心「監控告警」價值沒有落地。
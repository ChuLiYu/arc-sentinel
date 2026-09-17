# Arc Sentinel — 分類派工總表（Task Breakdown Index）

> 本檔是 `SPEC-v1.0-2026-09-16.md` 的執行索引。規格書是唯一事實來源；本檔只做任務分類、責任歸屬與依賴關係管理。

## 一、文件分類

| 面向 | 分類 | 對應規格章節 |
|---|---|---|
| 目標定義 | 申請補助的最小可運作原型（MVP） | §1、§3 |
| 技術基準 | Arc 主網/測試網參數 | §2 |
| 架構 | 單台 OCI VM + Docker Compose 四容器 | §4、§5 |
| 資料模型 | 9 張表 + Alembic | §7 |
| API | REST `/api/v1` + 公開 Dashboard | §8 |
| 業務規則 | 4 條確定性規則（不可用 LLM） | §9 |
| 品質 | 單元/整合/CI 測試三層 | §10 |
| 部署 | GitHub Actions → GHCR → OCI VM | §11 |
| 維運 | 監控、備份、成本控管 | §12 |
| 合規 | 唯讀非託管、金鑰管理、FINTRAC | §13 |
| 時程 | 14 天，滾動審查越早送越好 | §14 |
| 待決 | 6 項人類決策 | §16 |

## 二、任務卡（Work Items）

### WI-1 專案基座（今天）
- **負責**: Sisyphus-EM（本代理）
- **內容**: 規格建檔、git init、README 骨架、LICENSE (MIT，待 §16-5 確認)、`.env.example`、目錄結構
- **驗收**: repo 可 clone，目錄符合 §6

### WI-2 資料層 (Day 2-3)
- **負責**: backend-architect
- **內容**: SQLAlchemy models + Alembic baseline migration（§7 全部 9 表）、`decode.py` decimal 換算 + 單元測試
- **驗收**: `pytest tests/unit/test_decode.py` 全綠

### WI-3 Indexer 核心 (Day 4-5)
- **負責**: backend-architect
- **內容**: `rpc_client.py`（指數退避重試）、`worker.py` 輪詢主迴圈、`indexer_state` 斷點續傳、銜接 Arc Testnet
- **驗收**: last_indexed_block 前進、重啟不重複寫入（UNIQUE tx_hash）

### WI-4 規則引擎 (Day 6)
- **負責**: code-reviewer（審查安全）+ backend-architect（實作）
- **內容**: §9 四條規則，純函式 + 邊界值單元測試，覆蓋率 ≥ 90%
- **驗收**: `pytest tests/unit/test_rules.py` 全綠

### WI-5 API 與 Dashboard (Day 7-8)
- **負責**: backend-architect
- **內容**: §8 全部路由、X-API-Key 驗證、rate limit、Jinja2 Dashboard（agent 列表、支出圖、告警列表）
- **驗收**: `/docs` 可開、每支 API 回傳預期格式

### WI-6 CI/CD (Day 9)
- **負責**: devops
- **內容**: `ci.yml`（ruff→pytest→build→push GHCR）、`deploy.yml`（SSH→compose pull/up）、健康檢查排程
- **驗收**: PR 自動跑測試；merge main 自動部署

### WI-7 部署上線 (Day 9-11)
- **負責**: devops（+ 人類提供 OCI 帳號/VPS）
- **內容**: OCI A1 VM 起機、UFW、Caddy TLS、docker compose up、備份 cron
- **驗收**: `curl https://<domain>/health` 正常、落後區塊維持個位數

### WI-8 文件與送件 (Day 13-14)
- **負責**: ghostwriter
- **內容**: README 完稿、`SUBMISSION.md`（對照 §3 逐項）、Demo GIF
- **驗收**: 資格表逐項打勾、提早送件

## 三、人類決策阻塞點（BLOCKED_ON_HUMAN）

| ID | 決策 | 影響任務 | 截止 |
|---|---|---|---|
| H-1 | 選定 RPC provider（Alchemy/QuickNode/dRPC/Blockdaemon）並註冊取得 Arc Mainnet API Key | WI-2 後的一切主網動作、第 0 階段驗收 | Day 1（今天） |
| H-2 | OCI 帳號可用性 + VM 區域偏好 | WI-7 | Day 3 前 |
| H-3 | 網域名稱（購買 / DuckDNS / nip.io） | WI-5、WI-7 | Day 7 前 |
| H-4 | Phase 7 用哪個錢包 + 金額（建議 US$0.5-1） | WI-7 主網驗證 | Day 10 |
| H-5 | License: MIT vs Apache-2.0 | WI-1（LICENSE 檔） | 今天 |
| H-6 | Dashboard 公開 vs 私有 | WI-5 API 認證設計 | Day 7 |
| H-7 | 是否整合 Circle CLI 唯讀查詢 | WI-5 範圍 | Day 7 |

## 四、依賴圖

```
WI-1 ──► WI-2 ──► WI-3 ──► WI-4 ──► WI-5 ──► WI-6 ──► WI-7 ──► WI-8
                ▲              ▲                           │
           H-5(今天)      H-1(Day1)                   H-2/H-3/H-4
                                                      H-6/H-7(Day7)
```

**關鍵路徑**：WI-1 → WI-2 → WI-3 → WI-4 → WI-5 → WI-6 → WI-7 → WI-8 = 14 天。
**可並行**：H-1~H-4（人類）與 WI-2~WI-4（機器）可同時進行。

## 五、風險登錄

| 風險 | 等級 | 因應 |
|---|---|---|
| Arc Mainnet RPC permissioned，key 申請延遲 | 高 | 立即啟動 H-1；開發全程用 Testnet（chain 5042002），不受阻塞 |
| OCI A1 區域容量不足 | 中 | 優先 eu-frankfurt-1 / ap-singapore-1；失敗換區域 |
| OCI 免費額度再度變動 | 中 | 部署前以 OCI Console 實測，不信文件死數字 |
| 14 天滑期 | 中 | 滾動審查 → 只要「主網上線」先達成即可送件，文件可後補 |
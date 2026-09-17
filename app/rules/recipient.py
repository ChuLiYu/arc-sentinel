"""Rule 2 — unknown_recipient (SPEC §9).

A transaction whose ``to_address`` is not on the agent's **allow list** is
flagged ``warning``. Only triggered when the agent actually has an allow list
configured — no policy means no comparison is possible (spec: "若該 agent 有設定
allow list").
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from app.rules.common import AlertDraft


def _norm(address: str) -> str:
    return address.strip().lower()


def check_unknown_recipient(
    *,
    agent_id: uuid.UUID,
    agent_name: str,
    tx_hash: str,
    to_address: str | None,
    allow_list: Iterable[str] | None,
) -> list[AlertDraft]:
    """Flag a tx whose recipient is not on the agent's allow list.

    ``allow_list`` may be ``None`` (agent has no policy → no alert), an empty
    iterable (policy with zero addresses → everything unknown), or a set of
    checksum/lowercase EVM addresses.
    """
    if allow_list is None:
        return []

    allowed = {_norm(a) for a in allow_list}
    if to_address is None or _norm(to_address) not in allowed:
        display = to_address or "<no recipient>"
        return [
            AlertDraft(
                agent_id=agent_id,
                alert_type="unknown_recipient",
                severity="warning",
                message=(
                    f"Agent {agent_name} paid an address not on the allow list: "
                    f"{display} (tx {tx_hash})"
                ),
                tx_hash=tx_hash,
            )
        ]
    return []


def check_blocked_recipient(
    *,
    agent_id: uuid.UUID,
    agent_name: str,
    tx_hash: str,
    to_address: str | None,
    block_list: Iterable[str] | None,
) -> list[AlertDraft]:
    """Complementary check for the block list (mirrors Circle allow/block).

    A tx sent to an explicitly blocked address is ``critical`` regardless of
    whether an allow list exists.
    """
    if block_list is None or to_address is None:
        return []

    blocked = {_norm(a) for a in block_list}
    if _norm(to_address) in blocked:
        return [
            AlertDraft(
                agent_id=agent_id,
                alert_type="blocked_recipient",
                severity="critical",
                message=(
                    f"Agent {agent_name} paid a blocked address: {to_address} "
                    f"(tx {tx_hash})"
                ),
                tx_hash=tx_hash,
            )
        ]
    return []

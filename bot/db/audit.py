"""Auditoria interna e idempotência de eventos do Stripe.

``stripe_events`` garante que cada evento do Stripe seja processado uma única
vez (chave primária = event id), mesmo com reentregas/retries do webhook.
``audit_log`` registra ações administrativas e operacionais para investigação.
Nenhum campo aqui contém segredos, cartões ou conteúdo de entrega.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from aiosqlite import Row

from .core import Executor, utcnow_iso


# --------------------------------------------------------------- eventos Stripe
async def claim_event(ex: Executor, event_id: str, event_type: str) -> bool:
    """Registra recebimento do evento. False = duplicado (já visto antes)."""
    cur = await ex.execute(
        "INSERT OR IGNORE INTO stripe_events (event_id, type, status, received_at)"
        " VALUES (?, ?, 'received', ?)",
        (event_id, event_type, utcnow_iso()),
    )
    return cur.rowcount > 0


async def mark_event(ex: Executor, event_id: str, status: str) -> None:
    await ex.execute(
        "UPDATE stripe_events SET status = ?, processed_at = ? WHERE event_id = ?",
        (status, utcnow_iso(), event_id),
    )


async def get_event(ex: Executor, event_id: str) -> Optional[Row]:
    return await ex.fetchone("SELECT * FROM stripe_events WHERE event_id = ?", (event_id,))


# -------------------------------------------------------------------- auditoria
async def record(
    ex: Executor,
    *,
    guild_id: Optional[int],
    actor_id: Optional[int],
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> int:
    cur = await ex.execute(
        """
        INSERT INTO audit_log (guild_id, actor_id, action, target_type, target_id, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            actor_id,
            action,
            target_type,
            target_id,
            json.dumps(details or {}, ensure_ascii=False, default=str),
            utcnow_iso(),
        ),
    )
    return int(cur.lastrowid)


async def recent(ex: Executor, guild_id: Optional[int], limit: int = 15) -> list[Row]:
    if guild_id is None:
        return await ex.fetchall("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
    return await ex.fetchall(
        "SELECT * FROM audit_log WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
        (guild_id, limit),
    )

"""Repositório de carteiras, ledger financeiro e recargas (topups).

Regra de ouro: o saldo NUNCA muda sem uma linha correspondente em
``ledger_transactions`` (histórico auditável).
"""
from __future__ import annotations

import uuid
from typing import Optional

from aiosqlite import Row

from .core import Executor, utcnow_iso


# --------------------------------------------------------------------- carteira
async def get_wallet(ex: Executor, user_id: int) -> Optional[Row]:
    return await ex.fetchone("SELECT * FROM wallets WHERE user_id = ?", (user_id,))


async def get_or_create_wallet(ex: Executor, user_id: int, currency: str) -> Row:
    row = await get_wallet(ex, user_id)
    if row is not None:
        return row
    now = utcnow_iso()
    await ex.execute(
        "INSERT INTO wallets (user_id, currency, balance_cents, created_at, updated_at)"
        " VALUES (?, ?, 0, ?, ?)",
        (user_id, currency, now, now),
    )
    return await get_wallet(ex, user_id)  # type: ignore[return-value]


async def apply_delta(ex: Executor, user_id: int, delta_cents: int, currency: str) -> int:
    """Aplica variação de saldo (com sinal) e retorna o novo saldo."""
    await get_or_create_wallet(ex, user_id, currency)
    await ex.execute(
        "UPDATE wallets SET balance_cents = balance_cents + ?, updated_at = ? WHERE user_id = ?",
        (delta_cents, utcnow_iso(), user_id),
    )
    row = await get_wallet(ex, user_id)
    return int(row["balance_cents"])  # type: ignore[index]


# ------------------------------------------------------------------------ ledger
async def add_ledger(
    ex: Executor,
    *,
    user_id: int,
    guild_id: Optional[int],
    amount_cents: int,
    operation_amount_cents: int,
    currency: str,
    type: str,
    status: str,
    stripe_ref: Optional[str] = None,
    reference_type: Optional[str] = None,
    reference_id: Optional[int] = None,
    reason: Optional[str] = None,
    actor_id: Optional[int] = None,
) -> int:
    cur = await ex.execute(
        """
        INSERT INTO ledger_transactions (
            tx_uuid, user_id, guild_id, amount_cents, operation_amount_cents, currency,
            type, status, stripe_ref, reference_type, reference_id, reason, actor_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            user_id,
            guild_id,
            amount_cents,
            operation_amount_cents,
            currency,
            type,
            status,
            stripe_ref,
            reference_type,
            reference_id,
            reason,
            actor_id,
            utcnow_iso(),
        ),
    )
    return int(cur.lastrowid)


async def complete_ledger(ex: Executor, ledger_id: int, stripe_ref: Optional[str] = None) -> None:
    await ex.execute(
        """
        UPDATE ledger_transactions
           SET status = 'completed',
               completed_at = ?,
               stripe_ref = COALESCE(?, stripe_ref)
         WHERE id = ?
        """,
        (utcnow_iso(), stripe_ref, ledger_id),
    )


async def set_ledger_status(ex: Executor, ledger_id: int, status: str) -> None:
    await ex.execute("UPDATE ledger_transactions SET status = ? WHERE id = ?", (status, ledger_id))


async def list_ledger(
    ex: Executor, user_id: int, limit: int = 10, offset: int = 0
) -> list[Row]:
    return await ex.fetchall(
        "SELECT * FROM ledger_transactions WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    )


async def recent_ledger(ex: Executor, guild_id: Optional[int], limit: int = 15) -> list[Row]:
    if guild_id is None:
        return await ex.fetchall(
            "SELECT * FROM ledger_transactions ORDER BY id DESC LIMIT ?", (limit,)
        )
    return await ex.fetchall(
        "SELECT * FROM ledger_transactions WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
        (guild_id, limit),
    )


# ------------------------------------------------------------------------ topups
async def create_topup(ex: Executor, user_id: int, amount_cents: int, currency: str) -> int:
    cur = await ex.execute(
        "INSERT INTO topups (user_id, amount_cents, currency, status, created_at)"
        " VALUES (?, ?, ?, 'pending', ?)",
        (user_id, amount_cents, currency, utcnow_iso()),
    )
    return int(cur.lastrowid)


async def attach_topup_session(ex: Executor, topup_id: int, session_id: str) -> None:
    await ex.execute(
        "UPDATE topups SET stripe_session_id = ? WHERE id = ?", (session_id, topup_id)
    )


async def get_topup(ex: Executor, topup_id: int) -> Optional[Row]:
    return await ex.fetchone("SELECT * FROM topups WHERE id = ?", (topup_id,))


async def get_topup_by_session(ex: Executor, session_id: str) -> Optional[Row]:
    return await ex.fetchone("SELECT * FROM topups WHERE stripe_session_id = ?", (session_id,))


async def get_topup_by_payment_intent(ex: Executor, payment_intent: str) -> Optional[Row]:
    return await ex.fetchone(
        "SELECT * FROM topups WHERE stripe_payment_intent_id = ? ORDER BY id DESC LIMIT 1",
        (payment_intent,),
    )


async def mark_topup_paid(ex: Executor, topup_id: int, payment_intent: Optional[str]) -> bool:
    """Transição pending -> paid. Retorna False se já estava paga (idempotência)."""
    cur = await ex.execute(
        "UPDATE topups SET status = 'paid', paid_at = ?, stripe_payment_intent_id = COALESCE(?, stripe_payment_intent_id)"
        " WHERE id = ? AND status = 'pending'",
        (utcnow_iso(), payment_intent, topup_id),
    )
    return cur.rowcount > 0


async def set_topup_status(ex: Executor, topup_id: int, status: str) -> None:
    await ex.execute("UPDATE topups SET status = ? WHERE id = ?", (status, topup_id))

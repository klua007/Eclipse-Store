"""Repositório de pedidos.

O pedido guarda um SNAPSHOT do conteúdo entregue (tipo, payload, arquivo),
de modo que edições futuras do produto não alteram o que o cliente comprou.
"""
from __future__ import annotations

from typing import Optional

from aiosqlite import Row

from .core import Executor, utcnow_iso


async def create_order(
    ex: Executor,
    *,
    guild_id: int,
    buyer_id: int,
    product: Row,
    payment_method: str,
    status: str,
    stripe_session_id: Optional[str] = None,
) -> int:
    cur = await ex.execute(
        """
        INSERT INTO orders (
            guild_id, buyer_id, product_id, product_name, price_cents, currency,
            payment_method, status, delivery_status, delivery_type, delivery_payload,
            delivery_file_name, delivery_file_path, stripe_session_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id,
            buyer_id,
            product["id"],
            product["name"],
            product["price_cents"],
            product["currency"],
            payment_method,
            status,
            product["delivery_type"],
            product["delivery_payload"],
            product["delivery_file_name"],
            product["delivery_file_path"],
            stripe_session_id,
            utcnow_iso(),
        ),
    )
    return int(cur.lastrowid)


async def get_order(ex: Executor, order_id: int) -> Optional[Row]:
    return await ex.fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))


async def get_order_by_session(ex: Executor, session_id: str) -> Optional[Row]:
    return await ex.fetchone(
        "SELECT * FROM orders WHERE stripe_session_id = ?", (session_id,)
    )


async def get_order_by_payment_intent(ex: Executor, payment_intent: str) -> Optional[Row]:
    return await ex.fetchone(
        "SELECT * FROM orders WHERE stripe_payment_intent_id = ? ORDER BY id DESC LIMIT 1",
        (payment_intent,),
    )


async def list_orders(
    ex: Executor,
    guild_id: Optional[int] = None,
    buyer_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Row]:
    clauses, params = [], []
    if guild_id is not None:
        clauses.append("guild_id = ?")
        params.append(guild_id)
    if buyer_id is not None:
        clauses.append("buyer_id = ?")
        params.append(buyer_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    sql = "SELECT * FROM orders"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return await ex.fetchall(sql, params)


async def mark_paid(
    ex: Executor, order_id: int, payment_intent: Optional[str] = None
) -> bool:
    """Transição awaiting_payment -> paid (idempotente por condição de status)."""
    cur = await ex.execute(
        """
        UPDATE orders
           SET status = 'paid',
               paid_at = ?,
               stripe_payment_intent_id = COALESCE(?, stripe_payment_intent_id)
         WHERE id = ? AND status = 'awaiting_payment'
        """,
        (utcnow_iso(), payment_intent, order_id),
    )
    return cur.rowcount > 0


async def set_status(ex: Executor, order_id: int, status: str) -> None:
    await ex.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))


async def set_delivery(
    ex: Executor,
    order_id: int,
    status: str,
    via: Optional[str] = None,
) -> None:
    delivered_at = utcnow_iso() if status == "delivered" else None
    await ex.execute(
        """
        UPDATE orders
           SET delivery_status = ?,
               delivered_via = COALESCE(?, delivered_via),
               delivered_at = COALESCE(?, delivered_at)
         WHERE id = ?
        """,
        (status, via, delivered_at, order_id),
    )

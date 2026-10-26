"""Repositório de produtos e reservas de estoque.

Estoque: ``stock`` NULL = ilimitado; ``reserved_qty`` conta unidades reservadas
aguardando pagamento Stripe. Estoque disponível = stock - reserved_qty.
Toda baixa/consumo é feita com UPDATE condicional (WHERE garante disponível),
impedindo estoque negativo e venda duplicada da última unidade.
"""
from __future__ import annotations

from typing import Optional

from aiosqlite import Row

from .core import Executor, utcnow_iso

DELIVERY_TYPES = ("text", "file", "code", "link", "custom")

_PRODUCT_FIELDS = {
    "name", "description", "category", "price_cents", "currency", "active",
    "delivery_type", "delivery_payload", "delivery_file_name", "delivery_file_path",
    "stock", "low_stock_threshold", "seller_id",
}


def available_stock(row: Row) -> Optional[int]:
    """Estoque disponível para venda (None = ilimitado)."""
    if row["stock"] is None:
        return None
    return int(row["stock"]) - int(row["reserved_qty"])


# ---------------------------------------------------------------------- produtos
async def create_product(
    ex: Executor,
    *,
    guild_id: int,
    name: str,
    description: str,
    category: str,
    price_cents: int,
    currency: str,
    delivery_type: str,
    delivery_payload: Optional[str] = None,
    delivery_file_name: Optional[str] = None,
    delivery_file_path: Optional[str] = None,
    stock: Optional[int] = None,
    low_stock_threshold: Optional[int] = None,
    seller_id: int,
    active: bool = True,
) -> int:
    if delivery_type not in DELIVERY_TYPES:
        raise ValueError(f"delivery_type inválido: {delivery_type}")
    now = utcnow_iso()
    cur = await ex.execute(
        """
        INSERT INTO products (
            guild_id, name, description, category, price_cents, currency, active,
            delivery_type, delivery_payload, delivery_file_name, delivery_file_path,
            stock, reserved_qty, low_stock_threshold, seller_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
        """,
        (
            guild_id, name, description, category, price_cents, currency,
            1 if active else 0, delivery_type, delivery_payload,
            delivery_file_name, delivery_file_path, stock, low_stock_threshold,
            seller_id, now, now,
        ),
    )
    return int(cur.lastrowid)


async def get_product(ex: Executor, product_id: int) -> Optional[Row]:
    return await ex.fetchone("SELECT * FROM products WHERE id = ?", (product_id,))


async def list_products(
    ex: Executor, guild_id: int, include_inactive: bool = False
) -> list[Row]:
    sql = "SELECT * FROM products WHERE guild_id = ?"
    if not include_inactive:
        sql += " AND active = 1"
    sql += " ORDER BY category, name"
    return await ex.fetchall(sql, (guild_id,))


async def list_categories(ex: Executor, guild_id: int) -> list[Row]:
    return await ex.fetchall(
        "SELECT DISTINCT category FROM products WHERE guild_id = ? AND active = 1 ORDER BY category",
        (guild_id,),
    )


async def update_product(ex: Executor, product_id: int, **fields) -> None:
    unknown = set(fields) - _PRODUCT_FIELDS
    if unknown:
        raise ValueError(f"Campos desconhecidos: {unknown}")
    if not fields:
        return
    fields["updated_at"] = utcnow_iso()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    await ex.execute(
        f"UPDATE products SET {assignments} WHERE id = ?",
        (*fields.values(), product_id),
    )


async def set_stock(ex: Executor, product_id: int, stock: Optional[int]) -> None:
    """Define estoque absoluto (None = ilimitado). Não permite ficar abaixo do reservado."""
    row = await get_product(ex, product_id)
    if row is None:
        return
    reserved = int(row["reserved_qty"])
    if stock is not None and stock < reserved:
        stock = reserved
    await update_product(ex, product_id, stock=stock)


async def adjust_stock(ex: Executor, product_id: int, delta: int) -> Optional[Row]:
    """Soma/remove unidades (entrada/saída manual). Retorna o produto atualizado."""
    row = await get_product(ex, product_id)
    if row is None:
        return None
    if row["stock"] is None:
        return row  # ilimitado: nada a ajustar
    new_stock = max(int(row["stock"]) + delta, int(row["reserved_qty"]))
    await update_product(ex, product_id, stock=new_stock)
    return await get_product(ex, product_id)


# ------------------------------------------------- baixa atômica (compra carteira)
async def try_consume_stock(ex: Executor, product_id: int, qty: int = 1) -> bool:
    """Baixa estoque SOMENTE se houver disponível. Nunca negativar."""
    cur = await ex.execute(
        """
        UPDATE products
           SET stock = CASE WHEN stock IS NULL THEN NULL ELSE stock - ? END,
               updated_at = ?
         WHERE id = ? AND (stock IS NULL OR (stock - reserved_qty) >= ?)
        """,
        (qty, utcnow_iso(), product_id, qty),
    )
    return cur.rowcount > 0


# ------------------------------------------------------------- reservas (Stripe)
async def try_reserve_stock(ex: Executor, product_id: int, qty: int = 1) -> bool:
    """Reserva estoque para pedido aguardando pagamento Stripe."""
    cur = await ex.execute(
        """
        UPDATE products
           SET reserved_qty = reserved_qty + ?, updated_at = ?
         WHERE id = ? AND (stock IS NULL OR (stock - reserved_qty) >= ?)
        """,
        (qty, utcnow_iso(), product_id, qty),
    )
    return cur.rowcount > 0


async def create_reservation(
    ex: Executor, order_id: int, product_id: int, qty: int, expires_at: str
) -> int:
    cur = await ex.execute(
        "INSERT INTO stock_reservations (order_id, product_id, qty, status, expires_at, created_at)"
        " VALUES (?, ?, ?, 'reserved', ?, ?)",
        (order_id, product_id, qty, expires_at, utcnow_iso()),
    )
    return int(cur.lastrowid)


async def get_reservation_by_order(ex: Executor, order_id: int) -> Optional[Row]:
    return await ex.fetchone("SELECT * FROM stock_reservations WHERE order_id = ?", (order_id,))


async def consume_reservation(ex: Executor, reservation_id: int) -> bool:
    """Converte reserva em venda: libera a reserva e baixa o estoque uma única vez.

    Retorna False se não for possível (reserva já usada/liberada ou estoque
    inconsistente) — o chamador deve tratar como falha, nunca repetir.
    """
    res = await ex.fetchone(
        "SELECT * FROM stock_reservations WHERE id = ?", (reservation_id,)
    )
    if res is None or res["status"] != "reserved":
        return False
    product = await get_product(ex, res["product_id"])
    if product is None:
        return False
    qty = int(res["qty"])
    if product["stock"] is not None and int(product["stock"]) < qty:
        return False  # estoque foi reduzido manualmente abaixo da reserva
    await ex.execute(
        """
        UPDATE products
           SET reserved_qty = MAX(reserved_qty - ?, 0),
               stock = CASE WHEN stock IS NULL THEN NULL ELSE stock - ? END,
               updated_at = ?
         WHERE id = ?
        """,
        (qty, qty, utcnow_iso(), product["id"]),
    )
    await ex.execute(
        "UPDATE stock_reservations SET status = 'consumed' WHERE id = ?", (reservation_id,)
    )
    return True


async def release_reservation(ex: Executor, reservation_id: int) -> bool:
    """Libera reserva expirada/cancelada sem baixar estoque."""
    res = await ex.fetchone(
        "SELECT * FROM stock_reservations WHERE id = ?", (reservation_id,)
    )
    if res is None or res["status"] != "reserved":
        return False
    await ex.execute(
        "UPDATE products SET reserved_qty = MAX(reserved_qty - ?, 0), updated_at = ? WHERE id = ?",
        (int(res["qty"]), utcnow_iso(), res["product_id"]),
    )
    await ex.execute(
        "UPDATE stock_reservations SET status = 'released' WHERE id = ?", (reservation_id,)
    )
    return True


async def expired_reservations(ex: Executor, now_iso: str) -> list[Row]:
    return await ex.fetchall(
        "SELECT * FROM stock_reservations WHERE status = 'reserved' AND expires_at <= ?",
        (now_iso,),
    )

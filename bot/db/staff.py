"""Repositório de equipe (vendedores) e configurações por guild."""
from __future__ import annotations

from typing import Optional

from aiosqlite import Row

from .core import Executor, utcnow_iso


# ---------------------------------------------------------------------- vendedores
async def add_seller(ex: Executor, guild_id: int, user_id: int, added_by: int) -> bool:
    cur = await ex.execute(
        "INSERT OR IGNORE INTO sellers (guild_id, user_id, added_by, added_at) VALUES (?, ?, ?, ?)",
        (guild_id, user_id, added_by, utcnow_iso()),
    )
    return cur.rowcount > 0


async def remove_seller(ex: Executor, guild_id: int, user_id: int) -> bool:
    cur = await ex.execute(
        "DELETE FROM sellers WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
    )
    return cur.rowcount > 0


async def is_seller(ex: Executor, guild_id: int, user_id: int) -> bool:
    row = await ex.fetchone(
        "SELECT 1 FROM sellers WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
    )
    return row is not None


async def list_sellers(ex: Executor, guild_id: int) -> list[Row]:
    return await ex.fetchall(
        "SELECT * FROM sellers WHERE guild_id = ? ORDER BY added_at", (guild_id,)
    )


# ------------------------------------------------------------------ configurações
async def get_settings(ex: Executor, guild_id: int) -> Row:
    row = await ex.fetchone(
        "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
    )
    if row is not None:
        return row
    now = utcnow_iso()
    await ex.execute(
        "INSERT OR IGNORE INTO guild_settings (guild_id, owner_id, log_channel_id, created_at, updated_at)"
        " VALUES (?, NULL, NULL, ?, ?)",
        (guild_id, now, now),
    )
    return await ex.fetchone(  # type: ignore[return-value]
        "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
    )


async def set_log_channel(ex: Executor, guild_id: int, channel_id: Optional[int]) -> None:
    await get_settings(ex, guild_id)
    await ex.execute(
        "UPDATE guild_settings SET log_channel_id = ?, updated_at = ? WHERE guild_id = ?",
        (channel_id, utcnow_iso(), guild_id),
    )


async def set_owner(ex: Executor, guild_id: int, owner_id: Optional[int]) -> None:
    await get_settings(ex, guild_id)
    await ex.execute(
        "UPDATE guild_settings SET owner_id = ?, updated_at = ? WHERE guild_id = ?",
        (owner_id, utcnow_iso(), guild_id),
    )

"""Núcleo de acesso ao banco SQLite.

Garantias:
- WAL + foreign keys + busy_timeout para concorrência segura;
- Todas as escritas multi-passos usam ``Database.tx()`` (BEGIN IMMEDIATE),
  garantindo atomicidade (compra + débito + estoque + ledger em uma transação);
- Uma única conexão compartilhada serializada por lock de escrita, evitando
  condições de corrida entre cogs e o servidor de webhooks.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Optional, Sequence, Union

import aiosqlite


def utcnow_iso() -> str:
    """Timestamp UTC ISO-8601 (segundos) usado em todas as tabelas."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


Params = Union[Sequence[Any], Iterable[Any]]


class Executor:
    """Interface comum de execução SQL (conexão direta ou transação)."""

    _conn: aiosqlite.Connection

    async def execute(self, sql: str, params: Params = ()) -> aiosqlite.Cursor:
        return await self._conn.execute(sql, tuple(params))

    async def fetchone(self, sql: str, params: Params = ()) -> Optional[aiosqlite.Row]:
        cur = await self.execute(sql, params)
        return await cur.fetchone()

    async def fetchall(self, sql: str, params: Params = ()) -> list[aiosqlite.Row]:
        cur = await self.execute(sql, params)
        return await cur.fetchall()

    async def fetchval(self, sql: str, params: Params = ()) -> Any:
        row = await self.fetchone(sql, params)
        return row[0] if row is not None else None


class Transaction(Executor):
    """Transação aberta (BEGIN IMMEDIATE). Commit/rollback em ``Database.tx``."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn


class Database(Executor):
    """Wrapper assíncrono do SQLite com transações atômicas."""

    def __init__(self, path: Union[str, Path]) -> None:
        self.path = Path(path)
        self._conn: Optional[aiosqlite.Connection] = None
        self._write_lock = asyncio.Lock()

    # ------------------------------------------------------------- ciclo de vida
    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Banco de dados não conectado (chame await db.connect()).")
        return self._conn

    # ------------------------------------------------------------------ escritas
    async def execute(self, sql: str, params: Params = ()) -> aiosqlite.Cursor:
        async with self._write_lock:
            return await self._conn.execute(sql, tuple(params))

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[Transaction]:
        """Transação atômica: commit no sucesso, rollback em qualquer exceção."""
        async with self._write_lock:
            await self._conn.execute("BEGIN IMMEDIATE")
            txn = Transaction(self._conn)
            try:
                yield txn
            except BaseException:
                await self._conn.execute("ROLLBACK")
                raise
            else:
                await self._conn.execute("COMMIT")


def row_dict(row: Optional[aiosqlite.Row]) -> Optional[dict]:
    return dict(row) if row is not None else None

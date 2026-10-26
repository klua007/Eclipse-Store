"""Migrações versionadas do esquema do banco.

Cada migração é uma lista de statements aplicados dentro de UMA transação
(atômica). A versão é registrada em ``schema_migrations``.
"""
from __future__ import annotations

import logging
from typing import List, Tuple

from .core import Database

log = logging.getLogger("eclipse.db")

MIGRATIONS: List[Tuple[int, str, List[str]]] = [
    (
        1,
        "esquema inicial: carteiras, ledger, topups, produtos, pedidos, reservas, equipe, auditoria",
        [
            # ------------------------------------------------------------ carteira
            """
            CREATE TABLE wallets (
                user_id       INTEGER PRIMARY KEY,          -- ID numérico do Discord
                currency      TEXT    NOT NULL,
                balance_cents INTEGER NOT NULL DEFAULT 0,    -- saldo em centavos inteiros
                created_at    TEXT    NOT NULL,
                updated_at    TEXT    NOT NULL
            )
            """,
            # ------------------------------------------------- ledger (financeiro)
            """
            CREATE TABLE ledger_transactions (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_uuid                TEXT    NOT NULL UNIQUE,
                user_id                INTEGER NOT NULL,     -- ID Discord afetado
                guild_id               INTEGER,
                amount_cents           INTEGER NOT NULL,     -- variação (com sinal) na carteira
                operation_amount_cents INTEGER NOT NULL,     -- magnitude da operação
                currency               TEXT    NOT NULL,
                type                   TEXT    NOT NULL,     -- topup|purchase|refund|chargeback|admin_adjust
                status                 TEXT    NOT NULL,     -- pending|completed|failed|cancelled|reversed
                stripe_ref             TEXT,                 -- session/payment_intent/charge/dispute
                reference_type         TEXT,                 -- topup|order|manual
                reference_id           INTEGER,
                reason                 TEXT,
                actor_id               INTEGER,              -- quem originou (NULL = Stripe/sistema)
                created_at             TEXT    NOT NULL,
                completed_at           TEXT
            )
            """,
            "CREATE INDEX idx_ledger_user ON ledger_transactions (user_id, id DESC)",
            "CREATE INDEX idx_ledger_stripe ON ledger_transactions (stripe_ref)",
            # ---------------------------------------------------------------- topups
            """
            CREATE TABLE topups (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id                  INTEGER NOT NULL,
                amount_cents             INTEGER NOT NULL CHECK (amount_cents > 0),
                currency                 TEXT    NOT NULL,
                status                   TEXT    NOT NULL DEFAULT 'pending',
                stripe_session_id        TEXT    UNIQUE,
                stripe_payment_intent_id TEXT,
                created_at               TEXT    NOT NULL,
                paid_at                  TEXT
            )
            """,
            # -------------------------------------------------------------- produtos
            """
            CREATE TABLE products (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id            INTEGER NOT NULL,
                name                TEXT    NOT NULL,
                description         TEXT    NOT NULL DEFAULT '',
                category            TEXT    NOT NULL DEFAULT 'Geral',
                price_cents         INTEGER NOT NULL CHECK (price_cents >= 0),
                currency            TEXT    NOT NULL,
                active              INTEGER NOT NULL DEFAULT 1,
                delivery_type       TEXT    NOT NULL,         -- text|file|code|link|custom
                delivery_payload    TEXT,
                delivery_file_name  TEXT,
                delivery_file_path  TEXT,
                stock               INTEGER,                  -- NULL = ilimitado
                reserved_qty        INTEGER NOT NULL DEFAULT 0,
                low_stock_threshold INTEGER,
                seller_id           INTEGER NOT NULL,         -- vendedor responsável
                created_at          TEXT    NOT NULL,
                updated_at          TEXT    NOT NULL,
                UNIQUE (guild_id, name)
            )
            """,
            "CREATE INDEX idx_products_guild ON products (guild_id, active)",
            # ---------------------------------------------------------------- pedidos
            """
            CREATE TABLE orders (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id                 INTEGER NOT NULL,
                buyer_id                 INTEGER NOT NULL,
                product_id               INTEGER NOT NULL,
                product_name             TEXT    NOT NULL,
                price_cents              INTEGER NOT NULL,
                currency                 TEXT    NOT NULL,
                payment_method           TEXT    NOT NULL,     -- wallet|stripe
                status                   TEXT    NOT NULL,     -- awaiting_payment|paid|expired|cancelled|failed_out_of_stock|refunded|disputed
                delivery_status          TEXT    NOT NULL DEFAULT 'none',  -- none|pending|delivered|failed|dm_blocked
                delivered_via            TEXT,                 -- ephemeral|dm|ephemeral+dm
                delivered_at             TEXT,
                delivery_type            TEXT    NOT NULL,
                delivery_payload         TEXT,
                delivery_file_name       TEXT,
                delivery_file_path       TEXT,
                stripe_session_id        TEXT    UNIQUE,
                stripe_payment_intent_id TEXT,
                created_at               TEXT    NOT NULL,
                paid_at                  TEXT
            )
            """,
            "CREATE INDEX idx_orders_guild ON orders (guild_id, id DESC)",
            "CREATE INDEX idx_orders_buyer ON orders (buyer_id, id DESC)",
            # ------------------------------------------------- reservas de estoque
            """
            CREATE TABLE stock_reservations (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id   INTEGER NOT NULL UNIQUE REFERENCES orders (id),
                product_id INTEGER NOT NULL REFERENCES products (id),
                qty        INTEGER NOT NULL DEFAULT 1,
                status     TEXT    NOT NULL DEFAULT 'reserved',  -- reserved|consumed|released
                expires_at TEXT    NOT NULL,
                created_at TEXT    NOT NULL
            )
            """,
            # ------------------------------------------------------------------ equipe
            """
            CREATE TABLE sellers (
                guild_id INTEGER NOT NULL,
                user_id  INTEGER NOT NULL,
                added_by INTEGER NOT NULL,
                added_at TEXT    NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
            """
            CREATE TABLE guild_settings (
                guild_id       INTEGER PRIMARY KEY,
                owner_id       INTEGER,
                log_channel_id INTEGER,
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL
            )
            """,
            # -------------------------------------------------- eventos Stripe (idempotência)
            """
            CREATE TABLE stripe_events (
                event_id    TEXT PRIMARY KEY,
                type        TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'received',  -- received|processed|ignored|error
                received_at TEXT NOT NULL,
                processed_at TEXT
            )
            """,
            # ---------------------------------------------------------------- auditoria
            """
            CREATE TABLE audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER,
                actor_id    INTEGER,
                action      TEXT NOT NULL,
                target_type TEXT,
                target_id   TEXT,
                details     TEXT,                              -- JSON (sem segredos)
                created_at  TEXT NOT NULL
            )
            """,
            "CREATE INDEX idx_audit_guild ON audit_log (guild_id, id DESC)",
        ],
    ),
]


async def apply_migrations(db: Database) -> int:
    """Aplica migrações pendentes. Retorna quantas foram aplicadas."""
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied = 0
    for version, name, statements in MIGRATIONS:
        exists = await db.fetchval("SELECT 1 FROM schema_migrations WHERE version = ?", (version,))
        if exists:
            continue
        log.info("Aplicando migração %s: %s", version, name)
        async with db.tx() as txn:
            for statement in statements:
                await txn.execute(statement)
            from .core import utcnow_iso

            await txn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, utcnow_iso()),
            )
        applied += 1
    return applied

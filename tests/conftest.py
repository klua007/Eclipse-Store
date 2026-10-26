"""Fixtures de teste: banco temporário com migrações e Stripe fake (sem rede)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.config import Config
from bot.db.core import Database
from bot.db.migrations import apply_migrations


class FakeStripe:
    """Dublê do StripeService: gera sessões determinísticas, sem chamadas de rede."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.expired: list[str] = []

    async def create_topup_session(self, *, topup_id, user_id, amount_cents, currency):
        session_id = f"cs_test_topup_{topup_id}"
        self.created.append(session_id)
        return SimpleNamespace(id=session_id, url=f"https://checkout.test/{session_id}")

    async def create_order_session(self, *, order_id, product_name, amount_cents, currency, guild_id, user_id, product_id):
        session_id = f"cs_test_order_{order_id}"
        self.created.append(session_id)
        return SimpleNamespace(id=session_id, url=f"https://checkout.test/{session_id}")

    async def expire_session(self, session_id):
        if session_id:
            self.expired.append(session_id)

    def construct_event(self, payload, sig):
        """Verificação de assinatura REAL do SDK (mesma usada em produção)."""
        import stripe

        return stripe.Webhook.construct_event(payload, sig, "whsec_fake")


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    await apply_migrations(database)
    yield database
    await database.close()


@pytest.fixture
def config(tmp_path):
    return Config(
        discord_token="token-fake",
        stripe_secret_key="sk_test_fake",
        stripe_webhook_secret="whsec_fake",
        owner_ids=[111],
        admin_ids=[222],
        database_path=tmp_path / "unused.db",
        deliverables_dir=tmp_path / "deliverables",
        backup_dir=tmp_path / "backups",
        currency="BRL",
        webhook_port=8999,
        reservation_ttl_minutes=30,
    )


@pytest.fixture
def fake_stripe():
    return FakeStripe()


def make_session(kind: str, session_id: str, paid: bool = True, **meta):
    metadata = {"kind": kind, **{k: str(v) for k, v in meta.items()}}
    return SimpleNamespace(
        id=session_id,
        payment_status="paid" if paid else "unpaid",
        payment_intent=f"pi_{session_id}",
        metadata=metadata,
    )

"""Testes dos fluxos críticos: carteira, compra atômica, Stripe, estornos, auditoria."""
from __future__ import annotations

import pytest
from conftest import make_session

from bot.db import audit, catalog, orders as orders_repo, staff, wallets
from bot.services import purchase
from bot.services.money import MoneyError, format_cents, parse_cents

GUILD = 123456789012345678
BUYER = 424242424242424242
SELLER = 555555555555555555


async def _product(db, price=1000, stock=None, **kw):
    async with db.tx() as tx:
        pid = await catalog.create_product(
            tx,
            guild_id=GUILD,
            name=kw.pop("name", "Chave de licença Teste"),
            description="Produto de teste",
            category="Testes",
            price_cents=price,
            currency="BRL",
            delivery_type="code",
            delivery_payload="KEY-1234-ABCD",
            stock=stock,
            low_stock_threshold=kw.pop("low", None),
            seller_id=SELLER,
        )
    return await catalog.get_product(db, pid)


# --------------------------------------------------------------------- dinheiro
def test_money_parse_and_format():
    assert parse_cents("10,50") == 1050
    assert parse_cents("10.50") == 1050
    assert parse_cents("10") == 1000
    assert parse_cents("1.234,56") == 123456
    assert format_cents(1050) == "R$ 10,50"
    assert format_cents(123456) == "R$ 1.234,56"
    assert format_cents(-500) == "-R$ 5,00"
    with pytest.raises(MoneyError):
        parse_cents("abc")
    with pytest.raises(MoneyError):
        parse_cents("0,50")  # abaixo do mínimo


# ------------------------------------------------------------------ recargas
async def test_topup_flow_is_idempotent(db, config, fake_stripe):
    topup, session = await purchase.start_topup(db, fake_stripe, config, BUYER, 2500)
    assert topup["status"] == "pending"

    paid_session = make_session("topup", session.id, topup_id=topup["id"], user_id=BUYER)
    credited = await purchase.complete_topup(db, config, paid_session)
    assert credited is not None

    wallet = await wallets.get_wallet(db, BUYER)
    assert wallet["balance_cents"] == 2500

    # segundo envio do MESMO evento: não credita de novo
    again = await purchase.complete_topup(db, config, paid_session)
    assert again is None
    wallet = await wallets.get_wallet(db, BUYER)
    assert wallet["balance_cents"] == 2500

    ledger = await wallets.list_ledger(db, BUYER, limit=10)
    completed = [r for r in ledger if r["type"] == "topup" and r["status"] == "completed"]
    assert len(completed) == 1


async def test_topup_not_confirmed_without_paid_status(db, config, fake_stripe):
    topup, session = await purchase.start_topup(db, fake_stripe, config, BUYER, 1500)
    unpaid = make_session("topup", session.id, paid=False, topup_id=topup["id"])
    assert await purchase.complete_topup(db, config, unpaid) is None
    wallet = await wallets.get_wallet(db, BUYER)
    assert wallet is None or wallet["balance_cents"] == 0


# ------------------------------------------------------------- compra carteira
async def test_wallet_purchase_atomic(db, config):
    await purchase.admin_adjust(
        db, config, actor_id=111, target_user_id=BUYER, delta_cents=5000,
        reason="seed de teste", guild_id=GUILD,
    )
    product = await _product(db, price=2000, stock=3)
    result = await purchase.purchase_with_wallet(db, config, GUILD, BUYER, product["id"])
    assert result.new_balance == 3000
    assert result.order["status"] == "paid"

    refreshed = await catalog.get_product(db, product["id"])
    assert refreshed["stock"] == 2

    ledger = await wallets.list_ledger(db, BUYER, limit=5)
    types = {(r["type"], r["status"]) for r in ledger}
    assert ("purchase", "completed") in types


async def test_wallet_purchase_insufficient_funds(db, config):
    product = await _product(db, price=2000)
    with pytest.raises(purchase.InsufficientFunds):
        await purchase.purchase_with_wallet(db, config, GUILD, BUYER, product["id"])
    # nada mudou
    wallet = await wallets.get_wallet(db, BUYER)
    assert wallet is None or wallet["balance_cents"] == 0


async def test_last_item_cannot_be_sold_twice(db, config):
    await purchase.admin_adjust(
        db, config, actor_id=111, target_user_id=BUYER, delta_cents=10000,
        reason="seed", guild_id=GUILD,
    )
    product = await _product(db, price=1000, stock=1)
    await purchase.purchase_with_wallet(db, config, GUILD, BUYER, product["id"])
    with pytest.raises(purchase.OutOfStock):
        await purchase.purchase_with_wallet(db, config, GUILD, BUYER, product["id"])
    refreshed = await catalog.get_product(db, product["id"])
    assert refreshed["stock"] == 0  # nunca negativo


# ------------------------------------------------------------- compra Stripe
async def test_stripe_order_lifecycle_idempotent(db, config, fake_stripe):
    product = await _product(db, price=3000, stock=2)
    order, session = await purchase.start_stripe_order(
        db, fake_stripe, config, GUILD, BUYER, product["id"]
    )
    assert order["status"] == "awaiting_payment"
    reserved = await catalog.get_product(db, product["id"])
    assert reserved["reserved_qty"] == 1
    assert catalog.available_stock(reserved) == 1

    paid = make_session("order", session.id, order_id=order["id"])
    confirmed = await purchase.complete_order(db, config, paid)
    assert confirmed is not None and confirmed["status"] == "paid"

    after = await catalog.get_product(db, product["id"])
    assert after["stock"] == 1 and after["reserved_qty"] == 0

    # webhook reentregue: idempotente
    assert await purchase.complete_order(db, config, paid) is None
    after2 = await catalog.get_product(db, product["id"])
    assert after2["stock"] == 1  # estoque baixado UMA única vez


async def test_stripe_reservation_blocks_last_item(db, config, fake_stripe):
    product = await _product(db, price=1000, stock=1)
    await purchase.start_stripe_order(db, fake_stripe, config, GUILD, BUYER, product["id"])
    with pytest.raises(purchase.OutOfStock):
        await purchase.start_stripe_order(db, fake_stripe, config, GUILD, SELLER, product["id"])


async def test_expired_reservation_releases_stock(db, config, fake_stripe):
    product = await _product(db, price=1000, stock=1)
    order, session = await purchase.start_stripe_order(
        db, fake_stripe, config, GUILD, BUYER, product["id"]
    )
    # força expiração
    await db.execute(
        "UPDATE stock_reservations SET expires_at = ? WHERE order_id = ?",
        ("2000-01-01T00:00:00+00:00", order["id"]),
    )
    cancelled = await purchase.sweep_expired_reservations(db, config)
    assert cancelled == [order["id"]]
    refreshed = await catalog.get_product(db, product["id"])
    assert refreshed["reserved_qty"] == 0 and catalog.available_stock(refreshed) == 1
    final = await orders_repo.get_order(db, order["id"])
    assert final["status"] == "expired"


# ------------------------------------------------------------------ estornos
async def test_refund_adjusts_wallet_and_ledger(db, config, fake_stripe):
    topup, session = await purchase.start_topup(db, fake_stripe, config, BUYER, 4000)
    paid = make_session("topup", session.id, topup_id=topup["id"])
    await purchase.complete_topup(db, config, paid)

    charge = type("Charge", (), {
        "id": "ch_123",
        "payment_intent": paid.payment_intent,
        "amount_refunded": 4000,
    })()
    ref = await purchase.apply_charge_refund(db, config, charge)
    assert ref == f"topup:{topup['id']}"

    wallet = await wallets.get_wallet(db, BUYER)
    assert wallet["balance_cents"] == 0
    refreshed = await wallets.get_topup(db, topup["id"])
    assert refreshed["status"] == "refunded"
    ledger = await wallets.list_ledger(db, BUYER, limit=10)
    assert any(r["type"] == "refund" and r["status"] == "completed" for r in ledger)


async def test_dispute_recorded(db, config, fake_stripe):
    topup, session = await purchase.start_topup(db, fake_stripe, config, BUYER, 2000)
    paid = make_session("topup", session.id, topup_id=topup["id"])
    await purchase.complete_topup(db, config, paid)
    dispute = type("Dispute", (), {
        "id": "dp_1", "payment_intent": paid.payment_intent, "amount": 2000, "status": "needs_response",
    })()
    ref = await purchase.apply_dispute(db, config, dispute, closed=False)
    assert ref is not None
    refreshed = await wallets.get_topup(db, topup["id"])
    assert refreshed["status"] == "disputed"


# --------------------------------------------------------------- ajustes/admin
async def test_admin_adjust_requires_reason(db, config):
    with pytest.raises(purchase.PurchaseError):
        await purchase.admin_adjust(
            db, config, actor_id=111, target_user_id=BUYER, delta_cents=100,
            reason="   ", guild_id=GUILD,
        )


async def test_admin_adjust_audited(db, config):
    await purchase.admin_adjust(
        db, config, actor_id=111, target_user_id=BUYER, delta_cents=-0 + 700,
        reason="compensação por atraso", guild_id=GUILD,
    )
    rows = await audit.recent(db, GUILD, limit=5)
    assert any(r["action"] == "wallet.admin_adjust" for r in rows)
    ledger = await wallets.list_ledger(db, BUYER, limit=5)
    entry = [r for r in ledger if r["type"] == "admin_adjust"][0]
    assert entry["reason"] == "compensação por atraso"
    assert entry["actor_id"] == 111


# ------------------------------------------------------------ idempotência eventos
async def test_stripe_event_claim_is_single_use(db):
    async with db.tx() as tx:
        assert await audit.claim_event(tx, "evt_1", "checkout.session.completed") is True
    async with db.tx() as tx:
        assert await audit.claim_event(tx, "evt_1", "checkout.session.completed") is False


# ------------------------------------------------------------------ equipe
async def test_sellers_table(db):
    async with db.tx() as tx:
        assert await staff.add_seller(tx, GUILD, SELLER, 111) is True
        assert await staff.add_seller(tx, GUILD, SELLER, 111) is False  # duplicado
        assert await staff.is_seller(tx, GUILD, SELLER) is True
        assert await staff.remove_seller(tx, GUILD, SELLER) is True
        assert await staff.is_seller(tx, GUILD, SELLER) is False

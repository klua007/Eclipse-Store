"""Teste de ponta a ponta do servidor de webhooks com assinatura Stripe real."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import aiohttp
import pytest

from bot.app import EclipseApp
from bot.db import wallets
from bot.services import purchase
from bot.services.notifier import Notifier
from bot.web.webhook_server import WebhookServer

from conftest import FakeStripe

BUYER = 777777777777777777
PORT = 8999


def _sign(payload: bytes, secret: str) -> str:
    timestamp = int(time.time())
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


@pytest.fixture
async def server(db, config):
    fake = FakeStripe()
    app = EclipseApp(
        config=config,
        db=db,
        stripe=fake,  # type: ignore[arg-type]
        perms=None,  # type: ignore[arg-type]
        notifier=Notifier(None, db, config),  # type: ignore[arg-type]
        bot=None,
    )
    srv = WebhookServer(app)
    await srv.start()
    yield app, srv
    await srv.stop()


async def _post_event(client, config, event: dict, sign_with: str | None = "whsec_fake"):
    payload = json.dumps(event).encode()
    headers = {}
    if sign_with is not None:
        headers["Stripe-Signature"] = _sign(payload, sign_with)
    url = f"http://127.0.0.1:{PORT}/stripe/webhook"
    async with client.post(url, data=payload, headers=headers) as resp:
        body = await resp.text()
        return resp.status, body


async def test_webhook_topup_end_to_end(server, db, config):
    app, srv = server
    topup, session = await purchase.start_topup(db, app.stripe, config, BUYER, 3300)

    event = {
        "id": "evt_test_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": session.id,
                "object": "checkout.session",
                "payment_status": "paid",
                "payment_intent": "pi_evt1",
                "metadata": {"kind": "topup", "topup_id": str(topup["id"]), "user_id": str(BUYER)},
            }
        },
    }
    async with aiohttp.ClientSession() as client:
        status, body = await _post_event(client, config, event)
        assert status == 200 and "ok" in body
        wallet = await wallets.get_wallet(db, BUYER)
        assert wallet["balance_cents"] == 3300

        # reenvio do mesmo evento: idempotente (não credita duas vezes)
        status2, body2 = await _post_event(client, config, event)
        assert status2 == 200 and "duplicate" in body2
        wallet = await wallets.get_wallet(db, BUYER)
        assert wallet["balance_cents"] == 3300

        # assinatura inválida: rejeitada
        status3, _ = await _post_event(client, config, event, sign_with="whsec_errado")
        assert status3 == 400

        # sem assinatura: rejeitada
        status4, _ = await _post_event(client, config, event, sign_with=None)
        assert status4 == 400

        # healthcheck
        async with client.get(f"http://127.0.0.1:{PORT}/healthz") as resp:
            assert resp.status == 200


async def test_webhook_redirect_does_not_credit(server, db, config):
    """O redirect (success_url) nunca credita: só o webhook com payment_status=paid."""
    app, srv = server
    topup, session = await purchase.start_topup(db, app.stripe, config, BUYER, 900)
    event = {
        "id": "evt_test_unpaid",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": session.id,
                "object": "checkout.session",
                "payment_status": "unpaid",
                "payment_intent": None,
                "metadata": {"kind": "topup", "topup_id": str(topup["id"])},
            }
        },
    }
    async with aiohttp.ClientSession() as client:
        status, _ = await _post_event(client, config, event)
        assert status == 200
    wallet = await wallets.get_wallet(db, BUYER)
    assert wallet is None or wallet["balance_cents"] == 0

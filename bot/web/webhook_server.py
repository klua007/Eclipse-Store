"""Servidor HTTP (aiohttp) que recebe os webhooks do Stripe.

A confirmação de pagamento acontece SOMENTE aqui, com assinatura validada
(``Stripe-Signature`` + ``STRIPE_WEBHOOK_SECRET``). O redirect do cliente
(página de sucesso) nunca credita saldo nem libera pedido.

Rotas:
- POST /stripe/webhook  -> eventos do Stripe
- GET  /healthz         -> verificação de vida
- GET  /sucesso         -> página informativa pós-checkout
- GET  /cancelado       -> página informativa de cancelamento
"""
from __future__ import annotations

import logging

from aiohttp import web

from ..app import EclipseApp
from ..db import audit, orders as orders_repo
from ..services import delivery, purchase
from ..services.stripe_service import sget, smeta

log = logging.getLogger("eclipse.webhook")

_PAGE = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<title>Eclipse Store</title>
<style>body{{background:#0b0b0f;color:#e5e7eb;font-family:system-ui,sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
.card{{background:#101014;border:1px solid #8b5cf6;border-radius:16px;padding:32px 40px;
max-width:520px;text-align:center;box-shadow:0 0 40px rgba(139,92,246,.35)}}
h1{{color:#8b5cf6;margin:0 0 8px}}p{{color:#22d3ee}}</style></head>
<body><div class="card"><h1>{titulo}</h1><p>{texto}</p></div></body></html>"""


class WebhookServer:
    def __init__(self, ec: EclipseApp) -> None:
        self.ec = ec
        self._runner: web.AppRunner | None = None

    # ------------------------------------------------------------------ ciclo
    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/stripe/webhook", self.handle_webhook)
        app.router.add_get("/healthz", self.handle_health)
        app.router.add_get("/sucesso", self.handle_success)
        app.router.add_get("/cancelado", self.handle_cancel)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(
            self._runner, self.ec.config.webhook_host, self.ec.config.webhook_port
        )
        await site.start()
        log.info(
            "Webhook Stripe ouvindo em http://%s:%s/stripe/webhook",
            self.ec.config.webhook_host,
            self.ec.config.webhook_port,
        )

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    # ------------------------------------------------------------------ rotas
    async def handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "bot": "eclipse-store"})

    async def handle_success(self, request: web.Request) -> web.Response:
        return web.Response(
            content_type="text/html",
            text=_PAGE.format(
                titulo="Pagamento recebido!",
                texto="Se o pagamento for confirmado pelo Stripe, seu saldo ou produto "
                "será liberado automaticamente em instantes, direto no Discord. "
                "Você pode fechar esta página.",
            ),
        )

    async def handle_cancel(self, request: web.Request) -> web.Response:
        return web.Response(
            content_type="text/html",
            text=_PAGE.format(
                titulo="Pagamento cancelado",
                texto="Nenhuma cobrança foi concluída e nenhuma reserva foi mantida "
                "por muito tempo. Tente novamente quando quiser.",
            ),
        )

    async def handle_webhook(self, request: web.Request) -> web.Response:
        payload = await request.read()
        signature = request.headers.get("Stripe-Signature")
        try:
            event = self.ec.stripe.construct_event(payload, signature)
        except Exception as exc:  # SignatureVerificationError / ValueError
            log.warning("Webhook rejeitado (assinatura inválida): %s", exc)
            return web.Response(status=400, text="assinatura inválida")

        event_id = sget(event, "id")
        event_type = sget(event, "type", "unknown")
        db = self.ec.db

        # Idempotência: cada event id é processado uma única vez.
        async with db.tx() as tx:
            claimed = await audit.claim_event(tx, event_id, event_type)
        if not claimed:
            log.info("Evento duplicado ignorado: %s (%s)", event_id, event_type)
            return web.json_response({"status": "duplicate"})

        try:
            handled = await self._dispatch(event_type, sget(sget(event, "data"), "object"))
        except Exception:
            log.exception("Erro ao processar evento %s (%s)", event_id, event_type)
            async with db.tx() as tx:
                await audit.mark_event(tx, event_id, "error")
            # 500 faz o Stripe reenviar; a idempotência evita double-credit
            return web.Response(status=500, text="erro interno")

        async with db.tx() as tx:
            await audit.mark_event(tx, event_id, "processed" if handled else "ignored")
        return web.json_response({"status": "ok", "handled": bool(handled)})

    # -------------------------------------------------------------- dispatch
    async def _dispatch(self, event_type: str, obj) -> bool:
        ec = self.ec
        if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            if sget(obj, "payment_status") != "paid":
                return False  # pagamento assíncrono ainda pendente
            kind = smeta(obj, "kind")
            if kind == "topup":
                topup = await purchase.complete_topup(ec.db, ec.config, obj)
                if topup is None:
                    return False
                await ec.notifier.payment_confirmed(
                    None,
                    "Recarga de carteira",
                    int(topup["user_id"]),
                    int(topup["amount_cents"]),
                    topup["currency"],
                    sget(obj, "payment_intent") or sget(obj, "id"),
                )
                return True
            if kind == "order":
                order = await purchase.complete_order(ec.db, ec.config, obj)
                if order is None:
                    return False
                await ec.notifier.payment_confirmed(
                    int(order["guild_id"]),
                    f"Compra (pedido #{order['id']})",
                    int(order["buyer_id"]),
                    int(order["price_cents"]),
                    order["currency"],
                    sget(obj, "payment_intent") or sget(obj, "id"),
                )
                if order["status"] == "paid":
                    ok, how = await delivery.deliver_background(ec.bot, ec.db, order)
                    await ec.notifier.log(
                        int(order["guild_id"]),
                        "📦 Entrega de pedido pago",
                        [
                            ("Pedido", f"#{order['id']} — {order['product_name']}"),
                            ("Cliente", f"<@{order['buyer_id']}>"),
                            ("Canal", "DM" if ok else "DM bloqueada — usar /meus-pedidos"),
                        ],
                    )
                    await purchase.maybe_low_stock_alert(
                        ec.db, ec.notifier, int(order["guild_id"]), int(order["product_id"])
                    )
                return True
            return False

        if event_type in ("checkout.session.expired", "checkout.session.async_payment_failed"):
            session_id = sget(obj, "id")
            order = await orders_repo.get_order_by_session(ec.db, session_id)
            if order is None or order["status"] != "awaiting_payment":
                return False
            status = "expired" if event_type == "checkout.session.expired" else "cancelled"
            await purchase.cancel_order(
                ec.db, ec.config, int(order["id"]), status, f"evento Stripe {event_type}"
            )
            await ec.notifier.log(
                int(order["guild_id"]),
                "🧾 Pedido cancelado/expirado",
                [
                    ("Pedido", f"#{order['id']} — {order['product_name']}"),
                    ("Motivo", event_type),
                    ("Estoque", "reserva liberada"),
                ],
            )
            return True

        if event_type == "charge.refunded":
            ref = await purchase.apply_charge_refund(ec.db, ec.config, obj)
            if ref is None:
                return False
            guild_id = None
            if ref.startswith("order:"):
                order = await orders_repo.get_order(ec.db, int(ref.split(":", 1)[1]))
                guild_id = int(order["guild_id"]) if order else None
            await ec.notifier.log(
                guild_id,
                "↩️ Estorno registrado",
                [
                    ("Referência", ref),
                    ("Charge", sget(obj, "id") or "-"),
                    ("Valor estornado", str(sget(obj, "amount_refunded"))),
                ],
            )
            return True

        if event_type in ("charge.dispute.created", "charge.dispute.closed"):
            ref = await purchase.apply_dispute(
                ec.db, ec.config, obj, closed=(event_type == "charge.dispute.closed")
            )
            if ref is None:
                return False
            await ec.notifier.log(
                None,
                "⚠️ Disputa (chargeback) registrada",
                [
                    ("Referência", ref),
                    ("Evento", event_type),
                    ("Status Stripe", sget(obj, "status") or "-"),
                ],
            )
            return True

        log.info("Evento Stripe não tratado: %s", event_type)
        return False

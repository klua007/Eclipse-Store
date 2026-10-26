"""Casos de uso de compra, recarga, reembolso e ajustes.

Todas as mutações financeiras acontecem dentro de UMA transação de banco
(``db.tx()``), garantindo atomicidade entre: carteira, ledger, estoque,
reservas e pedido. Eventos do Stripe são idempotentes (stripe_events +
guardas de status).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from aiosqlite import Row

from ..config import Config
from ..db import audit, catalog, orders as orders_repo, wallets
from ..db.core import Database, utcnow_iso
from .stripe_service import StripeService, sget, smeta

log = logging.getLogger("eclipse.purchase")


class PurchaseError(Exception):
    """Erro de negócio com mensagem segura para exibir ao usuário."""


class InsufficientFunds(PurchaseError):
    pass


class ProductUnavailable(PurchaseError):
    pass


class OutOfStock(ProductUnavailable):
    pass


@dataclass
class WalletPurchase:
    order: Row
    new_balance: int


def _ttl_expiry(config: Config) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(minutes=config.reservation_ttl_minutes)
    ).isoformat(timespec="seconds")


# ====================================================================== recargas
async def start_topup(
    db: Database, stripe_svc: StripeService, config: Config, user_id: int, amount_cents: int
) -> tuple[Row, Any]:
    """Cria recarga pendente + ledger pendente + Checkout Session do Stripe."""
    async with db.tx() as tx:
        topup_id = await wallets.create_topup(tx, user_id, amount_cents, config.currency)
        await wallets.add_ledger(
            tx,
            user_id=user_id,
            guild_id=None,
            amount_cents=amount_cents,
            operation_amount_cents=amount_cents,
            currency=config.currency,
            type="topup",
            status="pending",
            reference_type="topup",
            reference_id=topup_id,
            reason="Recarga via Stripe Checkout",
        )
    session = await stripe_svc.create_topup_session(
        topup_id=topup_id, user_id=user_id, amount_cents=amount_cents, currency=config.currency
    )
    async with db.tx() as tx:
        await wallets.attach_topup_session(tx, topup_id, sget(session, "id"))
        topup = await wallets.get_topup(tx, topup_id)
        # guarda o ledger id no topup? não: ledger referencia topup via reference_id
    return topup, session


async def complete_topup(db: Database, config: Config, session: Any) -> Optional[Row]:
    """Crédito de recarga confirmado pelo WEBHOOK (nunca pelo redirect).

    Idempotente: eventos repetidos não creditam duas vezes.
    Retorna a topup creditada ou None se nada a fazer.
    """
    session_id = sget(session, "id")
    if sget(session, "payment_status") != "paid":
        log.info("checkout.session sem payment_status=paid (%s); ignorando.", session_id)
        return None

    async with db.tx() as tx:
        topup = await wallets.get_topup_by_session(tx, session_id)
        if topup is None:
            log.warning("Webhook de topup sem recarga local: session=%s", session_id)
            return None
        changed = await wallets.mark_topup_paid(
            tx, int(topup["id"]), sget(session, "payment_intent")
        )
        if not changed:  # já creditada em evento anterior
            return None
        ledger = await tx.fetchone(
            "SELECT * FROM ledger_transactions WHERE reference_type = 'topup' AND reference_id = ?",
            (topup["id"],),
        )
        if ledger is not None:
            await wallets.complete_ledger(
                tx, int(ledger["id"]), stripe_ref=sget(session, "payment_intent")
            )
        else:  # defesa extra: ledger ausente ainda assim registra a operação
            await wallets.add_ledger(
                tx,
                user_id=int(topup["user_id"]),
                guild_id=None,
                amount_cents=int(topup["amount_cents"]),
                operation_amount_cents=int(topup["amount_cents"]),
                currency=topup["currency"],
                type="topup",
                status="completed",
                stripe_ref=sget(session, "payment_intent"),
                reference_type="topup",
                reference_id=int(topup["id"]),
                reason="Recarga via Stripe Checkout",
            )
        new_balance = await wallets.apply_delta(
            tx, int(topup["user_id"]), int(topup["amount_cents"]), topup["currency"]
        )
        await audit.record(
            tx,
            guild_id=None,
            actor_id=None,
            action="topup.paid",
            target_type="topup",
            target_id=str(topup["id"]),
            details={
                "user_id": int(topup["user_id"]),
                "amount_cents": int(topup["amount_cents"]),
                "currency": topup["currency"],
                "new_balance_cents": new_balance,
                "stripe_session": session_id,
            },
        )
        return await wallets.get_topup(tx, int(topup["id"]))


# ================================================================= compra carteira
async def purchase_with_wallet(
    db: Database, config: Config, guild_id: int, buyer_id: int, product_id: int
) -> WalletPurchase:
    """Compra atômica com saldo: débito + baixa de estoque + ledger + pedido."""
    async with db.tx() as tx:
        product = await catalog.get_product(tx, product_id)
        if product is None or int(product["guild_id"]) != guild_id:
            raise ProductUnavailable("Produto não encontrado nesta loja.")
        if not int(product["active"]):
            raise ProductUnavailable("Este produto não está ativo no momento.")
        price = int(product["price_cents"])

        wallet = await wallets.get_or_create_wallet(tx, buyer_id, product["currency"])
        if int(wallet["balance_cents"]) < price:
            raise InsufficientFunds(
                "Saldo insuficiente. Use /carteira adicionar para recarregar."
            )

        if not await catalog.try_consume_stock(tx, product_id, 1):
            raise OutOfStock("Estoque esgotado para este produto.")

        order_id = await orders_repo.create_order(
            tx,
            guild_id=guild_id,
            buyer_id=buyer_id,
            product=product,
            payment_method="wallet",
            status="awaiting_payment",
        )
        await orders_repo.mark_paid(tx, order_id)  # débito local confirmado => pago
        new_balance = await wallets.apply_delta(tx, buyer_id, -price, product["currency"])
        await wallets.add_ledger(
            tx,
            user_id=buyer_id,
            guild_id=guild_id,
            amount_cents=-price,
            operation_amount_cents=price,
            currency=product["currency"],
            type="purchase",
            status="completed",
            reference_type="order",
            reference_id=order_id,
            reason=f"Compra: {product['name']}",
            actor_id=buyer_id,
        )
        await audit.record(
            tx,
            guild_id=guild_id,
            actor_id=buyer_id,
            action="order.paid_wallet",
            target_type="order",
            target_id=str(order_id),
            details={
                "product_id": product_id,
                "product": product["name"],
                "price_cents": price,
                "new_balance_cents": new_balance,
            },
        )
        order = await orders_repo.get_order(tx, order_id)
        return WalletPurchase(order=order, new_balance=new_balance)  # type: ignore[arg-type]


# =============================================================== compra via Stripe
async def start_stripe_order(
    db: Database,
    stripe_svc: StripeService,
    config: Config,
    guild_id: int,
    buyer_id: int,
    product_id: int,
) -> tuple[Row, Any]:
    """Cria pedido aguardando pagamento + reserva de estoque + Checkout Session."""
    async with db.tx() as tx:
        product = await catalog.get_product(tx, product_id)
        if product is None or int(product["guild_id"]) != guild_id:
            raise ProductUnavailable("Produto não encontrado nesta loja.")
        if not int(product["active"]):
            raise ProductUnavailable("Este produto não está ativo no momento.")
        if not await catalog.try_reserve_stock(tx, product_id, 1):
            raise OutOfStock("Estoque esgotado para este produto.")
        order_id = await orders_repo.create_order(
            tx,
            guild_id=guild_id,
            buyer_id=buyer_id,
            product=product,
            payment_method="stripe",
            status="awaiting_payment",
        )
        await catalog.create_reservation(tx, order_id, product_id, 1, _ttl_expiry(config))
        await wallets.add_ledger(
            tx,
            user_id=buyer_id,
            guild_id=guild_id,
            amount_cents=0,
            operation_amount_cents=int(product["price_cents"]),
            currency=product["currency"],
            type="purchase",
            status="pending",
            reference_type="order",
            reference_id=order_id,
            reason=f"Compra (Stripe): {product['name']}",
            actor_id=buyer_id,
        )
    session = await stripe_svc.create_order_session(
        order_id=order_id,
        product_name=product["name"],
        amount_cents=int(product["price_cents"]),
        currency=product["currency"],
        guild_id=guild_id,
        user_id=buyer_id,
        product_id=product_id,
    )
    async with db.tx() as tx:
        await tx.execute(
            "UPDATE orders SET stripe_session_id = ? WHERE id = ?",
            (sget(session, "id"), order_id),
        )
        order = await orders_repo.get_order(tx, order_id)
    return order, session  # type: ignore[return-value]


async def complete_order(db: Database, config: Config, session: Any) -> Optional[Row]:
    """Confirma pedido pelo WEBHOOK: consome reserva, marca pago. Idempotente."""
    session_id = sget(session, "id")
    if sget(session, "payment_status") != "paid":
        return None
    async with db.tx() as tx:
        order = await orders_repo.get_order_by_session(tx, session_id)
        if order is None:
            meta_order = smeta(session, "order_id")
            order = await orders_repo.get_order(tx, int(meta_order)) if meta_order else None
        if order is None:
            log.warning("Webhook de pedido sem pedido local: session=%s", session_id)
            return None
        order_id = int(order["id"])
        if order["status"] != "awaiting_payment":
            return None  # já confirmado anteriormente (idempotência)
        reservation = await catalog.get_reservation_by_order(tx, order_id)
        consumed = (
            await catalog.consume_reservation(tx, int(reservation["id"]))
            if reservation is not None
            else True
        )
        if not consumed:
            await orders_repo.set_status(tx, order_id, "failed_out_of_stock")
            await audit.record(
                tx,
                guild_id=int(order["guild_id"]),
                actor_id=None,
                action="order.failed_out_of_stock",
                target_type="order",
                target_id=str(order_id),
                details={"stripe_session": session_id, "motivo": "estoque indisponível no webhook; reembolsar via Stripe"},
            )
            return await orders_repo.get_order(tx, order_id)
        await orders_repo.mark_paid(tx, order_id, sget(session, "payment_intent"))
        ledger = await tx.fetchone(
            "SELECT * FROM ledger_transactions WHERE reference_type = 'order' AND reference_id = ? AND type = 'purchase'",
            (order_id,),
        )
        if ledger is not None:
            await wallets.complete_ledger(tx, int(ledger["id"]), stripe_ref=sget(session, "payment_intent"))
        await audit.record(
            tx,
            guild_id=int(order["guild_id"]),
            actor_id=None,
            action="order.paid_stripe",
            target_type="order",
            target_id=str(order_id),
            details={
                "product_id": int(order["product_id"]),
                "price_cents": int(order["price_cents"]),
                "stripe_session": session_id,
            },
        )
        return await orders_repo.get_order(tx, order_id)


async def cancel_order(db: Database, config: Config, order_id: int, status: str, reason: str) -> bool:
    """Cancela/expira pedido aguardando pagamento e libera a reserva de estoque."""
    async with db.tx() as tx:
        order = await orders_repo.get_order(tx, order_id)
        if order is None or order["status"] != "awaiting_payment":
            return False
        await orders_repo.set_status(tx, order_id, status)
        reservation = await catalog.get_reservation_by_order(tx, order_id)
        if reservation is not None:
            await catalog.release_reservation(tx, int(reservation["id"]))
        ledger = await tx.fetchone(
            "SELECT * FROM ledger_transactions WHERE reference_type = 'order' AND reference_id = ? AND status = 'pending'",
            (order_id,),
        )
        if ledger is not None:
            await wallets.set_ledger_status(tx, int(ledger["id"]), "cancelled")
        await audit.record(
            tx,
            guild_id=int(order["guild_id"]),
            actor_id=None,
            action=f"order.{status}",
            target_type="order",
            target_id=str(order_id),
            details={"motivo": reason},
        )
        return True


async def sweep_expired_reservations(db: Database, config: Config) -> list[int]:
    """Libera reservas expiradas (Checkout abandonado). Retorna order_ids expirados."""
    now = utcnow_iso()
    expired = await db.fetchall(
        "SELECT * FROM stock_reservations WHERE status = 'reserved' AND expires_at <= ?",
        (now,),
    )
    cancelled: list[int] = []
    for reservation in expired:
        order_id = int(reservation["order_id"])
        if await cancel_order(db, config, order_id, "expired", "reserva expirada (pagamento não confirmado)"):
            cancelled.append(order_id)
    return cancelled


# ================================================================ reembolsos/disputas
async def apply_charge_refund(db: Database, config: Config, charge: Any) -> Optional[str]:
    """Registra estorno (charge.refunded) e ajusta carteira/pedido de forma rastreável."""
    payment_intent = sget(charge, "payment_intent")
    refunded = int(sget(charge, "amount_refunded", 0) or 0)
    if not payment_intent or refunded <= 0:
        return None

    async with db.tx() as tx:
        topup = await wallets.get_topup_by_payment_intent(tx, payment_intent)
        if topup is not None and topup["status"] in ("paid", "partially_refunded"):
            user_id = int(topup["user_id"])
            delta = -min(refunded, int(topup["amount_cents"]))
            new_balance = await wallets.apply_delta(tx, user_id, delta, topup["currency"])
            full = refunded >= int(topup["amount_cents"])
            await wallets.set_topup_status(tx, int(topup["id"]), "refunded" if full else "partially_refunded")
            await wallets.add_ledger(
                tx,
                user_id=user_id,
                guild_id=None,
                amount_cents=delta,
                operation_amount_cents=refunded,
                currency=topup["currency"],
                type="refund",
                status="completed",
                stripe_ref=sget(charge, "id"),
                reference_type="topup",
                reference_id=int(topup["id"]),
                reason="Estorno confirmado pelo Stripe",
            )
            await audit.record(
                tx,
                guild_id=None,
                actor_id=None,
                action="topup.refunded",
                target_type="topup",
                target_id=str(topup["id"]),
                details={
                    "user_id": user_id,
                    "refunded_cents": refunded,
                    "new_balance_cents": new_balance,
                    "charge": sget(charge, "id"),
                },
            )
            return f"topup:{topup['id']}"

        order = await orders_repo.get_order_by_payment_intent(tx, payment_intent)
        if order is not None and order["status"] == "paid":
            await orders_repo.set_status(tx, int(order["id"]), "refunded")
            await wallets.add_ledger(
                tx,
                user_id=int(order["buyer_id"]),
                guild_id=int(order["guild_id"]),
                amount_cents=0,  # pagamento direto Stripe: carteira não envolvida
                operation_amount_cents=refunded,
                currency=order["currency"],
                type="refund",
                status="completed",
                stripe_ref=sget(charge, "id"),
                reference_type="order",
                reference_id=int(order["id"]),
                reason="Estorno confirmado pelo Stripe (pedido direto)",
            )
            await audit.record(
                tx,
                guild_id=int(order["guild_id"]),
                actor_id=None,
                action="order.refunded",
                target_type="order",
                target_id=str(order["id"]),
                details={
                    "refunded_cents": refunded,
                    "charge": sget(charge, "id"),
                    "obs": "produto digital já entregue; avaliar bloqueio manual",
                },
            )
            return f"order:{order['id']}"
    return None


async def apply_dispute(db: Database, config: Config, dispute: Any, closed: bool = False) -> Optional[str]:
    """Registra disputa (chargeback) e, no fechamento, ajusta conforme resultado."""
    payment_intent = sget(dispute, "payment_intent")
    status = sget(dispute, "status")
    amount = int(sget(dispute, "amount", 0) or 0)
    if not payment_intent:
        return None

    async with db.tx() as tx:
        topup = await wallets.get_topup_by_payment_intent(tx, payment_intent)
        target_type = "topup"
        target: Optional[Row] = topup
        order: Optional[Row] = None
        if topup is None:
            order = await orders_repo.get_order_by_payment_intent(tx, payment_intent)
            target = order
            target_type = "order"
        if target is None:
            return None
        user_id = int(target["user_id"] if target_type == "topup" else target["buyer_id"])
        guild_id = None if target_type == "topup" else int(target["guild_id"])
        currency = target["currency"]

        if not closed:
            if target_type == "topup":
                await wallets.set_topup_status(tx, int(target["id"]), "disputed")
            else:
                await orders_repo.set_status(tx, int(target["id"]), "disputed")
            await wallets.add_ledger(
                tx,
                user_id=user_id,
                guild_id=guild_id,
                amount_cents=0,
                operation_amount_cents=amount,
                currency=currency,
                type="chargeback",
                status="pending",
                stripe_ref=sget(dispute, "id"),
                reference_type=target_type,
                reference_id=int(target["id"]),
                reason="Disputa aberta no Stripe",
            )
            action = "dispute.opened"
        else:
            lost = status in ("lost", "warning_closed")
            delta = -amount if (lost and target_type == "topup") else 0
            if delta:
                await wallets.apply_delta(tx, user_id, delta, currency)
            if target_type == "topup":
                await wallets.set_topup_status(tx, int(target["id"]), "disputed")
            await wallets.add_ledger(
                tx,
                user_id=user_id,
                guild_id=guild_id,
                amount_cents=delta,
                operation_amount_cents=amount,
                currency=currency,
                type="chargeback",
                status="completed" if lost else "reversed",
                stripe_ref=sget(dispute, "id"),
                reference_type=target_type,
                reference_id=int(target["id"]),
                reason=f"Disputa encerrada (status Stripe: {status})",
            )
            action = "dispute.closed"
        await audit.record(
            tx,
            guild_id=guild_id,
            actor_id=None,
            action=action,
            target_type=target_type,
            target_id=str(target["id"]),
            details={"dispute": sget(dispute, "id"), "amount_cents": amount, "status": status},
        )
        return f"{target_type}:{target['id']}"


# =================================================================== ajuste manual
async def maybe_low_stock_alert(
    db: Database, notifier, guild_id: Optional[int], product_id: int
) -> None:
    """Dispara alerta de estoque baixo no canal de logs, quando configurado."""
    if guild_id is None:
        return
    product = await catalog.get_product(db, product_id)
    if product is None or product["low_stock_threshold"] is None:
        return
    available = catalog.available_stock(product)
    if available is not None and available <= int(product["low_stock_threshold"]):
        await notifier.low_stock(guild_id, product)


async def admin_adjust(
    db: Database,
    config: Config,
    *,
    actor_id: int,
    target_user_id: int,
    delta_cents: int,
    reason: str,
    guild_id: Optional[int],
) -> int:
    """Ajuste manual de saldo: exige motivo e registra tudo no ledger + auditoria."""
    if not reason or not reason.strip():
        raise PurchaseError("Todo ajuste manual de saldo exige um motivo.")
    if delta_cents == 0:
        raise PurchaseError("O ajuste não pode ser zero.")
    async with db.tx() as tx:
        new_balance = await wallets.apply_delta(tx, target_user_id, delta_cents, config.currency)
        await wallets.add_ledger(
            tx,
            user_id=target_user_id,
            guild_id=guild_id,
            amount_cents=delta_cents,
            operation_amount_cents=abs(delta_cents),
            currency=config.currency,
            type="admin_adjust",
            status="completed",
            reference_type="manual",
            reason=reason.strip(),
            actor_id=actor_id,
        )
        await audit.record(
            tx,
            guild_id=guild_id,
            actor_id=actor_id,
            action="wallet.admin_adjust",
            target_type="wallet",
            target_id=str(target_user_id),
            details={
                "delta_cents": delta_cents,
                "new_balance_cents": new_balance,
                "reason": reason.strip(),
            },
        )
        return new_balance

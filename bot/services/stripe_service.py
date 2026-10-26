"""Integração com a API do Stripe (Checkout hospedado + verificação de webhook).

O bot NUNCA coleta dados de cartão: todo pagamento acontece em páginas
hospedadas pelo Stripe (Checkout Session). A confirmação de pagamento é
feita exclusivamente pelo webhook com assinatura validada.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import stripe

from ..config import Config

log = logging.getLogger("eclipse.stripe")


def sget(obj: Any, key: str, default: Any = None) -> Any:
    """Leitura tolerante: objetos Stripe e dicts simples."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def smeta(obj: Any, key: str, default: Any = None) -> Any:
    metadata = sget(obj, "metadata", {}) or {}
    if isinstance(metadata, dict):
        return metadata.get(key, default)
    return getattr(metadata, key, default)


class StripeService:
    """Encapsula chamadas à API do Stripe (executadas em thread para não bloquear o loop)."""

    def __init__(self, config: Config) -> None:
        self.config = config
        stripe.api_key = config.stripe_secret_key
        self._stripe = stripe

    # ------------------------------------------------------------------ sessões
    async def create_topup_session(self, *, topup_id: int, user_id: int, amount_cents: int, currency: str) -> Any:
        session = await asyncio.to_thread(
            self._stripe.checkout.Session.create,
            mode="payment",
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": currency.lower(),
                        "unit_amount": amount_cents,
                        "product_data": {
                            "name": "Recarga de saldo — Eclipse Store",
                            "description": f"Crédito de carteira para o usuário Discord {user_id}",
                        },
                    },
                    "quantity": 1,
                }
            ],
            metadata={"kind": "topup", "topup_id": str(topup_id), "user_id": str(user_id)},
            success_url=self.config.success_url,
            cancel_url=self.config.cancel_url,
            expires_after={"minutes": 60},
        )
        return session

    async def create_order_session(self, *, order_id: int, product_name: str, amount_cents: int, currency: str, guild_id: int, user_id: int, product_id: int) -> Any:
        session = await asyncio.to_thread(
            self._stripe.checkout.Session.create,
            mode="payment",
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": currency.lower(),
                        "unit_amount": amount_cents,
                        "product_data": {
                            "name": f"Eclipse Store — {product_name}",
                            "description": "Compra de produto digital",
                        },
                    },
                    "quantity": 1,
                }
            ],
            metadata={
                "kind": "order",
                "order_id": str(order_id),
                "product_id": str(product_id),
                "guild_id": str(guild_id),
                "user_id": str(user_id),
            },
            success_url=self.config.success_url,
            cancel_url=self.config.cancel_url,
            expires_after={"minutes": max(self.config.reservation_ttl_minutes, 10)},
        )
        return session

    async def expire_session(self, session_id: Optional[str]) -> None:
        """Melhor esforço: expira sessão abandonada no Stripe."""
        if not session_id:
            return
        try:
            await asyncio.to_thread(self._stripe.checkout.Session.expire, session_id)
        except Exception as exc:  # noqa: BLE001 - nunca derruba o fluxo principal
            log.warning("Não foi possível expirar sessão %s: %s", session_id, exc)

    # ------------------------------------------------------------------ webhook
    def construct_event(self, payload: bytes, sig_header: Optional[str]) -> Any:
        """Valida a assinatura do webhook. Lança exceção se inválida."""
        return self._stripe.Webhook.construct_event(
            payload, sig_header, self.config.stripe_webhook_secret
        )

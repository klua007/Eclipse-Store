"""Servidor web embutido (webhooks do Stripe + páginas de retorno)."""
from .webhook_server import WebhookServer

__all__ = ["WebhookServer"]

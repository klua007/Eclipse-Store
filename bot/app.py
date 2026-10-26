"""Contêiner de dependências da aplicação (acessível como ``bot.app``)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from .config import Config
from .db.core import Database
from .services.notifier import Notifier
from .services.permissions import PermissionService
from .services.stripe_service import StripeService

if TYPE_CHECKING:  # pragma: no cover
    import discord


@dataclass
class EclipseApp:
    """Agrega configuração e serviços compartilhados entre cogs e webhook."""

    config: Config
    db: Database
    stripe: StripeService
    perms: PermissionService
    notifier: Notifier
    bot: Optional["discord.Client"] = None

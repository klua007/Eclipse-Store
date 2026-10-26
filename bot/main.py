"""Ponto de entrada do Eclipse Store.

Responsabilidades:
- carregar configuração e logging (sem vazar segredos);
- conectar banco + migrações;
- iniciar servidor de webhooks do Stripe;
- publicar comandos de barra (guild de dev ou global);
- tarefa de fundo: liberação de reservas expiradas.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .app import EclipseApp
from .config import Config, ConfigError, load_config
from .db.core import Database
from .db.migrations import apply_migrations
from .services.notifier import Notifier
from .services.permissions import PermissionService
from .services.stripe_service import StripeService
from .web.webhook_server import WebhookServer

log = logging.getLogger("eclipse")


class _SecretFilter(logging.Filter):
    """Redação defensiva: nada parecido com token/chave vai para os logs."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in self.secrets:
            if secret in message:
                message = message.replace(secret, "***REDACTED***")
        record.msg = message
        record.args = ()
        return True


class EclipseBot(commands.Bot):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.members = True  # checagem viva de permissões do servidor
        super().__init__(command_prefix=commands.when_mentioned_or("!eclipse "), intents=intents, help_command=None)
        self.config = config
        self.app: Optional[EclipseApp] = None
        self.webhook_server: Optional[WebhookServer] = None
        self.sweeper = self._build_sweeper()

    # ------------------------------------------------------------------ startup
    async def setup_hook(self) -> None:
        db = Database(self.config.database_path)
        await db.connect()
        applied = await apply_migrations(db)
        if applied:
            log.info("Migrações aplicadas: %s", applied)

        stripe_svc = StripeService(self.config)
        perms = PermissionService(self, db, self.config)
        notifier = Notifier(self, db, self.config)
        self.app = EclipseApp(
            config=self.config,
            db=db,
            stripe=stripe_svc,
            perms=perms,
            notifier=notifier,
            bot=self,
        )
        await perms.load_app_owner()

        self.webhook_server = WebhookServer(self.app)
        await self.webhook_server.start()

        for name in ("store", "wallet", "catalog_manage", "orders", "staff", "admin", "help"):
            await self.load_extension(f"bot.cogs.{name}")

        await self._sync_commands()
        self.sweeper.start()

    async def _sync_commands(self) -> None:
        if self.config.dev_guild_id:
            guild = discord.Object(id=self.config.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Comandos publicados na guild de desenvolvimento %s", self.config.dev_guild_id)
        else:
            await self.tree.sync()
            log.info("Comandos globais publicados (propagação pode levar até 1h).")

    # ------------------------------------------------------------- reservações
    def _build_sweeper(self) -> tasks.Loop:
        async def _sweep() -> None:
            if self.app is None:
                return
            from .db import orders as orders_repo
            from .db.core import utcnow_iso
            from .services import purchase

            now = utcnow_iso()
            rows = await self.app.db.fetchall(
                "SELECT * FROM stock_reservations WHERE status = 'reserved' AND expires_at <= ?",
                (now,),
            )
            for reservation in rows:
                order = await orders_repo.get_order(self.app.db, int(reservation["order_id"]))
                if order is None:
                    continue
                await self.app.stripe.expire_session(order["stripe_session_id"])
                cancelled = await purchase.cancel_order(
                    self.app.db,
                    self.app.config,
                    int(order["id"]),
                    "expired",
                    "reserva expirada sem pagamento confirmado",
                )
                if cancelled:
                    await self.app.notifier.log(
                        int(order["guild_id"]),
                        "⏳ Reserva expirada",
                        [
                            ("Pedido", f"#{order['id']} — {order['product_name']}"),
                            ("Cliente", f"<@{order['buyer_id']}>"),
                            ("Estoque", "reserva liberada"),
                        ],
                    )

        return tasks.loop(minutes=1)(_sweep)

    # ------------------------------------------------------------------ eventos
    async def on_ready(self) -> None:
        assert self.user is not None
        log.info("Eclipse Store online como %s (id=%s)", self.user, self.user.id)

    async def on_command_error(self, context, exception) -> None:  # pragma: no cover
        log.error("Erro de comando: %s", exception)

    async def close(self) -> None:
        if self.sweeper.is_running():
            self.sweeper.cancel()
        if self.webhook_server is not None:
            await self.webhook_server.stop()
        if self.app is not None:
            await self.app.db.close()
        await super().close()


def _configure_logging(config: Config) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addFilter(
        _SecretFilter(
            [
                config.discord_token,
                config.stripe_secret_key,
                config.stripe_webhook_secret,
            ]
        )
    )
    logging.getLogger("discord.http").setLevel(logging.WARNING)


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"[config] {exc}", file=sys.stderr)
        raise SystemExit(2)
    _configure_logging(config)
    bot = EclipseBot(config)

    @bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        log.error("Erro em comando de barra: %r", error)
        message = "Erro inesperado. A equipe foi notificada pelos logs."
        if isinstance(error, app_commands.CommandInvokeError) and error.original is not None:
            log.error("Causa: %r", error.original)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass

    try:
        asyncio.run(bot.start(config.discord_token))
    except KeyboardInterrupt:  # pragma: no cover
        log.info("Encerrando por teclado.")


if __name__ == "__main__":
    main()

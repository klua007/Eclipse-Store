"""Logs operacionais em canal privado configurável por guild.

Canal definido em ``guild_settings.log_channel_id`` (comando /config logs)
ou pela variável LOG_CHANNEL_ID como padrão inicial.
Nenhum log contém segredos, cartões ou conteúdo de entrega.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence, Tuple

import discord
from aiosqlite import Row

from ..config import Config
from ..db import staff
from ..db.core import Database
from ..ui.theme import COLOR_ACCENT, COLOR_DANGER, COLOR_PRIMARY, base_embed

log = logging.getLogger("eclipse.notifier")


class Notifier:
    def __init__(self, bot: discord.Client, db: Database, config: Config) -> None:
        self.bot = bot
        self.db = db
        self.config = config

    async def _channel(self, guild_id: Optional[int]) -> Optional[discord.TextChannel]:
        if guild_id is None:
            return None
        settings = await staff.get_settings(self.db, guild_id)
        channel_id = settings["log_channel_id"] or self.config.log_channel_id
        if not channel_id:
            return None
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except discord.HTTPException:
                return None
        return channel  # type: ignore[return-value]

    async def log(
        self,
        guild_id: Optional[int],
        title: str,
        fields: Sequence[Tuple[str, str]] = (),
        color: int = COLOR_PRIMARY,
    ) -> None:
        """Envia embed de log no canal privado. Falhas nunca derrubam o fluxo."""
        try:
            channel = await self._channel(guild_id)
            if channel is None:
                return
            embed = base_embed(title=title, color=color)
            for name, value in fields:
                embed.add_field(name=name, value=str(value)[:1024], inline=False)
            embed.set_footer(text="Eclipse Store • log operacional")
            await channel.send(embed=embed)
        except Exception:  # noqa: BLE001
            log.exception("Falha ao enviar log operacional (guild=%s, title=%s)", guild_id, title)

    async def low_stock(self, guild_id: int, product: Row) -> None:
        await self.log(
            guild_id,
            "📉 Estoque baixo",
            [
                ("Produto", f"#{product['id']} — {product['name']}"),
                ("Disponível", str((product["stock"] or 0) - product["reserved_qty"])),
                ("Alerta configurado", str(product["low_stock_threshold"])),
                ("Ação", "Reponha com `/produto editar` → Estoque."),
            ],
            color=COLOR_DANGER,
        )

    async def payment_confirmed(self, guild_id: Optional[int], kind: str, user_id: int, amount_cents: int, currency: str, ref: str) -> None:
        from .money import format_cents

        await self.log(
            guild_id,
            "💳 Pagamento confirmado pelo Stripe",
            [
                ("Tipo", kind),
                ("Usuário", f"<@{user_id}> (`{user_id}`)"),
                ("Valor", format_cents(amount_cents, currency)),
                ("Referência Stripe", ref or "-"),
            ],
            color=COLOR_ACCENT,
        )

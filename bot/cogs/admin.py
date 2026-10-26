"""Comandos /painel, /ajuste e /config — administração da loja."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..db import audit, staff
from ..services import purchase
from ..services.money import MoneyError, format_cents, parse_cents, signed_format_cents
from ..ui.admin_views import AdminPanelView
from ..ui.components import safe_respond
from ..ui.seller_views import SellerPanelView
from ..ui.theme import error_embed, ok_embed, panel_embed


class AdminCog(commands.Cog):
    painel = app_commands.Group(name="painel", description="Painéis de gerenciamento")
    ajuste = app_commands.Group(name="ajuste", description="Ajustes manuais (dono/admin)")
    config = app_commands.Group(name="config", description="Configurações da loja (dono/admin)")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.ec = bot.app  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ painéis
    @painel.command(name="vendedor", description="Painel do vendedor (produtos, estoque, pedidos)")
    async def painel_vendedor(self, interaction: discord.Interaction) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        view = SellerPanelView(self.ec, interaction.guild_id or 0, interaction.user.id)
        await safe_respond(
            interaction,
            panel_embed(
                "Painel do vendedor",
                "Gerencie produtos, preços, estoque e pedidos.\n"
                "Toda ação é confirmada, registrada em auditoria e notificada no canal de logs.",
            ),
            view=view,
        )

    @painel.command(name="admin", description="Painel administrativo (equipe, registros, ajustes)")
    async def painel_admin(self, interaction: discord.Interaction) -> None:
        if not await self.ec.perms.ensure_admin(interaction):
            return
        view = AdminPanelView(self.ec, interaction.guild_id or 0)
        await safe_respond(
            interaction,
            panel_embed(
                "Painel administrativo",
                "Vendedores, ajustes de saldo com motivo, canal de logs, transações e auditoria.",
            ),
            view=view,
        )

    # ------------------------------------------------------------------- ajustes
    @ajuste.command(name="saldo", description="Ajuste manual de saldo com motivo obrigatório")
    @app_commands.describe(
        usuario="Usuário afetado",
        valor="Valor com sinal (ex.: +10,00 ou -5,00)",
        motivo="Motivo do ajuste (fica registrado)",
    )
    async def ajuste_saldo(
        self, interaction: discord.Interaction, usuario: discord.User, valor: str, motivo: str
    ) -> None:
        if not await self.ec.perms.ensure_admin(interaction):
            return
        raw = valor.strip().replace(" ", "")
        negative = raw.startswith("-")
        if raw.startswith(("+", "-")):
            raw = raw[1:]
        try:
            cents = parse_cents(raw)
        except MoneyError as exc:
            await safe_respond(interaction, error_embed("Valor inválido", str(exc)))
            return
        delta = -cents if negative else cents
        if not motivo.strip():
            await safe_respond(interaction, error_embed("Motivo obrigatório"))
            return
        new_balance = await purchase.admin_adjust(
            self.ec.db,
            self.ec.config,
            actor_id=interaction.user.id,
            target_user_id=usuario.id,
            delta_cents=delta,
            reason=motivo,
            guild_id=interaction.guild_id,
        )
        await self.ec.notifier.log(
            interaction.guild_id,
            "⚖️ Ajuste manual de saldo",
            [
                ("Usuário", f"{usuario.mention} (`{usuario.id}`)"),
                ("Variação", signed_format_cents(delta, self.ec.config.currency)),
                ("Novo saldo", format_cents(new_balance, self.ec.config.currency)),
                ("Motivo", motivo),
                ("Por", f"<@{interaction.user.id}>"),
            ],
        )
        await safe_respond(
            interaction,
            ok_embed(
                "Ajuste aplicado",
                f"{usuario.mention}: **{signed_format_cents(delta, self.ec.config.currency)}** → "
                f"saldo **{format_cents(new_balance, self.ec.config.currency)}**",
            ),
        )

    # -------------------------------------------------------------- configurações
    @config.command(name="logs", description="Definir o canal privado de logs operacionais")
    @app_commands.describe(canal="Canal privado que receberá os registros")
    async def config_logs(self, interaction: discord.Interaction, canal: discord.TextChannel) -> None:
        if not await self.ec.perms.ensure_admin(interaction):
            return
        async with self.ec.db.tx() as tx:
            await staff.set_log_channel(tx, interaction.guild_id or 0, canal.id)
            await audit.record(
                tx,
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                action="config.log_channel",
                target_type="channel",
                target_id=str(canal.id),
            )
        await safe_respond(interaction, ok_embed("Canal de logs configurado", f"{canal.mention} receberá os registros."))

    @config.command(name="dono", description="Transferir a posse da loja neste servidor")
    @app_commands.describe(novo_dono="Novo dono da loja")
    async def config_dono(self, interaction: discord.Interaction, novo_dono: discord.Member) -> None:
        if not await self.ec.perms.is_owner(interaction.user.id, interaction.guild):
            await safe_respond(interaction, error_embed("Somente o dono da loja pode transferir a posse."))
            return
        async with self.ec.db.tx() as tx:
            await staff.set_owner(tx, interaction.guild_id or 0, novo_dono.id)
            await audit.record(
                tx,
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                action="config.owner_transferred",
                target_type="user",
                target_id=str(novo_dono.id),
            )
        await self.ec.notifier.log(
            interaction.guild_id,
            "👑 Posse transferida",
            [("Novo dono", novo_dono.mention), ("Por", f"<@{interaction.user.id}>")],
        )
        await safe_respond(interaction, ok_embed("Posse transferida", f"{novo_dono.mention} é o novo dono da loja."))

    @config.command(name="ver", description="Ver configurações atuais da loja")
    async def config_ver(self, interaction: discord.Interaction) -> None:
        if not await self.ec.perms.ensure_admin(interaction):
            return
        settings = await staff.get_settings(self.ec.db, interaction.guild_id or 0)
        embed = panel_embed("Configurações da loja", None)
        owner_id = settings["owner_id"] or (self.ec.config.owner_ids[0] if self.ec.config.owner_ids else None)
        embed.add_field(name="Dono da loja", value=f"<@{owner_id}>" if owner_id else "-", inline=True)
        embed.add_field(
            name="Canal de logs",
            value=f"<#{settings['log_channel_id']}>" if settings["log_channel_id"] else "não configurado",
            inline=True,
        )
        embed.add_field(name="Moeda", value=self.ec.config.currency, inline=True)
        embed.add_field(
            name="Reserva de estoque",
            value=f"{self.ec.config.reservation_ttl_minutes} min",
            inline=True,
        )
        embed.add_field(name="Webhook Stripe", value=f"`{self.ec.config.webhook_url}`", inline=False)
        await safe_respond(interaction, embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))

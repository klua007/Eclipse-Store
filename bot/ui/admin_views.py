"""Painel administrativo: vendedores, registros, ajustes de saldo e configurações."""
from __future__ import annotations

import logging

import discord
from aiosqlite import Row

from ..app import EclipseApp
from ..db import audit, staff, wallets
from ..services import purchase
from ..services.money import MoneyError, format_cents, parse_cents, signed_format_cents
from .components import ConfirmView, safe_edit, safe_respond
from .theme import COLOR_ACCENT, base_embed, error_embed, ok_embed, panel_embed

log = logging.getLogger("eclipse.ui.admin")


class AdminPanelView(discord.ui.View):
    def __init__(self, ec: EclipseApp, guild_id: int) -> None:
        super().__init__(timeout=600)
        self.ec = ec
        self.guild_id = guild_id

    @discord.ui.button(label="👥 Vendedores", style=discord.ButtonStyle.primary, row=0)
    async def sellers(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.ensure_admin(interaction):
            return
        rows = await staff.list_sellers(self.ec.db, self.guild_id)
        if not rows:
            embed = base_embed(
                "Nenhum vendedor cadastrado",
                "Adicione com `/vendedor adicionar @usuario`.",
                COLOR_ACCENT,
            )
            await safe_respond(interaction, embed)
            return
        view = SellersManageView(self.ec, rows)
        embed = panel_embed("Vendedores autorizados", "Selecione para remover.")
        for row in rows[:20]:
            embed.add_field(
                name=f"<@{row['user_id']}>",
                value=f"desde {row['added_at']}\npor <@{row['added_by']}>",
                inline=True,
            )
        await safe_edit(interaction, embed, view=view)

    @discord.ui.button(label="⚖️ Ajuste de saldo", style=discord.ButtonStyle.danger, row=0)
    async def adjust(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.ensure_admin(interaction):
            return
        await interaction.response.send_modal(AdjustModal(self.ec))

    @discord.ui.button(label="📣 Canal de logs", style=discord.ButtonStyle.secondary, row=0)
    async def logs(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.ensure_admin(interaction):
            return
        await interaction.response.send_modal(LogChannelModal(self.ec))

    @discord.ui.button(label="📒 Transações", style=discord.ButtonStyle.secondary, row=1)
    async def ledger(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.ensure_admin(interaction):
            return
        rows = await wallets.recent_ledger(self.ec.db, self.guild_id, limit=10)
        embed = panel_embed("Transações recentes (ledger)", None)
        if not rows:
            embed.description = "Nenhuma transação registrada nesta guild."
        for row in rows:
            embed.add_field(
                name=f"#{row['id']} {row['type']} • {row['status']}",
                value=(
                    f"user `{row['user_id']}` • {signed_format_cents(int(row['amount_cents']), row['currency'])}\n"
                    f"{row['reason'] or '-'} • {row['created_at']}"
                ),
                inline=False,
            )
        await safe_respond(interaction, embed)

    @discord.ui.button(label="🕵️ Auditoria", style=discord.ButtonStyle.secondary, row=1)
    async def audit_view(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.ensure_admin(interaction):
            return
        rows = await audit.recent(self.ec.db, self.guild_id, limit=10)
        embed = panel_embed("Auditoria recente", None)
        if not rows:
            embed.description = "Nenhum evento de auditoria nesta guild."
        for row in rows:
            details = row["details"] or ""
            embed.add_field(
                name=f"{row['action']} • {row['created_at']}",
                value=f"ator `{row['actor_id']}` • alvo {row['target_type'] or '-'}#{row['target_id'] or '-'}\n{details[:200]}",
                inline=False,
            )
        await safe_respond(interaction, embed)

    @discord.ui.button(label="👑 Transferir dono", style=discord.ButtonStyle.danger, row=2)
    async def transfer(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.is_owner(interaction.user.id, interaction.guild):
            await safe_respond(interaction, error_embed("Somente o dono da loja pode transferir a posse."))
            return
        await interaction.response.send_modal(OwnerModal(self.ec))


class SellersManageView(discord.ui.View):
    def __init__(self, ec: EclipseApp, rows: list[Row]) -> None:
        super().__init__(timeout=300)
        self.ec = ec
        options = [
            discord.SelectOption(label=f"ID {r['user_id']}", value=str(r["user_id"]))
            for r in rows[:25]
        ]
        self.select = discord.ui.Select(placeholder="Remover vendedor…", options=options)
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        if not await self.ec.perms.ensure_admin(interaction):
            return
        user_id = int(self.select.values[0])

        async def _do(inner: discord.Interaction) -> None:
            async with self.ec.db.tx() as tx:
                await staff.remove_seller(tx, interaction.guild_id or 0, user_id)
                await audit.record(
                    tx,
                    guild_id=interaction.guild_id,
                    actor_id=interaction.user.id,
                    action="seller.removed",
                    target_type="user",
                    target_id=str(user_id),
                )
            await self.ec.notifier.log(
                interaction.guild_id,
                "👥 Vendedor removido",
                [("Usuário", f"<@{user_id}> (`{user_id}`)"), ("Por", f"<@{interaction.user.id}>")],
            )
            await safe_edit(inner, ok_embed("Vendedor removido.", f"<@{user_id}> não possui mais acesso."), view=None)

        view = ConfirmView(
            title="Remover vendedor",
            description=f"<@{user_id}> perderá o acesso ao painel de vendas imediatamente.",
            confirm_label="Remover",
            on_confirm=_do,
            owner_id=interaction.user.id,
        )
        await safe_respond(interaction, view.initial_embed(), view=view)


class AdjustModal(discord.ui.Modal, title="Ajuste manual de saldo"):
    user_id = discord.ui.TextInput(label="ID numérico do usuário", style=discord.TextStyle.short, max_length=25)
    value = discord.ui.TextInput(label="Valor com sinal (ex.: +10,00 ou -500)", style=discord.TextStyle.short, max_length=20)
    reason = discord.ui.TextInput(label="Motivo (obrigatório)", style=discord.TextStyle.paragraph, max_length=300)

    def __init__(self, ec: EclipseApp) -> None:
        super().__init__()
        self.ec = ec

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.ec.perms.ensure_admin(interaction):
            return
        if not self.user_id.value.strip().isdigit():
            await safe_respond(interaction, error_embed("ID inválido", "Informe o ID numérico do Discord."))
            return
        raw = self.value.value.strip().replace(" ", "")
        negative = raw.startswith("-")
        if raw.startswith(("+", "-")):
            raw = raw[1:]
        try:
            cents = parse_cents(raw)
        except MoneyError as exc:
            await safe_respond(interaction, error_embed("Valor inválido", str(exc)))
            return
        delta = -cents if negative else cents
        if not self.reason.value.strip():
            await safe_respond(interaction, error_embed("Motivo obrigatório", "Todo ajuste manual exige motivo registrado."))
            return
        try:
            new_balance = await purchase.admin_adjust(
                self.ec.db,
                self.ec.config,
                actor_id=interaction.user.id,
                target_user_id=int(self.user_id.value.strip()),
                delta_cents=delta,
                reason=self.reason.value,
                guild_id=interaction.guild_id,
            )
        except purchase.PurchaseError as exc:
            await safe_respond(interaction, error_embed("Ajuste não aplicado", str(exc)))
            return
        await self.ec.notifier.log(
            interaction.guild_id,
            "⚖️ Ajuste manual de saldo",
            [
                ("Usuário", f"<@{self.user_id.value.strip()}>"),
                ("Variação", signed_format_cents(delta, self.ec.config.currency)),
                ("Novo saldo", format_cents(new_balance, self.ec.config.currency)),
                ("Motivo", self.reason.value),
                ("Por", f"<@{interaction.user.id}>"),
            ],
        )
        warning = ""
        if new_balance < 0:
            warning = "\n⚠️ O saldo ficou negativo (estorno/ajuste maior que o saldo)."
        await safe_respond(
            interaction,
            ok_embed(
                "Ajuste aplicado",
                f"Variação: **{signed_format_cents(delta, self.ec.config.currency)}**\n"
                f"Novo saldo: **{format_cents(new_balance, self.ec.config.currency)}**{warning}",
            ),
        )


class LogChannelModal(discord.ui.Modal, title="Canal privado de logs"):
    channel_id = discord.ui.TextInput(label="ID do canal de logs", style=discord.TextStyle.short, max_length=25)

    def __init__(self, ec: EclipseApp) -> None:
        super().__init__()
        self.ec = ec

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.ec.perms.ensure_admin(interaction):
            return
        raw = self.channel_id.value.strip()
        if not raw.isdigit():
            await safe_respond(interaction, error_embed("ID de canal inválido."))
            return
        channel = interaction.guild.get_channel(int(raw)) if interaction.guild else None
        if channel is None:
            await safe_respond(interaction, error_embed("Canal não encontrado neste servidor."))
            return
        async with self.ec.db.tx() as tx:
            await staff.set_log_channel(tx, interaction.guild_id or 0, int(raw))
            await audit.record(
                tx,
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                action="config.log_channel",
                target_type="channel",
                target_id=raw,
            )
        await safe_respond(interaction, ok_embed("Canal de logs configurado", f"<#{raw}> receberá os registros operacionais."))


class OwnerModal(discord.ui.Modal, title="Transferir dono da loja"):
    new_owner = discord.ui.TextInput(label="ID numérico do novo dono", style=discord.TextStyle.short, max_length=25)

    def __init__(self, ec: EclipseApp) -> None:
        super().__init__()
        self.ec = ec

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.ec.perms.is_owner(interaction.user.id, interaction.guild):
            await safe_respond(interaction, error_embed("Somente o dono da loja pode transferir a posse."))
            return
        raw = self.new_owner.value.strip()
        if not raw.isdigit():
            await safe_respond(interaction, error_embed("ID inválido."))
            return
        async with self.ec.db.tx() as tx:
            await staff.set_owner(tx, interaction.guild_id or 0, int(raw))
            await audit.record(
                tx,
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                action="config.owner_transferred",
                target_type="user",
                target_id=raw,
            )
        await self.ec.notifier.log(
            interaction.guild_id,
            "👑 Posse transferida",
            [("Novo dono", f"<@{raw}>"), ("Por", f"<@{interaction.user.id}>")],
        )
        await safe_respond(interaction, ok_embed("Posse transferida", f"<@{raw}> agora é o dono da loja neste servidor."))

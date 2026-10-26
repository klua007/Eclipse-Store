"""Comandos /carteira — saldo, histórico e recarga (sempre privados)."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..db import wallets
from ..services.money import format_cents, signed_format_cents
from ..ui.theme import COLOR_ACCENT, base_embed
from ..ui.wallet_views import TopupModal

TYPE_LABELS = {
    "topup": "Recarga",
    "purchase": "Compra",
    "refund": "Estorno",
    "chargeback": "Disputa",
    "admin_adjust": "Ajuste manual",
}


class WalletCog(commands.Cog):
    carteira = app_commands.Group(name="carteira", description="Saldo, extrato e recargas")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.ec = bot.app  # type: ignore[attr-defined]

    @carteira.command(name="saldo", description="Consulta privada do seu saldo")
    async def saldo(self, interaction: discord.Interaction) -> None:
        wallet = await wallets.get_or_create_wallet(self.ec.db, interaction.user.id, self.ec.config.currency)
        embed = base_embed(
            "👝 Sua carteira",
            f"Saldo disponível: **{format_cents(int(wallet['balance_cents']), wallet['currency'])}**\n"
            f"Carteira vinculada ao ID `{interaction.user.id}`.",
            COLOR_ACCENT,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @carteira.command(name="historico", description="Extrato privado das suas transações")
    @app_commands.describe(limite="Quantidade de lançamentos (padrão 10, máx 50)")
    async def historico(self, interaction: discord.Interaction, limite: int = 10) -> None:
        limite = max(1, min(limite, 50))
        rows = await wallets.list_ledger(self.ec.db, interaction.user.id, limit=limite)
        embed = base_embed("📒 Seu extrato", None, COLOR_ACCENT)
        if not rows:
            embed.description = "Nenhuma transação ainda. Use `/carteira adicionar` para recarregar."
        for row in rows:
            embed.add_field(
                name=f"#{row['id']} • {TYPE_LABELS.get(row['type'], row['type'])} • {row['status']}",
                value=(
                    f"{signed_format_cents(int(row['amount_cents']), row['currency'])} • {row['created_at']}\n"
                    f"{row['reason'] or '-'}"
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @carteira.command(name="adicionar", description="Gerar pagamento seguro no Stripe para recarregar")
    async def adicionar(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TopupModal(self.ec))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WalletCog(bot))

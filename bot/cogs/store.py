"""Comando /loja — painel público do catálogo (compra sempre privada)."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..db import catalog
from ..ui.store_views import StoreView
from ..ui.theme import COLOR_ACCENT, base_embed, error_embed


class StoreCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.ec = bot.app  # type: ignore[attr-defined]

    @app_commands.command(name="loja", description="Abrir o painel do catálogo da Eclipse Store")
    async def loja(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("Use dentro do servidor da loja."), ephemeral=True
            )
            return
        products = await catalog.list_products(self.ec.db, interaction.guild.id)
        if not products:
            await interaction.response.send_message(
                embed=base_embed(
                    "🛍️ Eclipse Store",
                    "O catálogo ainda está vazio. Volte quando a equipe publicar produtos!",
                    COLOR_ACCENT,
                )
            )
            return
        view = StoreView(self.ec, interaction.guild.id)
        categories = sorted({p["category"] for p in products})
        view.category_select.options = [
            discord.SelectOption(label=c[:100], value=c[:100]) for c in categories[:25]
        ]
        embed = base_embed(
            "🛍️ Eclipse Store — Catálogo",
            f"**{len(products)}** produto(s) em **{len(categories)}** categoria(s).\n"
            "Escolha uma categoria para começar. Compras e entregas acontecem em privado.",
            COLOR_ACCENT,
        )
        await interaction.response.send_message(embed=embed, view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StoreCog(bot))

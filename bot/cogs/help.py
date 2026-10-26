"""Comando /ajuda — visão geral privada dos fluxos da loja."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..ui.theme import COLOR_PRIMARY, base_embed


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ajuda", description="Guia rápido da Eclipse Store")
    async def ajuda(self, interaction: discord.Interaction) -> None:
        embed = base_embed(
            "◐ Eclipse Store — Guia rápido",
            "Loja digital segura dentro do Discord. Pagamentos via Stripe, "
            "entrega privada e auditoria completa.",
            COLOR_PRIMARY,
        )
        embed.add_field(
            name="🛍️ Clientes",
            value=(
                "`/loja` — navegar no catálogo e comprar\n"
                "`/carteira saldo` — seu saldo (privado)\n"
                "`/carteira historico` — extrato (privado)\n"
                "`/carteira adicionar` — recarregar via Stripe\n"
                "`/meus-pedidos` — entregas e histórico (privado)"
            ),
            inline=False,
        )
        embed.add_field(
            name="📦 Vendedores",
            value=(
                "`/painel vendedor` — painel de gestão\n"
                "`/produto criar|editar|remover|listar|arquivo`\n"
                "`/pedidos listar|ver`"
            ),
            inline=False,
        )
        embed.add_field(
            name="🛠️ Dono/Admin",
            value=(
                "`/painel admin` — painel administrativo\n"
                "`/vendedor adicionar|remover` • `/vendedores listar`\n"
                "`/ajuste saldo` — com motivo obrigatório\n"
                "`/config logs|dono|ver`"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔒 Segurança",
            value=(
                "• Pagamentos confirmados **somente** pelo webhook do Stripe\n"
                "• Saldo, códigos e arquivos sempre em respostas privadas\n"
                "• Estoque atômico: nunca negativo, nunca duplicado\n"
                "• Toda ação sensível é auditada e notificada no canal de logs",
            ),
            inline=False,
        )
        embed.set_footer(text="Eclipse Store ◐ suporte: equipe da loja")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))

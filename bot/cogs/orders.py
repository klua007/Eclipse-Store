"""Comandos /pedidos (equipe) e /meus-pedidos (cliente)."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..db import orders as orders_repo
from ..services import delivery
from ..services.money import format_cents
from ..ui.components import safe_edit, safe_respond
from ..ui.seller_views import OrdersListView, _order_embed
from ..ui.theme import COLOR_ACCENT, base_embed, error_embed, panel_embed


class MyOrdersView(discord.ui.View):
    """Histórico privado do cliente com reentrega segura."""

    def __init__(self, ec, rows) -> None:
        super().__init__(timeout=600)
        self.ec = ec
        options = [
            discord.SelectOption(
                label=f"#{o['id']} {o['product_name']}"[:100],
                value=str(o["id"]),
                description=f"{o['status']} • {format_cents(int(o['price_cents']), o['currency'])}",
            )
            for o in rows[:25]
        ]
        self.select = discord.ui.Select(placeholder="Escolha o pedido…", options=options)
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        order = await orders_repo.get_order(self.ec.db, int(self.select.values[0]))
        if order is None or int(order["buyer_id"]) != interaction.user.id:
            await safe_respond(interaction, error_embed("Pedido não encontrado para sua conta."))
            return
        if order["status"] != "paid":
            await safe_respond(
                interaction,
                base_embed(
                    f"Pedido #{order['id']}",
                    f"Status: **{order['status']}** — ainda não há entrega para exibir.",
                    COLOR_ACCENT,
                ),
            )
            return
        view = MyOrderDetailView(self.ec, order)
        embed = base_embed(
            f"🧾 Pedido #{order['id']}",
            f"**{order['product_name']}** — {format_cents(int(order['price_cents']), order['currency'])}\n"
            f"Status: {order['status']} • Entrega: {order['delivery_status']}",
            COLOR_ACCENT,
        )
        await safe_edit(interaction, embed, view=view)


class MyOrderDetailView(discord.ui.View):
    def __init__(self, ec, order) -> None:
        super().__init__(timeout=600)
        self.ec = ec
        self.order_id = int(order["id"])
        self.buyer_id = int(order["buyer_id"])

    @discord.ui.button(label="📥 Receber entrega novamente", style=discord.ButtonStyle.primary)
    async def receive(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.buyer_id:
            await safe_respond(interaction, error_embed("Este pedido não é seu."))
            return
        order = await orders_repo.get_order(self.ec.db, self.order_id)
        if order is None or order["status"] != "paid":
            await safe_respond(interaction, error_embed("Pedido não pago."))
            return
        await delivery.redeliver(interaction, order)


class OrdersCog(commands.Cog):
    pedidos = app_commands.Group(name="pedidos", description="Consulta de pedidos (equipe)")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.ec = bot.app  # type: ignore[attr-defined]

    @pedidos.command(name="listar", description="Listar pedidos recentes da loja (equipe)")
    @app_commands.describe(status="Filtrar por status (ex.: paid, refunded, expired)")
    async def listar(self, interaction: discord.Interaction, status: str | None = None) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        rows = await orders_repo.list_orders(
            self.ec.db, guild_id=interaction.guild_id, status=status, limit=25
        )
        if not rows:
            await safe_respond(interaction, base_embed("Nenhum pedido encontrado.", None, COLOR_ACCENT))
            return
        view = OrdersListView(self.ec, rows)
        await safe_respond(interaction, panel_embed("Pedidos da loja", "Selecione para detalhar."), view=view)

    @pedidos.command(name="ver", description="Detalhar um pedido pelo número (equipe)")
    @app_commands.describe(pedido_id="Número do pedido")
    async def ver(self, interaction: discord.Interaction, pedido_id: int) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        order = await orders_repo.get_order(self.ec.db, pedido_id)
        if order is None or int(order["guild_id"]) != interaction.guild_id:
            await safe_respond(interaction, error_embed("Pedido não encontrado nesta loja."))
            return
        from ..ui.seller_views import OrderDetailView

        view = OrderDetailView(self.ec, order)
        await safe_respond(interaction, _order_embed(order), view=view)

    @app_commands.command(name="meus-pedidos", description="Seus pedidos e entregas (privado)")
    async def meus_pedidos(self, interaction: discord.Interaction) -> None:
        rows = await orders_repo.list_orders(self.ec.db, buyer_id=interaction.user.id, limit=25)
        paid = [r for r in rows]
        if not paid:
            await safe_respond(
                interaction,
                base_embed("Sem pedidos", "Quando você comprar algo, o histórico aparece aqui.", COLOR_ACCENT),
            )
            return
        view = MyOrdersView(self.ec, rows)
        await safe_respond(
            interaction,
            base_embed(
                "🧾 Meus pedidos",
                "Selecione um pedido para ver status e receber a entrega em privado.",
                COLOR_ACCENT,
            ),
            view=view,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OrdersCog(bot))

"""Painel público da loja: categorias → produtos → detalhe → compra."""
from __future__ import annotations

import logging

import discord
from aiosqlite import Row

from ..app import EclipseApp
from ..db import catalog
from ..services import delivery, purchase
from ..services.money import format_cents
from .components import safe_edit, safe_respond
from .theme import (
    COLOR_ACCENT,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    base_embed,
    error_embed,
    info_embed,
)

log = logging.getLogger("eclipse.ui.store")

TYPE_LABELS = {
    "text": "Texto",
    "code": "Código/chave",
    "link": "Link",
    "custom": "Instrução",
    "file": "Arquivo",
}


def product_embed(product: Row) -> discord.Embed:
    available = catalog.available_stock(product)
    embed = base_embed(f"🛍️ {product['name']}", product["description"] or None, COLOR_PRIMARY)
    embed.add_field(name="Categoria", value=product["category"], inline=True)
    embed.add_field(
        name="Preço", value=format_cents(int(product["price_cents"]), product["currency"]), inline=True
    )
    embed.add_field(
        name="Estoque",
        value="Ilimitado" if available is None else str(available),
        inline=True,
    )
    embed.add_field(name="Entrega", value=TYPE_LABELS.get(product["delivery_type"], "-"), inline=True)
    embed.add_field(name="Código do produto", value=f"#{product['id']}", inline=True)
    return embed


class StoreView(discord.ui.View):
    """Navegação do catálogo (mensagens públicas só mostram o catálogo)."""

    def __init__(self, ec: EclipseApp, guild_id: int) -> None:
        super().__init__(timeout=600)
        self.ec = ec
        self.guild_id = guild_id

    async def _categories(self) -> list[str]:
        rows = await catalog.list_categories(self.ec.db, self.guild_id)
        return [row["category"] for row in rows]

    @discord.ui.select(
        placeholder="📂 Escolha uma categoria…",
        min_values=1,
        max_values=1,
        row=0,
    )
    async def category_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        category = select.values[0]
        products = [
            p
            for p in await catalog.list_products(self.ec.db, self.guild_id)
            if p["category"] == category
        ]
        if not products:
            await safe_respond(interaction, info_embed("Categoria vazia", "Nenhum produto ativo aqui."))
            return
        view = ProductListView(self.ec, self.guild_id, products)
        embed = base_embed(
            f"📂 {category}", f"{len(products)} produto(s) disponível(is). Selecione para ver detalhes.", COLOR_ACCENT
        )
        await safe_edit(interaction, embed, view=view)


class ProductListView(discord.ui.View):
    def __init__(self, ec: EclipseApp, guild_id: int, products: list[Row]) -> None:
        super().__init__(timeout=600)
        self.ec = ec
        self.guild_id = guild_id
        options = [
            discord.SelectOption(
                label=f"{p['name']}"[:100],
                value=str(p["id"]),
                description=format_cents(int(p["price_cents"]), p["currency"]),
            )
            for p in products[:25]
        ]
        self.select = discord.ui.Select(
            placeholder="🛒 Escolha um produto…", options=options, row=0
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        product = await catalog.get_product(self.ec.db, int(self.select.values[0]))
        if product is None or not int(product["active"]):
            await safe_respond(interaction, error_embed("Produto indisponível."))
            return
        view = ProductDetailView(self.ec, product)
        await safe_edit(interaction, product_embed(product), view=view)


class ProductDetailView(discord.ui.View):
    def __init__(self, ec: EclipseApp, product: Row) -> None:
        super().__init__(timeout=600)
        self.ec = ec
        self.product_id = int(product["id"])

    @discord.ui.button(label="Comprar com saldo", style=discord.ButtonStyle.success, row=0)
    async def buy_wallet(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        product = await catalog.get_product(self.ec.db, self.product_id)
        if product is None or not int(product["active"]):
            await safe_respond(interaction, error_embed("Produto indisponível."))
            return
        view = PurchaseConfirmView(self.ec, product)
        embed = base_embed(
            "Confirmar compra",
            f"**{product['name']}** por **{format_cents(int(product['price_cents']), product['currency'])}**.\n"
            "O valor será debitado do seu saldo e a entrega é imediata e privada.",
            COLOR_SUCCESS,
        )
        await safe_respond(interaction, embed, view=view)

    @discord.ui.button(label="Pagar com cartão (Stripe)", style=discord.ButtonStyle.primary, row=0)
    async def buy_stripe(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            order, session = await purchase.start_stripe_order(
                self.ec.db,
                self.ec.stripe,
                self.ec.config,
                interaction.guild_id or 0,
                interaction.user.id,
                self.product_id,
            )
        except purchase.PurchaseError as exc:
            await safe_respond(interaction, error_embed("Não foi possível iniciar o pagamento", str(exc)))
            return
        view = discord.ui.View(timeout=600)
        view.add_item(
            discord.ui.Button(label="Abrir pagamento seguro", url=session.url, style=discord.ButtonStyle.link)
        )
        embed = base_embed(
            f"🧾 Pedido #{order['id']} criado",
            "Clique no botão para pagar na página segura do Stripe.\n"
            "**A entrega é liberada automaticamente quando o webhook confirmar o pagamento** — "
            "fechar a página não confirma o pagamento.\n"
            f"O estoque fica reservado por {self.ec.config.reservation_ttl_minutes} min.",
            COLOR_ACCENT,
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Voltar", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        products = await catalog.list_products(self.ec.db, interaction.guild_id or 0)
        view = ProductListView(self.ec, interaction.guild_id or 0, products)
        embed = base_embed(
            "🛍️ Catálogo Eclipse Store", "Selecione um produto para ver os detalhes.", COLOR_ACCENT
        )
        await safe_edit(interaction, embed, view=view)


class PurchaseConfirmView(discord.ui.View):
    """Confirmação final antes do débito atômico na carteira."""

    def __init__(self, ec: EclipseApp, product: Row) -> None:
        super().__init__(timeout=120)
        self.ec = ec
        self.product_id = int(product["id"])

    @discord.ui.button(label="Confirmar compra", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await purchase.purchase_with_wallet(
                self.ec.db,
                self.ec.config,
                interaction.guild_id or 0,
                interaction.user.id,
                self.product_id,
            )
        except purchase.InsufficientFunds as exc:
            await safe_respond(interaction, error_embed("Saldo insuficiente", str(exc)))
            return
        except purchase.OutOfStock:
            await safe_respond(interaction, error_embed("Estoque esgotado", "O último item acabou de ser vendido."))
            return
        except purchase.PurchaseError as exc:
            await safe_respond(interaction, error_embed("Compra não concluída", str(exc)))
            return

        order = result.order
        try:
            _, note = await delivery.deliver_in_interaction(interaction, self.ec.db, order)
        except discord.HTTPException:
            await safe_respond(
                interaction,
                error_embed(
                    "Pago, mas falha na entrega",
                    f"Seu pedido #{order['id']} foi registrado. Use `/meus-pedidos` para receber o conteúdo.",
                ),
            )
            return
        await purchase.maybe_low_stock_alert(
            self.ec.db, self.ec.notifier, interaction.guild_id, self.product_id
        )
        receipt = base_embed(
            f"Compra concluída — pedido #{order['id']}",
            f"Novo saldo: **{format_cents(result.new_balance, self.ec.config.currency)}**\n"
            + (note or "Conteúdo entregue acima, em privado."),
            COLOR_SUCCESS,
        )
        await safe_edit(interaction, receipt, view=None)
        self.stop()

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await safe_edit(
            interaction, base_embed("Compra cancelada", "Nenhum débito foi realizado.", COLOR_PRIMARY), view=None
        )

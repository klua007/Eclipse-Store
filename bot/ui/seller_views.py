"""Painel do vendedor: produtos, preços, estoque, entregas e pedidos."""
from __future__ import annotations

import logging

import discord
from aiosqlite import Row

from ..app import EclipseApp
from ..db import audit, catalog, orders as orders_repo
from ..services import delivery
from ..services.money import format_cents
from .components import ConfirmView, safe_edit, safe_respond
from .store_views import TYPE_LABELS
from .theme import COLOR_ACCENT, base_embed, error_embed, ok_embed, panel_embed

log = logging.getLogger("eclipse.ui.seller")


# --------------------------------------------------------------------- painel raiz
class SellerPanelView(discord.ui.View):
    def __init__(self, ec: EclipseApp, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=600)
        self.ec = ec
        self.guild_id = guild_id
        self.user_id = user_id

    @discord.ui.button(label="📦 Produtos", style=discord.ButtonStyle.primary, row=0)
    async def products(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        products = await catalog.list_products(self.ec.db, self.guild_id, include_inactive=True)
        if not products:
            await safe_respond(
                interaction,
                base_embed("Sem produtos", "Crie o primeiro com `/produto criar`.", COLOR_ACCENT),
            )
            return
        view = ProductSelectView(self.ec, self.guild_id, products)
        await safe_edit(interaction, panel_embed("Produtos da loja", "Selecione para gerenciar."), view=view)

    @discord.ui.button(label="🧾 Pedidos", style=discord.ButtonStyle.secondary, row=0)
    async def orders(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        rows = await orders_repo.list_orders(self.ec.db, guild_id=self.guild_id, limit=25)
        if not rows:
            await safe_respond(interaction, base_embed("Sem pedidos ainda.", None, COLOR_ACCENT))
            return
        view = OrdersListView(self.ec, rows)
        await safe_edit(interaction, panel_embed("Pedidos recentes", "Selecione para detalhar."), view=view)

    @discord.ui.button(label="🔄 Atualizar", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        products = await catalog.list_products(self.ec.db, self.guild_id, include_inactive=True)
        active = sum(1 for p in products if int(p["active"]))
        orders = await orders_repo.list_orders(self.ec.db, guild_id=self.guild_id, limit=1)
        embed = panel_embed(
            "Painel do vendedor",
            f"Produtos: **{len(products)}** ({active} ativos)\n"
            f"Pedidos registrados: **{'sim' if orders else 'nenhum'}**\n"
            "Use os botões para gerenciar. Toda ação é registrada em auditoria.",
        )
        await safe_edit(interaction, embed, view=self)


class ProductSelectView(discord.ui.View):
    def __init__(self, ec: EclipseApp, guild_id: int, products: list[Row]) -> None:
        super().__init__(timeout=600)
        self.ec = ec
        self.guild_id = guild_id
        options = [
            discord.SelectOption(
                label=f"#{p['id']} {p['name']}"[:100],
                value=str(p["id"]),
                description=("ativo • " if int(p["active"]) else "inativo • ")
                + format_cents(int(p["price_cents"]), p["currency"]),
            )
            for p in products[:25]
        ]
        self.select = discord.ui.Select(placeholder="Escolha o produto…", options=options)
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        product = await catalog.get_product(self.ec.db, int(self.select.values[0]))
        if product is None:
            await safe_respond(interaction, error_embed("Produto não encontrado."))
            return
        view = ProductManageView(self.ec, product)
        await safe_edit(interaction, _manage_embed(product), view=view)


def _manage_embed(product: Row) -> discord.Embed:
    available = catalog.available_stock(product)
    embed = panel_embed(f"Gerenciar: {product['name']}", product["description"][:300] or None)
    embed.add_field(name="Preço", value=format_cents(int(product["price_cents"]), product["currency"]), inline=True)
    embed.add_field(name="Estoque", value="Ilimitado" if available is None else f"{available} disp. ({product['stock']} total, {product['reserved_qty']} reserv.)", inline=True)
    embed.add_field(name="Status", value="Ativo" if int(product["active"]) else "Inativo", inline=True)
    embed.add_field(name="Categoria", value=product["category"], inline=True)
    embed.add_field(name="Entrega", value=TYPE_LABELS.get(product["delivery_type"], product["delivery_type"]), inline=True)
    embed.add_field(name="Alerta estoque", value=str(product["low_stock_threshold"] or "-"), inline=True)
    return embed


class ProductManageView(discord.ui.View):
    def __init__(self, ec: EclipseApp, product: Row) -> None:
        super().__init__(timeout=600)
        self.ec = ec
        self.product_id = int(product["id"])

    async def _reload(self, interaction: discord.Interaction) -> None:
        product = await catalog.get_product(self.ec.db, self.product_id)
        if product is None:
            await safe_edit(interaction, error_embed("Produto removido."), view=None)
            return
        await safe_edit(interaction, _manage_embed(product), view=self)

    @discord.ui.button(label="💲 Preço", style=discord.ButtonStyle.primary, row=0)
    async def price(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        await interaction.response.send_modal(PriceModal(self.ec, self.product_id, self))

    @discord.ui.button(label="📦 Estoque", style=discord.ButtonStyle.primary, row=0)
    async def stock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        await interaction.response.send_modal(StockModal(self.ec, self.product_id, self))

    @discord.ui.button(label="📝 Descrição", style=discord.ButtonStyle.secondary, row=0)
    async def description(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        await interaction.response.send_modal(DescriptionModal(self.ec, self.product_id, self))

    @discord.ui.button(label="🚚 Entrega", style=discord.ButtonStyle.secondary, row=0)
    async def delivery_edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        await interaction.response.send_modal(DeliveryModal(self.ec, self.product_id, self))

    @discord.ui.button(label="⏻ Ativar/Inativar", style=discord.ButtonStyle.secondary, row=1)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        product = await catalog.get_product(self.ec.db, self.product_id)
        if product is None:
            return
        new_active = 0 if int(product["active"]) else 1

        async def _do(inner: discord.Interaction) -> None:
            async with self.ec.db.tx() as tx:
                await catalog.update_product(tx, self.product_id, active=new_active)
                await audit.record(
                    tx,
                    guild_id=interaction.guild_id,
                    actor_id=interaction.user.id,
                    action="product.toggle_active",
                    target_type="product",
                    target_id=str(self.product_id),
                    details={"active": bool(new_active)},
                )
            await self.ec.notifier.log(
                interaction.guild_id,
                "Produto ativado/inativado",
                [("Produto", f"#{self.product_id} — {product['name']}"), ("Ativo", "sim" if new_active else "não")],
            )
            await self._reload(inner)

        if new_active == 0:
            view = ConfirmView(
                title="Inativar produto",
                description=f"**{product['name']}** sairá do catálogo imediatamente.",
                confirm_label="Inativar",
                danger=False,
                on_confirm=_do,
                owner_id=interaction.user.id,
            )
            await safe_respond(interaction, view.initial_embed(), view=view)
        else:
            await _do(interaction)

    @discord.ui.button(label="🗑️ Remover", style=discord.ButtonStyle.danger, row=1)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        product = await catalog.get_product(self.ec.db, self.product_id)
        if product is None:
            return

        async def _do(inner: discord.Interaction) -> None:
            async with self.ec.db.tx() as tx:
                await tx.execute("DELETE FROM products WHERE id = ?", (self.product_id,))
                await audit.record(
                    tx,
                    guild_id=interaction.guild_id,
                    actor_id=interaction.user.id,
                    action="product.deleted",
                    target_type="product",
                    target_id=str(self.product_id),
                    details={"name": product["name"]},
                )
            await self.ec.notifier.log(
                interaction.guild_id,
                "🗑️ Produto removido",
                [("Produto", f"#{self.product_id} — {product['name']}"), ("Por", f"<@{interaction.user.id}>")],
            )
            await safe_edit(inner, ok_embed("Produto removido do catálogo."), view=None)

        view = ConfirmView(
            title="Remover produto",
            description=f"**{product['name']}** será excluído do catálogo.\nPedidos antigos permanecem no histórico.",
            confirm_label="Remover definitivamente",
            on_confirm=_do,
            owner_id=interaction.user.id,
        )
        await safe_respond(interaction, view.initial_embed(), view=view)

    @discord.ui.button(label="↩️ Voltar", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        products = await catalog.list_products(self.ec.db, interaction.guild_id or 0, include_inactive=True)
        view = ProductSelectView(self.ec, interaction.guild_id or 0, products)
        await safe_edit(interaction, panel_embed("Produtos da loja", "Selecione para gerenciar."), view=view)


# ------------------------------------------------------------------------ modais
class PriceModal(discord.ui.Modal, title="Alterar preço"):
    def __init__(self, ec: EclipseApp, product_id: int, parent: discord.ui.View) -> None:
        super().__init__()
        self.ec = ec
        self.product_id = product_id
        self.parent = parent
        self.price = discord.ui.TextInput(
            label="Novo preço (ex.: 19,90)", style=discord.TextStyle.short, max_length=20
        )
        self.add_item(self.price)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        from ..services.money import MoneyError, parse_cents

        try:
            cents = parse_cents(self.price.value)
        except MoneyError as exc:
            await safe_respond(interaction, error_embed("Preço inválido", str(exc)))
            return
        product = await catalog.get_product(self.ec.db, self.product_id)
        async with self.ec.db.tx() as tx:
            await catalog.update_product(tx, self.product_id, price_cents=cents)
            await audit.record(
                tx,
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                action="product.price_changed",
                target_type="product",
                target_id=str(self.product_id),
                details={"old_cents": int(product["price_cents"]) if product else None, "new_cents": cents},
            )
        await self.ec.notifier.log(
            interaction.guild_id,
            "💲 Preço alterado",
            [
                ("Produto", f"#{self.product_id} — {product['name'] if product else '?'}"),
                ("Novo preço", format_cents(cents, self.ec.config.currency)),
                ("Por", f"<@{interaction.user.id}>"),
            ],
        )
        await safe_respond(interaction, ok_embed("Preço atualizado", format_cents(cents, self.ec.config.currency)))


class StockModal(discord.ui.Modal, title="Ajustar estoque"):
    def __init__(self, ec: EclipseApp, product_id: int, parent: discord.ui.View) -> None:
        super().__init__()
        self.ec = ec
        self.product_id = product_id
        self.parent = parent
        self.operation = discord.ui.TextInput(
            label="Operação: +5, -3 ou =20 (vazio p/ ilimitado)",
            style=discord.TextStyle.short,
            max_length=12,
            required=False,
            placeholder="+5 adiciona, -3 remove, =20 define, 'ilimitado' libera",
        )
        self.add_item(self.operation)
        self.threshold = discord.ui.TextInput(
            label="Alerta de estoque baixo (opcional)",
            style=discord.TextStyle.short,
            max_length=6,
            required=False,
            placeholder="ex.: 3",
        )
        self.add_item(self.threshold)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        raw = (self.operation.value or "").strip().lower()
        product = await catalog.get_product(self.ec.db, self.product_id)
        if product is None:
            await safe_respond(interaction, error_embed("Produto não encontrado."))
            return
        threshold = None
        if (self.threshold.value or "").strip():
            if not (self.threshold.value or "").strip().isdigit():
                await safe_respond(interaction, error_embed("Alerta inválido", "Use um número inteiro."))
                return
            threshold = int((self.threshold.value or "").strip())

        async with self.ec.db.tx() as tx:
            if raw in ("", "ilimitado", "inf"):
                await catalog.set_stock(tx, self.product_id, None)
                detail = "estoque ilimitado"
            elif raw.startswith("="):
                value = int(raw[1:])
                await catalog.set_stock(tx, self.product_id, value)
                detail = f"estoque definido para {value}"
            elif raw.startswith("+") or raw.startswith("-"):
                updated = await catalog.adjust_stock(tx, self.product_id, int(raw))
                detail = f"estoque agora: {updated['stock'] if updated else '?'}"
            else:
                await safe_respond(interaction, error_embed("Operação inválida", "Use +N, -N, =N ou 'ilimitado'."))
                return
            if threshold is not None:
                await catalog.update_product(tx, self.product_id, low_stock_threshold=threshold)
            await audit.record(
                tx,
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                action="product.stock_changed",
                target_type="product",
                target_id=str(self.product_id),
                details={"operation": raw or "ilimitado", "threshold": threshold},
            )
        await self.ec.notifier.log(
            interaction.guild_id,
            "📦 Estoque ajustado",
            [("Produto", f"#{self.product_id} — {product['name']}"), ("Mudança", detail), ("Por", f"<@{interaction.user.id}>")],
        )
        await safe_respond(interaction, ok_embed("Estoque atualizado", detail))


class DescriptionModal(discord.ui.Modal, title="Editar descrição"):
    def __init__(self, ec: EclipseApp, product_id: int, parent: discord.ui.View) -> None:
        super().__init__()
        self.ec = ec
        self.product_id = product_id
        self.parent = parent
        self.description = discord.ui.TextInput(
            label="Descrição exibida no catálogo",
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        async with self.ec.db.tx() as tx:
            await catalog.update_product(tx, self.product_id, description=self.description.value)
            await audit.record(
                tx,
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                action="product.description_changed",
                target_type="product",
                target_id=str(self.product_id),
            )
        await safe_respond(interaction, ok_embed("Descrição atualizada."))


class DeliveryModal(discord.ui.Modal, title="Editar conteúdo de entrega"):
    def __init__(self, ec: EclipseApp, product_id: int, parent: discord.ui.View) -> None:
        super().__init__()
        self.ec = ec
        self.product_id = product_id
        self.parent = parent
        self.dtype = discord.ui.TextInput(
            label="Tipo: text | code | link | custom",
            style=discord.TextStyle.short,
            max_length=10,
            placeholder="para arquivo use /produto arquivo",
        )
        self.add_item(self.dtype)
        self.payload = discord.ui.TextInput(
            label="Conteúdo entregue ao cliente",
            style=discord.TextStyle.paragraph,
            max_length=3800,
        )
        self.add_item(self.payload)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        dtype = (self.dtype.value or "").strip().lower()
        if dtype not in ("text", "code", "link", "custom"):
            await safe_respond(interaction, error_embed("Tipo inválido", "Use text, code, link ou custom."))
            return
        async with self.ec.db.tx() as tx:
            await catalog.update_product(
                tx,
                self.product_id,
                delivery_type=dtype,
                delivery_payload=self.payload.value,
                delivery_file_name=None,
                delivery_file_path=None,
            )
            await audit.record(
                tx,
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                action="product.delivery_changed",
                target_type="product",
                target_id=str(self.product_id),
                details={"type": dtype},
            )
        await self.ec.notifier.log(
            interaction.guild_id,
            "🚚 Entrega reconfigurada",
            [("Produto", f"#{self.product_id}"), ("Tipo", dtype), ("Por", f"<@{interaction.user.id}>")],
        )
        await safe_respond(interaction, ok_embed("Entrega atualizada.", f"Tipo: {dtype}"))


# ----------------------------------------------------------------------- pedidos
class OrdersListView(discord.ui.View):
    def __init__(self, ec: EclipseApp, rows: list[Row]) -> None:
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
        if not await self.ec.perms.ensure_seller(interaction):
            return
        order = await orders_repo.get_order(self.ec.db, int(self.select.values[0]))
        if order is None:
            await safe_respond(interaction, error_embed("Pedido não encontrado."))
            return
        view = OrderDetailView(self.ec, order)
        await safe_edit(interaction, _order_embed(order), view=view)


def _order_embed(order: Row) -> discord.Embed:
    embed = panel_embed(f"Pedido #{order['id']}", f"**{order['product_name']}**")
    embed.add_field(name="Cliente", value=f"<@{order['buyer_id']}> (`{order['buyer_id']}`)", inline=False)
    embed.add_field(name="Valor", value=format_cents(int(order["price_cents"]), order["currency"]), inline=True)
    embed.add_field(name="Pagamento", value=order["payment_method"], inline=True)
    embed.add_field(name="Status", value=order["status"], inline=True)
    embed.add_field(name="Entrega", value=f"{order['delivery_status']}" + (f" via {order['delivered_via']}" if order["delivered_via"] else ""), inline=True)
    embed.add_field(name="Criado", value=order["created_at"], inline=True)
    embed.add_field(name="Pago em", value=order["paid_at"] or "-", inline=True)
    if order["stripe_session_id"]:
        embed.add_field(name="Sessão Stripe", value=f"`{order['stripe_session_id']}`", inline=False)
    return embed


class OrderDetailView(discord.ui.View):
    def __init__(self, ec: EclipseApp, order: Row) -> None:
        super().__init__(timeout=600)
        self.ec = ec
        self.order_id = int(order["id"])

    @discord.ui.button(label="👁️ Ver entrega", style=discord.ButtonStyle.primary, row=0)
    async def view_delivery(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        order = await orders_repo.get_order(self.ec.db, self.order_id)
        if order is None or order["status"] != "paid":
            await safe_respond(interaction, error_embed("Pedido não pago."))
            return
        await delivery.redeliver(interaction, order)

    @discord.ui.button(label="↩️ Voltar", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        rows = await orders_repo.list_orders(self.ec.db, guild_id=interaction.guild_id, limit=25)
        view = OrdersListView(self.ec, rows)
        await safe_edit(interaction, panel_embed("Pedidos recentes", "Selecione para detalhar."), view=view)

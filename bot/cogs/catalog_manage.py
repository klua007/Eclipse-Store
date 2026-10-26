"""Comandos /produto — criação e manutenção do catálogo (vendedores)."""
from __future__ import annotations

import os
import re
import sqlite3
import uuid
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from ..db import audit, catalog
from ..services.money import MoneyError, format_cents, parse_cents
from ..ui.components import ConfirmView, safe_respond
from ..ui.seller_views import ProductManageView, ProductSelectView
from ..ui.store_views import TYPE_LABELS
from ..ui.theme import COLOR_ACCENT, base_embed, error_embed, ok_embed, panel_embed

MAX_FILE_BYTES = 8 * 1024 * 1024  # 8 MB (limite de anexo sem Nitro)


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80] or "arquivo.bin"


class CatalogCog(commands.Cog):
    produto = app_commands.Group(name="produto", description="Gestão do catálogo (vendedores)")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.ec = bot.app  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ criar
    @produto.command(name="criar", description="Criar produto digital no catálogo")
    @app_commands.describe(
        nome="Nome exibido no catálogo",
        preco="Preço (ex.: 19,90)",
        tipo_entrega="Como o cliente recebe o produto",
        descricao="Descrição pública do produto",
        categoria="Categoria/agrupamento",
        conteudo="Conteúdo entregue (texto, código, link ou instrução)",
        estoque="Unidades disponíveis (vazio = ilimitado)",
        alerta_estoque="Avisar quando o estoque chegar a este número",
        arquivo="Arquivo entregue ao cliente (obrigatório se tipo_entrega=Arquivo)",
    )
    async def criar(
        self,
        interaction: discord.Interaction,
        nome: str,
        preco: str,
        tipo_entrega: app_commands.Choice[str],
        descricao: str = "",
        categoria: str = "Geral",
        conteudo: str | None = None,
        estoque: int | None = None,
        alerta_estoque: int | None = None,
        arquivo: discord.Attachment | None = None,
    ) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            cents = parse_cents(preco)
        except MoneyError as exc:
            await safe_respond(interaction, error_embed("Preço inválido", str(exc)))
            return
        dtype = tipo_entrega.value
        if dtype == "file":
            if arquivo is None:
                await safe_respond(interaction, error_embed("Arquivo obrigatório", "Para entrega tipo Arquivo, anexe o arquivo no comando."))
                return
            if arquivo.size > MAX_FILE_BYTES:
                await safe_respond(interaction, error_embed("Arquivo muito grande", f"Limite: {MAX_FILE_BYTES // (1024*1024)} MB."))
                return
        elif not conteudo or not conteudo.strip():
            await safe_respond(interaction, error_embed("Conteúdo obrigatório", "Informe o conteúdo entregue para este tipo de entrega."))
            return
        if estoque is not None and estoque < 0:
            await safe_respond(interaction, error_embed("Estoque inválido", "Use número >= 0 ou deixe vazio para ilimitado."))
            return

        # arquivo: salva primeiro com nome temporário; renomeia com id do produto
        tmp_path: Path | None = None
        final_path: Path | None = None
        if arquivo is not None:
            tmp_path = self.ec.config.deliverables_dir / f"tmp_{uuid.uuid4().hex}.bin"
            await arquivo.save(tmp_path)
            final_path = tmp_path  # renomeado após criar o produto

        try:
            async with self.ec.db.tx() as tx:
                product_id = await catalog.create_product(
                    tx,
                    guild_id=interaction.guild_id or 0,
                    name=nome.strip()[:100],
                    description=descricao.strip()[:1000],
                    category=categoria.strip()[:60] or "Geral",
                    price_cents=cents,
                    currency=self.ec.config.currency,
                    delivery_type=dtype,
                    delivery_payload=(conteudo or "").strip() or None,
                    delivery_file_name=arquivo.filename if arquivo else None,
                    delivery_file_path=str(final_path) if final_path else None,
                    stock=estoque,
                    low_stock_threshold=alerta_estoque,
                    seller_id=interaction.user.id,
                )
                await audit.record(
                    tx,
                    guild_id=interaction.guild_id,
                    actor_id=interaction.user.id,
                    action="product.created",
                    target_type="product",
                    target_id=str(product_id),
                    details={"name": nome, "price_cents": cents, "delivery": dtype, "stock": estoque},
                )
        except sqlite3.IntegrityError:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            await safe_respond(interaction, error_embed("Nome duplicado", "Já existe um produto com este nome nesta loja."))
            return
        if tmp_path is not None:
            final = self.ec.config.deliverables_dir / f"p{product_id}_{_safe_filename(arquivo.filename)}"  # type: ignore[union-attr]
            os.replace(tmp_path, final)
            async with self.ec.db.tx() as tx:
                await catalog.update_product(tx, product_id, delivery_file_path=str(final))
        product = await catalog.get_product(self.ec.db, product_id)
        await self.ec.notifier.log(
            interaction.guild_id,
            "🆕 Produto criado",
            [
                ("Produto", f"#{product_id} — {nome}"),
                ("Preço", format_cents(cents, self.ec.config.currency)),
                ("Entrega", TYPE_LABELS.get(dtype, dtype)),
                ("Estoque", "ilimitado" if estoque is None else str(estoque)),
                ("Por", f"<@{interaction.user.id}>"),
            ],
        )
        view = ProductManageView(self.ec, product)  # type: ignore[arg-type]
        await safe_respond(interaction, ok_embed(f"Produto #{product_id} criado", "Use os botões para ajustar qualquer campo."), view=view)

    # ------------------------------------------------------------------ editar
    @produto.command(name="editar", description="Abrir painel de edição de um produto")
    async def editar(self, interaction: discord.Interaction) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        products = await catalog.list_products(self.ec.db, interaction.guild_id or 0, include_inactive=True)
        if not products:
            await safe_respond(interaction, base_embed("Sem produtos", "Crie um com `/produto criar`.", COLOR_ACCENT))
            return
        view = ProductSelectView(self.ec, interaction.guild_id or 0, products)
        await safe_respond(interaction, panel_embed("Editar produto", "Selecione o produto."), view=view)

    # ------------------------------------------------------------------ arquivo
    @produto.command(name="arquivo", description="Substituir o arquivo de entrega de um produto")
    @app_commands.describe(produto_id="ID do produto", anexo="Novo arquivo de entrega")
    async def arquivo_cmd(
        self, interaction: discord.Interaction, produto_id: int, anexo: discord.Attachment
    ) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        product = await catalog.get_product(self.ec.db, produto_id)
        if product is None or int(product["guild_id"]) != interaction.guild_id:
            await safe_respond(interaction, error_embed("Produto não encontrado nesta loja."))
            return
        if anexo.size > MAX_FILE_BYTES:
            await safe_respond(interaction, error_embed("Arquivo muito grande", f"Limite: {MAX_FILE_BYTES // (1024*1024)} MB."))
            return
        path = self.ec.config.deliverables_dir / f"p{produto_id}_{_safe_filename(anexo.filename)}"
        await anexo.save(path)
        old_path = product["delivery_file_path"]
        async with self.ec.db.tx() as tx:
            await catalog.update_product(
                tx,
                produto_id,
                delivery_type="file",
                delivery_file_name=anexo.filename,
                delivery_file_path=str(path),
                delivery_payload=None,
            )
            await audit.record(
                tx,
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                action="product.file_replaced",
                target_type="product",
                target_id=str(produto_id),
                details={"file": anexo.filename},
            )
        if old_path and old_path != str(path):
            Path(old_path).unlink(missing_ok=True)
        await self.ec.notifier.log(
            interaction.guild_id,
            "📎 Arquivo de entrega substituído",
            [("Produto", f"#{produto_id} — {product['name']}"), ("Por", f"<@{interaction.user.id}>")],
        )
        await safe_respond(interaction, ok_embed("Arquivo atualizado", f"Entregas futuras usarão **{anexo.filename}**."))

    # ------------------------------------------------------------------ remover
    @produto.command(name="remover", description="Remover produto do catálogo (com confirmação)")
    @app_commands.describe(produto_id="ID do produto a remover")
    async def remover(self, interaction: discord.Interaction, produto_id: int) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        product = await catalog.get_product(self.ec.db, produto_id)
        if product is None or int(product["guild_id"]) != interaction.guild_id:
            await safe_respond(interaction, error_embed("Produto não encontrado nesta loja."))
            return

        async def _do(inner: discord.Interaction) -> None:
            async with self.ec.db.tx() as tx:
                await tx.execute("DELETE FROM products WHERE id = ?", (produto_id,))
                await audit.record(
                    tx,
                    guild_id=interaction.guild_id,
                    actor_id=interaction.user.id,
                    action="product.deleted",
                    target_type="product",
                    target_id=str(produto_id),
                    details={"name": product["name"]},
                )
            await self.ec.notifier.log(
                interaction.guild_id,
                "🗑️ Produto removido",
                [("Produto", f"#{produto_id} — {product['name']}"), ("Por", f"<@{interaction.user.id}>")],
            )
            await safe_respond(inner, ok_embed("Produto removido."))

        view = ConfirmView(
            title="Remover produto",
            description=f"**{product['name']}** sairá do catálogo. Pedidos antigos permanecem auditáveis.",
            confirm_label="Remover definitivamente",
            on_confirm=_do,
            owner_id=interaction.user.id,
        )
        await safe_respond(interaction, view.initial_embed(), view=view)

    # ------------------------------------------------------------------ listar
    @produto.command(name="listar", description="Listar produtos da loja (equipe)")
    async def listar(self, interaction: discord.Interaction) -> None:
        if not await self.ec.perms.ensure_seller(interaction):
            return
        products = await catalog.list_products(self.ec.db, interaction.guild_id or 0, include_inactive=True)
        embed = panel_embed("Catálogo da equipe", None)
        if not products:
            embed.description = "Nenhum produto cadastrado."
        for p in products[:25]:
            available = catalog.available_stock(p)
            embed.add_field(
                name=f"#{p['id']} {p['name']}",
                value=(
                    f"{format_cents(int(p['price_cents']), p['currency'])} • "
                    f"{'ativo' if int(p['active']) else 'INATIVO'} • "
                    f"estoque: {'∞' if available is None else available} • {p['category']}"
                ),
                inline=False,
            )
        await safe_respond(interaction, embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CatalogCog(bot))

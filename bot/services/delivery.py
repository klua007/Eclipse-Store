"""Entrega privada de produtos digitais.

Prioridade de canal privado:
1. Resposta efêmera da própria interação (sempre privada);
2. Cópia por mensagem direta (DM) para o cliente guardar;
3. Se a DM estiver bloqueada, o cliente é avisado e a alternativa privada
   e segura é o comando /meus-pedidos (reexibe a entrega a qualquer momento).

Nada de saldo, códigos, links ou arquivos é enviado em canal público.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

import discord
from aiosqlite import Row

from ..db import orders as orders_repo
from ..db.core import Database
from .money import format_cents

log = logging.getLogger("eclipse.delivery")

TYPE_LABELS = {
    "text": "Texto",
    "code": "Código",
    "link": "Link",
    "custom": "Instrução personalizada",
    "file": "Arquivo",
}


def delivery_text(order: Row) -> str:
    """Monta o texto privado de entrega a partir do snapshot do pedido."""
    dtype = order["delivery_type"]
    payload = order["delivery_payload"] or ""
    header = (
        f"✅ **Compra confirmada — Pedido #{order['id']}**\n"
        f"**Produto:** {order['product_name']}\n"
        f"**Valor:** {format_cents(int(order['price_cents']), order['currency'])}\n"
        f"**Tipo de entrega:** {TYPE_LABELS.get(dtype, dtype)}\n"
    )
    if dtype == "code":
        return f"{header}\n**Seu código/chave:**\n```\n{payload}\n```"
    if dtype == "link":
        return f"{header}\n**Acesse:** <{payload}>"
    if dtype == "file":
        return f"{header}\n**Arquivo:** {order['delivery_file_name'] or 'anexo'} (em anexo)"
    return f"{header}\n{payload}"


def delivery_file(order: Row) -> Optional[discord.File]:
    if order["delivery_type"] != "file" or not order["delivery_file_path"]:
        return None
    path = Path(order["delivery_file_path"])
    if not path.exists():
        log.error("Arquivo de entrega ausente: %s (pedido #%s)", path, order["id"])
        return None
    return discord.File(str(path), filename=order["delivery_file_name"] or path.name)


async def _send_private(
    destination, text: str, file: Optional[discord.File]
) -> bool:
    try:
        await destination.send(content=text, file=file)
        return True
    except (discord.Forbidden, discord.HTTPException) as exc:
        log.info("Entrega privada falhou (%s): %s", type(exc).__name__, exc)
        return False


async def deliver_in_interaction(
    interaction: discord.Interaction, db: Database, order: Row
) -> Tuple[bool, str]:
    """Entrega durante uma interação: efêmero + tentativa de DM.

    Retorna (sucesso, nota sobre a DM).
    """
    text = delivery_text(order)
    file = delivery_file(order)
    via = "ephemeral"
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(content=text, file=file, ephemeral=True)
        else:
            await interaction.followup.send(content=text, file=file, ephemeral=True)
    except discord.HTTPException:
        log.exception("Falha ao responder entrega efêmera do pedido #%s", order["id"])
        await orders_repo.set_delivery(db, int(order["id"]), "failed")
        raise
    # arquivo precisa de uma nova instância para o segundo envio
    dm_file = delivery_file(order)
    note = ""
    if await _send_private(interaction.user, text, dm_file):
        via = "ephemeral+dm"
    else:
        note = (
            "⚠️ Não consegui enviar a DM (mensagens diretas bloqueadas). "
            "Sua entrega continua segura aqui e em `/meus-pedidos`."
        )
    await orders_repo.set_delivery(db, int(order["id"]), "delivered", via=via)
    return True, note


async def deliver_background(bot: discord.Client, db: Database, order: Row) -> Tuple[bool, str]:
    """Entrega fora de interação (pagamento confirmado pelo webhook): via DM."""
    user = bot.get_user(int(order["buyer_id"]))
    if user is None:
        try:
            user = await bot.fetch_user(int(order["buyer_id"]))
        except discord.HTTPException:
            user = None
    if user is None:
        await orders_repo.set_delivery(db, int(order["id"]), "failed")
        return False, "usuário inacessível"
    ok = await _send_private(user, delivery_text(order), delivery_file(order))
    if ok:
        await orders_repo.set_delivery(db, int(order["id"]), "delivered", via="dm")
        return True, "dm"
    await orders_repo.set_delivery(db, int(order["id"]), "dm_blocked")
    return False, "dm_blocked"


async def redeliver(interaction: discord.Interaction, order: Row) -> None:
    """Reexibe a entrega de um pedido pago (usada por /meus-pedidos e staff)."""
    text = delivery_text(order)
    file = delivery_file(order)
    if not interaction.response.is_done():
        await interaction.response.send_message(content=text, file=file, ephemeral=True)
    else:
        await interaction.followup.send(content=text, file=file, ephemeral=True)

"""Fluxo privado de carteira: recarga via Stripe Checkout."""
from __future__ import annotations

import logging

import discord

from ..app import EclipseApp
from ..services import purchase
from ..services.money import MoneyError, format_cents, parse_cents
from .components import safe_respond
from .theme import COLOR_ACCENT, base_embed, error_embed

log = logging.getLogger("eclipse.ui.wallet")


class TopupModal(discord.ui.Modal, title="Adicionar saldo via Stripe"):
    amount = discord.ui.TextInput(
        label="Valor da recarga (ex.: 25,00)",
        style=discord.TextStyle.short,
        max_length=20,
        placeholder="Mínimo R$ 1,00",
    )

    def __init__(self, ec: EclipseApp) -> None:
        super().__init__()
        self.ec = ec

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            cents = parse_cents(self.amount.value)
        except MoneyError as exc:
            await safe_respond(interaction, error_embed("Valor inválido", str(exc)))
            return
        try:
            topup, session = await purchase.start_topup(
                self.ec.db, self.ec.stripe, self.ec.config, interaction.user.id, cents
            )
        except Exception:  # noqa: BLE001 - erro de API Stripe vira mensagem amigável
            log.exception("Falha ao criar sessão de recarga para %s", interaction.user.id)
            await safe_respond(
                interaction,
                error_embed("Não foi possível criar o pagamento", "Tente novamente em instantes."),
            )
            return
        view = discord.ui.View(timeout=900)
        view.add_item(
            discord.ui.Button(label="Pagar no Stripe (ambiente seguro)", url=session.url, style=discord.ButtonStyle.link)
        )
        embed = base_embed(
            f"💳 Recarga de {format_cents(cents, self.ec.config.currency)}",
            "Clique no botão para pagar na página segura do Stripe.\n"
            "**O saldo é creditado automaticamente quando o webhook confirmar o pagamento** — "
            "o retorno da página não confirma nada.\n"
            f"Recarga nº `{topup['id']}`.",
            COLOR_ACCENT,
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

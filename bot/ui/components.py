"""Componentes reutilizáveis: confirmações e helpers de resposta efêmera."""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

import discord

from .theme import COLOR_DANGER, COLOR_PRIMARY, base_embed, error_embed

ConfirmCallback = Callable[[discord.Interaction], Awaitable[None]]


async def safe_respond(
    interaction: discord.Interaction,
    embed: discord.Embed,
    view: Optional[discord.ui.View] = None,
    ephemeral: bool = True,
) -> None:
    """Responde ou faz followup efêmero, sem estourar erro de já respondido."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=ephemeral)
    except discord.HTTPException:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)


async def safe_edit(
    interaction: discord.Interaction,
    embed: discord.Embed,
    view: Optional[discord.ui.View] = None,
) -> None:
    try:
        await interaction.response.edit_message(embed=embed, view=view)
    except discord.HTTPException:
        try:
            await interaction.message.edit(embed=embed, view=view)
        except discord.HTTPException:
            pass


class ConfirmView(discord.ui.View):
    """Confirmação explícita antes de ações importantes (remover, inativar...)."""

    def __init__(
        self,
        *,
        title: str,
        description: str,
        confirm_label: str = "Confirmar",
        danger: bool = True,
        on_confirm: ConfirmCallback,
        owner_id: int,
    ) -> None:
        super().__init__(timeout=120)
        self.on_confirm = on_confirm
        self.owner_id = owner_id
        self.confirm_button.label = confirm_label
        self.confirm_button.style = (
            discord.ButtonStyle.danger if danger else discord.ButtonStyle.primary
        )
        self._embed = base_embed(f"⚠️ {title}", description, COLOR_DANGER if danger else COLOR_PRIMARY)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await safe_respond(interaction, error_embed("Apenas quem iniciou a ação pode confirmá-la."))
            return False
        return True

    @discord.ui.button(label="Confirmar", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        await self.on_confirm(interaction)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        self.stop()
        await safe_edit(interaction, base_embed("Ação cancelada", "Nenhuma alteração foi feita.", COLOR_PRIMARY), view=None)

    def initial_embed(self) -> discord.Embed:
        return self._embed

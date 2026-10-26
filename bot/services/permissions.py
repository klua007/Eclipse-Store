"""Verificação de permissões no SERVIDOR a cada comando/interação.

Hierarquia:
- **Dono da loja**: OWNER_ID (.env), dono da aplicação Discord ou
  ``guild_settings.owner_id`` (transferível via /config dono);
- **Admin**: dono + IDs em ADMIN_IDS + membros com permissão
  ``Administrator`` no servidor (verificada ao vivo, não confiada em UI);
- **Vendedor**: tabela ``sellers`` da guild (gerenciada pelo dono/admin).

Toda view/botão revalida a permissão no callback — botões "ocultos" não são
mecanismo de segurança.
"""
from __future__ import annotations

import logging
from typing import Optional

import discord

from ..config import Config
from ..db import staff
from ..db.core import Database

log = logging.getLogger("eclipse.perms")


class PermissionService:
    def __init__(self, bot: discord.Client, db: Database, config: Config) -> None:
        self.bot = bot
        self.db = db
        self.config = config
        self._app_owner_ids: set[int] = set()

    async def load_app_owner(self) -> None:
        try:
            info = await self.bot.application_info()  # type: ignore[union-attr]
            if info.owner is not None:
                self._app_owner_ids.add(info.owner.id)
            if info.team is not None:
                self._app_owner_ids.add(info.team.owner_id)
                for member in info.team.members:
                    self._app_owner_ids.add(member.id)
        except Exception as exc:  # noqa: BLE001 - falha de rede/API não derruba o bot
            log.warning("Não foi possível carregar dono da aplicação: %s", exc)

    # ------------------------------------------------------------------ consultas
    async def is_owner(self, user_id: int, guild: Optional[discord.Guild] = None) -> bool:
        if user_id in self.config.owner_ids or user_id in self._app_owner_ids:
            return True
        if guild is not None:
            settings = await staff.get_settings(self.db, guild.id)
            if settings["owner_id"] and int(settings["owner_id"]) == user_id:
                return True
        return False

    async def member(self, guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return None
        return member

    async def has_administrator(self, guild: discord.Guild, user_id: int) -> bool:
        """Checagem viva da permissão Administrator no servidor."""
        member = await self.member(guild, user_id)
        return bool(member and member.guild_permissions.administrator)

    async def is_admin(self, user_id: int, guild: Optional[discord.Guild] = None) -> bool:
        if user_id in self.config.admin_ids:
            return True
        if await self.is_owner(user_id, guild):
            return True
        if guild is not None and await self.has_administrator(guild, user_id):
            return True
        return False

    async def is_seller(self, user_id: int, guild: discord.Guild) -> bool:
        if await self.is_admin(user_id, guild):
            return True
        return await staff.is_seller(self.db, guild.id, user_id)

    # -------------------------------------------------------------------- guards
    async def ensure_seller(self, interaction: discord.Interaction) -> bool:
        """Guard para callbacks/comandos de vendedor. Responde efêmero se negado."""
        guild = interaction.guild
        if guild is None:
            await _deny(interaction, "Use este comando dentro do servidor da loja.")
            return False
        if await self.is_seller(interaction.user.id, guild):
            return True
        await _deny(
            interaction,
            "Você não é vendedor autorizado nesta loja. Peça ao dono para usar `/vendedor adicionar`.",
        )
        return False

    async def ensure_admin(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if guild is None:
            await _deny(interaction, "Use este comando dentro do servidor da loja.")
            return False
        if await self.is_admin(interaction.user.id, guild):
            return True
        await _deny(
            interaction,
            "Ação restrita ao dono da loja ou administradores do servidor.",
        )
        return False


async def _deny(interaction: discord.Interaction, message: str) -> None:
    from ..ui.theme import error_embed

    embed = error_embed("Permissão negada", message)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:  # pragma: no cover
        log.warning("Não foi possível enviar negativa de permissão.")

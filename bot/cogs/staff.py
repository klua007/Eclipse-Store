"""Comandos /vendedor e /vendedores — gestão da equipe (dono/admin)."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..db import audit, staff
from ..ui.components import safe_respond
from ..ui.theme import COLOR_ACCENT, base_embed, ok_embed, panel_embed


class StaffCog(commands.Cog):
    vendedor = app_commands.Group(name="vendedor", description="Gerenciar vendedores autorizados")
    vendedores = app_commands.Group(name="vendedores", description="Consultar equipe")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.ec = bot.app  # type: ignore[attr-defined]

    @vendedor.command(name="adicionar", description="Autorizar um membro como vendedor")
    @app_commands.describe(usuario="Membro que poderá gerenciar produtos e pedidos")
    async def adicionar(self, interaction: discord.Interaction, usuario: discord.Member) -> None:
        if not await self.ec.perms.ensure_admin(interaction):
            return
        async with self.ec.db.tx() as tx:
            added = await staff.add_seller(tx, interaction.guild_id or 0, usuario.id, interaction.user.id)
            await audit.record(
                tx,
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                action="seller.added" if added else "seller.added_dup",
                target_type="user",
                target_id=str(usuario.id),
            )
        await self.ec.notifier.log(
            interaction.guild_id,
            "👥 Vendedor adicionado",
            [
                ("Usuário", f"{usuario.mention} (`{usuario.id}`)"),
                ("Por", f"<@{interaction.user.id}>"),
            ],
        )
        if added:
            await safe_respond(
                interaction,
                ok_embed("Vendedor autorizado", f"{usuario.mention} agora usa `/painel vendedor`."),
            )
        else:
            await safe_respond(interaction, base_embed("Já era vendedor", usuario.mention, COLOR_ACCENT))

    @vendedor.command(name="remover", description="Revogar o acesso de um vendedor")
    @app_commands.describe(usuario="Membro que perderá o acesso")
    async def remover(self, interaction: discord.Interaction, usuario: discord.Member) -> None:
        if not await self.ec.perms.ensure_admin(interaction):
            return
        async with self.ec.db.tx() as tx:
            removed = await staff.remove_seller(tx, interaction.guild_id or 0, usuario.id)
            await audit.record(
                tx,
                guild_id=interaction.guild_id,
                actor_id=interaction.user.id,
                action="seller.removed" if removed else "seller.removed_missing",
                target_type="user",
                target_id=str(usuario.id),
            )
        await self.ec.notifier.log(
            interaction.guild_id,
            "👥 Vendedor removido",
            [("Usuário", f"{usuario.mention} (`{usuario.id}`)"), ("Por", f"<@{interaction.user.id}>")],
        )
        if removed:
            await safe_respond(interaction, ok_embed("Acesso revogado", f"{usuario.mention} não é mais vendedor."))
        else:
            await safe_respond(interaction, base_embed("Não era vendedor", usuario.mention, COLOR_ACCENT))

    @vendedores.command(name="listar", description="Listar vendedores autorizados da loja")
    async def listar(self, interaction: discord.Interaction) -> None:
        if not await self.ec.perms.ensure_admin(interaction):
            return
        rows = await staff.list_sellers(self.ec.db, interaction.guild_id or 0)
        settings = await staff.get_settings(self.ec.db, interaction.guild_id or 0)
        embed = panel_embed("Equipe da Eclipse Store", None)
        owner_id = settings["owner_id"] or (self.ec.config.owner_ids[0] if self.ec.config.owner_ids else None)
        embed.add_field(name="👑 Dono da loja", value=f"<@{owner_id}>" if owner_id else "-", inline=False)
        if not rows:
            embed.add_field(name="Vendedores", value="Nenhum cadastrado.", inline=False)
        for row in rows[:20]:
            embed.add_field(
                name=f"<@{row['user_id']}>",
                value=f"desde {row['added_at']} • por <@{row['added_by']}>",
                inline=True,
            )
        await safe_respond(interaction, embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StaffCog(bot))

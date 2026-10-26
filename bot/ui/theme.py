"""Identidade visual Eclipse Store dentro do Discord.

Paleta dark/cyber derivada do logotipo oficial (branding/avatar.png):
fundo preto/grafite com acentos violeta e ciano. Embeds usam violeta como
cor primária, ciano como acento informativo e cores semânticas para
sucesso/erro — mantendo contraste e legibilidade.
"""
from __future__ import annotations

import discord

# Paleta oficial (ver docs/IDENTIDADE_VISUAL.md)
COLOR_PRIMARY = 0x8B5CF6   # violeta (eclipse)
COLOR_ACCENT = 0x22D3EE    # ciano (borda luminosa)
COLOR_SUCCESS = 0x22C55E
COLOR_DANGER = 0xF43F5E
COLOR_WARN = 0xF59E0B
COLOR_GRAPHITE = 0x101014  # grafite (menus/painéis)

BRAND_FOOTER = "Eclipse Store ◐ loja digital segura"


def base_embed(
    title: str | None = None,
    description: str | None = None,
    color: int = COLOR_PRIMARY,
) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text=BRAND_FOOTER)
    return embed


def ok_embed(title: str, description: str | None = None) -> discord.Embed:
    return base_embed(f"✅ {title}", description, COLOR_SUCCESS)


def error_embed(title: str, description: str | None = None) -> discord.Embed:
    return base_embed(f"⛔ {title}", description, COLOR_DANGER)


def info_embed(title: str, description: str | None = None) -> discord.Embed:
    return base_embed(title, description, COLOR_ACCENT)


def panel_embed(title: str, description: str | None = None) -> discord.Embed:
    """Embed de painel (grafite) para interfaces de gerenciamento."""
    return base_embed(f"🛠️ {title}", description, COLOR_GRAPHITE)

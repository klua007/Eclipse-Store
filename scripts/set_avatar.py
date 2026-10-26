#!/usr/bin/env python3
"""Aplica a identidade visual: define o avatar do bot a partir de branding/avatar.png.

Uso:
    DISCORD_TOKEN=... python scripts/set_avatar.py [--imagem branding/avatar.png]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import discord


async def run(token: str, image: Path) -> None:
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        assert client.user is not None
        data = image.read_bytes()
        await client.user.edit(avatar=data)
        print(f"[avatar] avatar atualizado para {client.user} usando {image}")
        await client.close()

    await client.start(token)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--imagem", default="branding/avatar.png")
    args = parser.parse_args()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("[avatar] defina DISCORD_TOKEN no ambiente.", file=sys.stderr)
        return 2
    image = Path(args.imagem)
    if not image.exists():
        print(f"[avatar] imagem não encontrada: {image}", file=sys.stderr)
        return 1
    asyncio.run(run(token, image))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

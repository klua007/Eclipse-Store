"""Smoke test: o bot inicializa (banco, migrações, webhook, cogs) sem rede Discord."""
from __future__ import annotations

import pytest

from bot.config import Config
from bot.main import EclipseBot


async def _noop_sync() -> None:  # atribuído à instância (sem bind), sem rede Discord
    return None


@pytest.mark.parametrize("port", [8996])
async def test_bot_startup_and_command_tree(tmp_path, port):
    config = Config(
        discord_token="token-fake",
        stripe_secret_key="sk_test_fake",
        stripe_webhook_secret="whsec_fake",
        owner_ids=[111],
        database_path=tmp_path / "smoke.db",
        deliverables_dir=tmp_path / "deliv",
        backup_dir=tmp_path / "backups",
        currency="BRL",
        webhook_port=port,
    )
    bot = EclipseBot(config)
    bot._sync_commands = _noop_sync  # evita chamada de rede Discord no smoke test
    await bot.setup_hook()
    try:
        top_level = {c.qualified_name for c in bot.tree.get_commands()}
        expected = {
            "loja", "carteira", "produto", "pedidos", "meus-pedidos",
            "vendedor", "vendedores", "painel", "ajuste", "config", "ajuda",
        }
        assert expected.issubset(top_level), f"faltando: {expected - top_level}"

        # subcomandos principais
        subs = {}
        for cmd in bot.tree.get_commands():
            group_cmds = getattr(cmd, "commands", set())
            subs[cmd.qualified_name] = {g.name for g in group_cmds}
        assert {"saldo", "historico", "adicionar"} <= subs["carteira"]
        assert {"criar", "editar", "remover", "listar", "arquivo"} <= subs["produto"]
        assert {"adicionar", "remover"} <= subs["vendedor"]
        assert {"listar"} <= subs["vendedores"]
        assert {"vendedor", "admin"} <= subs["painel"]
        assert {"saldo"} <= subs["ajuste"]
        assert {"logs", "dono", "ver"} <= subs["config"]
        assert {"listar", "ver"} <= subs["pedidos"]

        # servidor de webhook ativo
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/healthz") as resp:
                assert resp.status == 200
    finally:
        await bot.close()

"""Carregamento e validação da configuração via variáveis de ambiente.

Nenhum segredo possui valor padrão: se faltar variável obrigatória o bot
falha rápido com mensagem clara indicando o que configurar.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:  # python-dotenv é opcional em produção (variáveis podem vir do sistema)
    from dotenv import load_dotenv as _load_dotenv
except ImportError:  # pragma: no cover
    _load_dotenv = None


class ConfigError(Exception):
    """Erro de configuração (variável ausente ou inválida)."""


def _env(key: str) -> Optional[str]:
    value = os.getenv(key)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _required(key: str) -> str:
    value = _env(key)
    if value is None:
        raise ConfigError(
            f"Variável de ambiente obrigatória ausente: {key}. "
            "Copie .env.example para .env e preencha."
        )
    return value


def _int(key: str, default: Optional[int] = None) -> Optional[int]:
    value = _env(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"Variável {key} deve ser inteira, recebeu: {value!r}") from exc


def _id_list(key: str) -> list[int]:
    value = _env(key)
    if not value:
        return []
    ids: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise ConfigError(f"Variável {key} deve conter apenas IDs numéricos separados por vírgula.")
        ids.append(int(part))
    return ids


@dataclass
class Config:
    """Configuração imutável da aplicação."""

    discord_token: str
    stripe_secret_key: str
    stripe_webhook_secret: str
    owner_ids: list[int] = field(default_factory=list)
    admin_ids: list[int] = field(default_factory=list)
    dev_guild_id: Optional[int] = None
    database_path: Path = Path("data/eclipse.db")
    deliverables_dir: Path = Path("data/deliverables")
    backup_dir: Path = Path("data/backups")
    backup_keep: int = 14
    currency: str = "BRL"
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8927
    public_base_url: Optional[str] = None
    reservation_ttl_minutes: int = 30
    log_channel_id: Optional[int] = None

    # ------------------------------------------------------------------ URLs
    @property
    def base_url(self) -> str:
        """Base pública do servidor web embutido (webhooks e páginas)."""
        if self.public_base_url:
            return self.public_base_url.rstrip("/")
        # Fallback apenas para desenvolvimento local (Stripe CLI consegue alcançar).
        return f"http://127.0.0.1:{self.webhook_port}"

    @property
    def webhook_url(self) -> str:
        return f"{self.base_url}/stripe/webhook"

    @property
    def success_url(self) -> str:
        return f"{self.base_url}/sucesso"

    @property
    def cancel_url(self) -> str:
        return f"{self.base_url}/cancelado"

    # ------------------------------------------------------------- Diretórios
    def ensure_dirs(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.deliverables_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)


def load_config(env_file: Optional[str] = None) -> Config:
    """Lê o .env (se existir) e monta o objeto de configuração validado."""
    if _load_dotenv is not None:
        _load_dotenv(env_file or ".env", override=False)

    owner_ids = _id_list("OWNER_ID")
    if not owner_ids:
        raise ConfigError(
            "Defina OWNER_ID com o ID numérico do dono da loja no .env "
            "(ou o bot usará apenas o dono da aplicação Discord)."
        )

    cfg = Config(
        discord_token=_required("DISCORD_TOKEN"),
        stripe_secret_key=_required("STRIPE_SECRET_KEY"),
        stripe_webhook_secret=_required("STRIPE_WEBHOOK_SECRET"),
        owner_ids=owner_ids,
        admin_ids=_id_list("ADMIN_IDS"),
        dev_guild_id=_int("DEV_GUILD_ID"),
        database_path=Path(_env("DATABASE_PATH") or "data/eclipse.db"),
        deliverables_dir=Path(_env("DELIVERABLES_DIR") or "data/deliverables"),
        backup_dir=Path(_env("BACKUP_DIR") or "data/backups"),
        backup_keep=_int("BACKUP_KEEP", 14) or 14,
        currency=(_env("CURRENCY") or "BRL").upper(),
        webhook_host=_env("STRIPE_WEBHOOK_HOST") or "0.0.0.0",
        webhook_port=_int("STRIPE_WEBHOOK_PORT", 8927) or 8927,
        public_base_url=_env("PUBLIC_BASE_URL"),
        reservation_ttl_minutes=_int("RESERVATION_TTL_MINUTES", 30) or 30,
        log_channel_id=_int("LOG_CHANNEL_ID"),
    )
    cfg.ensure_dirs()
    return cfg

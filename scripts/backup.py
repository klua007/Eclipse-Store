#!/usr/bin/env python3
"""Backup seguro do Eclipse Store (com o bot EM EXECUÇÃO, se preciso).

Usa a API de backup online do SQLite (consistente mesmo com WAL ativo) e,
opcionalmente, empacota os arquivos de entrega (deliverables).

Uso:
    python scripts/backup.py                       # usa variáveis do .env
    python scripts/backup.py --db data/eclipse.db --dest data/backups --keep 14
    python scripts/backup.py --include-files       # inclui deliverables no pacote

Agende (cron) exemplo: 0 3 * * * cd /opt/eclipse-store && python3 scripts/backup.py
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _env(key: str, default: str) -> str:
    try:  # reaproveita o .env sem dependência externa
        from bot.config import _env as read  # type: ignore[attr-defined]

        return read(key) or default
    except Exception:  # pragma: no cover
        return os.getenv(key, default)


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup do banco Eclipse Store")
    parser.add_argument("--db", default=_env("DATABASE_PATH", "data/eclipse.db"))
    parser.add_argument("--dest", default=_env("BACKUP_DIR", "data/backups"))
    parser.add_argument("--keep", type=int, default=int(_env("BACKUP_KEEP", "14")))
    parser.add_argument(
        "--include-files",
        action="store_true",
        help="Inclui o diretório de entregáveis (DELIVERABLES_DIR) em um .tar.gz",
    )
    parser.add_argument("--deliverables", default=_env("DELIVERABLES_DIR", "data/deliverables"))
    args = parser.parse_args()

    db_path = Path(args.db)
    dest_dir = Path(args.dest)
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        print(f"[backup] banco não encontrado: {db_path}", file=sys.stderr)
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = dest_dir / f"eclipse-{stamp}.db"

    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    target = sqlite3.connect(backup_path)
    with target:
        source.backup(target)
    target.close()
    source.close()

    # verificação de integridade do backup
    check = sqlite3.connect(backup_path)
    integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
    check.close()
    if integrity != "ok":
        print(f"[backup] FALHA de integridade em {backup_path}", file=sys.stderr)
        return 1

    print(f"[backup] banco ok: {backup_path} ({backup_path.stat().st_size} bytes)")

    if args.include_files:
        files_path = dest_dir / f"eclipse-files-{stamp}.tar.gz"
        with tarfile.open(files_path, "w:gz") as tar:
            tar.add(args.deliverables, arcname="deliverables")
        print(f"[backup] arquivos ok: {files_path}")

    # retenção: mantém os N mais recentes de cada padrão
    for pattern in ("eclipse-*.db", "eclipse-files-*.tar.gz"):
        files = sorted(dest_dir.glob(pattern))
        for old in files[: max(len(files) - args.keep, 0)]:
            old.unlink()
            print(f"[backup] removido antigo: {old.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

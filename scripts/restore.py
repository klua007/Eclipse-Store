#!/usr/bin/env python3
"""Restauração de um backup do Eclipse Store.

PROCEDIMENTO SEGURO:
1. Pare o bot (Ctrl+C ou `systemctl stop eclipse-store`);
2. Execute este script apontando para o backup desejado;
3. Suba o bot novamente.

O script valida a integridade do backup antes de sobrescrever o banco atual
e preserva o banco substituído como `.pre-restore` para rollback.

Uso:
    python scripts/restore.py --backup data/backups/eclipse-20260101-030000.db
    python scripts/restore.py --backup <arquivo> --db data/eclipse.db
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Restaura backup do banco Eclipse Store")
    parser.add_argument("--backup", required=True, help="Arquivo .db de backup")
    parser.add_argument("--db", default="data/eclipse.db", help="Banco de destino")
    parser.add_argument(
        "--yes", action="store_true", help="Não pedir confirmação interativa"
    )
    args = parser.parse_args()

    backup = Path(args.backup)
    db_path = Path(args.db)
    if not backup.exists():
        print(f"[restore] backup não encontrado: {backup}", file=sys.stderr)
        return 1

    check = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
    tables = [r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    check.close()
    if integrity != "ok":
        print("[restore] backup CORROMPIDO — abortando.", file=sys.stderr)
        return 1
    if "wallets" not in tables or "orders" not in tables:
        print("[restore] backup não parece ser do Eclipse Store — abortando.", file=sys.stderr)
        return 1
    print(f"[restore] backup íntegro com {len(tables)} tabelas.")

    if not args.yes:
        answer = input(f"Sobrescrever {db_path} com {backup}? [y/N] ").strip().lower()
        if answer != "y":
            print("[restore] cancelado.")
            return 0

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        preserved = db_path.with_suffix(db_path.suffix + ".pre-restore")
        shutil.copy2(db_path, preserved)
        print(f"[restore] banco atual preservado em {preserved}")
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            side.unlink()

    source = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    target = sqlite3.connect(db_path)
    with target:
        source.backup(target)
    target.close()
    source.close()
    print(f"[restore] concluído: {db_path}")
    print("[restore] se houver entregáveis em backup (.tar.gz), extraia sobre DELIVERABLES_DIR.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

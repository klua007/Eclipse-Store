# Backup e recuperação — Eclipse Store

Os dados vivem em dois lugares:

1. **Banco SQLite** (`DATABASE_PATH`, padrão `data/eclipse.db`) — carteiras,
   ledger, topups, produtos, pedidos, reservas, equipe, auditoria, eventos;
2. **Entregáveis** (`DELIVERABLES_DIR`, padrão `data/deliverables`) — arquivos
   de produtos vendidos (chaves em arquivo, ebooks, etc.).

## Backup automático (recomendado)

```bash
# todos os dias às 03:00, mantém 14 gerações do banco + arquivos
0 3 * * * cd /opt/eclipse-store && .venv/bin/python scripts/backup.py --include-files >> data/backups/backup.log 2>&1
```

O script:

- usa a **API de backup online do SQLite** (consistente mesmo com o bot
  rodando e WAL ativo);
- roda `PRAGMA integrity_check` no backup gerado e **falha alto** se corromper;
- aplica retenção (`BACKUP_KEEP`, padrão 14) para `.db` e `.tar.gz`;
- com `--include-files`, empacota `DELIVERABLES_DIR` em
  `eclipse-files-<timestamp>.tar.gz`.

> Guarde cópias **fora da máquina** (object storage, outro disco). Backup que
> mora no mesmo disco não protege contra perda total.

## Backup manual

```bash
python scripts/backup.py                          # só o banco
python scripts/backup.py --include-files          # banco + entregáveis
python scripts/backup.py --db data/eclipse.db --dest /mnt/externo --keep 30
```

## Restauração (recuperação de desastres)

1. **Pare o bot** (`systemctl stop eclipse-store` ou Ctrl+C);
2. Restaure o banco:

   ```bash
   python scripts/restore.py --backup data/backups/eclipse-AAAAMMDD-HHMMSS.db
   ```

   O script valida integridade e tabelas antes de sobrescrever e preserva o
   banco substituído como `eclipse.db.pre-restore` (rollback imediato);
3. Se houver `eclipse-files-*.tar.gz`, extraia sobre `DELIVERABLES_DIR`:

   ```bash
   tar -xzf data/backups/eclipse-files-AAAAMMDD-HHMMSS.tar.gz -C data/
   ```

4. Suba o bot e confira `/painel admin → Transações` e `/config ver`.

## Teste de restauração (drill)

Faça periodicamente, em máquina separada:

```bash
python scripts/restore.py --backup <arquivo> --db /tmp/drill.db --yes
sqlite3 /tmp/drill.db "SELECT COUNT(*) FROM wallets; SELECT COUNT(*) FROM orders;"
```

## O que NÃO entra no backup

- `.env` (segredos): guarde em cofre de senhas/secrets manager, não em backup
  de código;
- logs operacionais (o canal de logs do Discord permanece como trilha extra).

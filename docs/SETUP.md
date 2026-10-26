# Guia de instalação e configuração — Eclipse Store

Este guia leva o bot do zero ao production-ready, em **modo de teste** primeiro
e produção depois. Nenhum token/ID é inventado aqui: tudo que você precisa
criar está listado em [`CHECKLIST_EXTERNO.md`](CHECKLIST_EXTERNO.md).

---

## 1. Dependências

Requisitos: Python **3.10+** e acesso à internet.

```bash
git clone <seu-repo>/Eclipse-Store && cd Eclipse-Store
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt      # produção
pip install -r requirements-dev.txt  # + pytest (recomendado)
cp .env.example .env
```

## 2. Aplicação no Discord (Developer Portal)

1. <https://discord.com/developers/applications> → **New Application** → nome
   `Eclipse Store`.
2. **Bot**:
   - `Reset Token` → copie o token para `DISCORD_TOKEN` no `.env`;
   - **Privileged Gateway Intents**: ative **Server Members Intent**
     (usado para checar permissões `Administrator` ao vivo). Nenhuma outra
     intent privilegiada é necessária;
   - Em **Authorization Flow / avatar**: use `branding/avatar.png`
     (ou rode `python scripts/set_avatar.py` com o bot offline/online).
3. **OAuth2 → URL Generator** (ou *Installation*):
   - scopes: `bot`, `applications.commands`;
   - permissões mínimas: `View Channels`, `Send Messages`, `Embed Links`,
     `Attach Files`, `Use Slash Commands`, `Send Messages in Threads`;
   - convide o bot ao seu servidor com a URL gerada.
4. Anote o **ID do servidor** (Mode Desenvolvedor → clique com botão direito no
   servidor) para `DEV_GUILD_ID` e o **seu ID numérico** para `OWNER_ID`.

## 3. Conta Stripe e chaves

1. Crie a conta em <https://dashboard.stripe.com>.
2. **Modo de teste**: use as chaves de teste (`sk_test_...`) em
   `STRIPE_SECRET_KEY`. Cartões de teste: `4242 4242 4242 4242` (sucesso) e
   `4000 0000 0000 0002` (recusado).
3. Produção: chave `sk_live_...` (veja §6).

## 4. Webhook do Stripe (onde o pagamento é confirmado)

O bot sobe um servidor HTTP próprio (aiohttp) na porta `STRIPE_WEBHOOK_PORT`
(padrao `8927`), ouvindo em `0.0.0.0`, rota **`POST /stripe/webhook`**.

### 4.1 Modo de teste (Stripe CLI — recomendado)

```bash
stripe login
stripe listen --forward-to localhost:8927/stripe/webhook
# a CLI imprime: "Ready! Your webhook signing secret is whsec_xxx"
```

Copie o `whsec_...` para `STRIPE_WEBHOOK_SECRET` no `.env` e inicie o bot:

```bash
python -m bot
```

Com o bot rodando, dispare eventos de teste:

```bash
stripe trigger checkout.session.completed   # opcional; o fluxo real é pelo /carteira adicionar
curl http://127.0.0.1:8927/healthz          # deve responder {"status":"ok"}
```

### 4.2 Produção (Dashboard Stripe)

1. **Developers → Webhooks → Add endpoint**;
2. URL: `https://SEU_DOMINIO/stripe/webhook` (veja proxy reverso abaixo);
3. Eventos a assinar (mínimos):
   - `checkout.session.completed`
   - `checkout.session.expired`
   - `checkout.session.async_payment_succeeded`
   - `checkout.session.async_payment_failed`
   - `charge.refunded`
   - `charge.dispute.created`
   - `charge.dispute.closed`
4. Copie o **Signing secret** (`whsec_...`) para `STRIPE_WEBHOOK_SECRET`.

### 4.3 Proxy reverso (TLS obrigatório em produção)

Exemplo com nginx + certbot:

```nginx
server {
    listen 443 ssl http2;
    server_name loja.seudominio.com;
    ssl_certificate     /etc/letsencrypt/live/loja.seudominio.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/loja.seudominio.com/privkey.pem;
    location / {
        proxy_pass http://127.0.0.1:8927;
        proxy_set_header Host $host;
    }
}
```

Defina `PUBLIC_BASE_URL=https://loja.seudominio.com` no `.env` — é essa base
que gera a URL do webhook e as páginas `/sucesso` e `/cancelado` do Checkout.

## 5. Publicação dos comandos de barra

- Com `DEV_GUILD_ID` preenchido: os comandos são publicados **na hora**, apenas
  naquele servidor (ideal para desenvolvimento).
- Com `DEV_GUILD_ID` vazio: publicação **global** na primeira inicialização
  (a propagação pode levar até 1 hora).

Não há passo manual: `bot/main.py` sincroniza a árvore no `setup_hook`.

## 6. Do teste para a produção

| Item | Teste | Produção |
| --- | --- | --- |
| `STRIPE_SECRET_KEY` | `sk_test_...` | `sk_live_...` |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` da CLI | `whsec_...` do Dashboard |
| `PUBLIC_BASE_URL` | vazio (localhost) | `https://...` com TLS |
| `DEV_GUILD_ID` | id do servidor de testes | **vazio** (global) |
| Banco | `data/eclipse.db` | disco com backup agendado (§BACKUP) |
| Processo | terminal | systemd/Docker com restart automático |
| Dados de teste | — | apague recargas/pedidos de teste ou comece com banco novo |

### systemd (sugestão)

```ini
# /etc/systemd/system/eclipse-store.service
[Unit]
Description=Eclipse Store Discord bot
After=network-online.target

[Service]
WorkingDirectory=/opt/eclipse-store
EnvironmentFile=/opt/eclipse-store/.env
ExecStart=/opt/eclipse-store/.venv/bin/python -m bot
Restart=always
RestartSec=5
User=eclipse

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now eclipse-store
journalctl -u eclipse-store -f     # logs (segredos são redigidos automaticamente)
```

## 7. Primeiros passos dentro do Discord

1. `/config logs #canal-privado` — canal onde a equipe recebe os registros;
2. `/vendedor adicionar @fulano` — autoriza vendedores;
3. `/produto criar ...` — publique o primeiro produto (ou `/painel vendedor`);
4. `/loja` — abra o catálogo; `/carteira adicionar` — recarregue e teste a
   compra de ponta a ponta;
5. `/painel admin` — confira transações e auditoria.

## 8. Verificações de funcionamento

- `curl https://SEU_DOMINIO/healthz` → `{"status":"ok","bot":"eclipse-store"}`;
- Stripe Dashboard → **Webhooks** → entregues com HTTP 200;
- Eventos duplicados aparecem nos logs como “Evento duplicado ignorado”;
- `pytest` verde (18 testes, incluindo webhook com assinatura real).

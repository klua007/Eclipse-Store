# Checklist de configurações externas (proprietário)

Nada disso existe no código e **não pode ser inventado** — cada item é criado
por você nas plataformas oficiais. Marque ao concluir.

## Discord — <https://discord.com/developers/applications>

- [ ] Criar a aplicação `Eclipse Store`.
- [ ] Gerar o **Bot Token** e colocar em `DISCORD_TOKEN` (.env).
- [ ] Ativar **Server Members Intent** (Bot → Privileged Gateway Intents).
- [ ] Definir o avatar com `branding/avatar.png` (Portal ou `scripts/set_avatar.py`).
- [ ] Gerar URL de convite com scopes `bot` + `applications.commands` e
      permissões: View Channels, Send Messages, Embed Links, Attach Files,
      Use Slash Commands.
- [ ] Convidar o bot ao servidor de testes e ao servidor de produção.
- [ ] Anotar o próprio **ID numérico** (Modo Desenvolvedor) em `OWNER_ID`.
- [ ] (Dev) Anotar o ID do servidor de testes em `DEV_GUILD_ID`.
- [ ] Criar um **canal privado** de logs e configurá-lo com `/config logs`.

## Stripe — <https://dashboard.stripe.com>

- [ ] Criar a conta e confirmar o e-mail.
- [ ] Copiar a chave secreta de **teste** (`sk_test_...`) → `STRIPE_SECRET_KEY`.
- [ ] Instalar o Stripe CLI e rodar
      `stripe listen --forward-to localhost:8927/stripe/webhook`;
      copiar o `whsec_...` → `STRIPE_WEBHOOK_SECRET`.
- [ ] (Produção) Criar endpoint de webhook no Dashboard em
      `https://SEU_DOMINIO/stripe/webhook` com os 7 eventos listados em
      `SETUP.md §4.2`; copiar o `whsec_...` de produção.
- [ ] (Produção) Substituir `STRIPE_SECRET_KEY` pela `sk_live_...`.
- [ ] (Opcional) Revisar limites de risco/3D Secure no Dashboard.

## Infraestrutura

- [ ] Domínio + DNS apontando para o servidor do bot.
- [ ] Certificado TLS (certbot/Let's Encrypt) e proxy reverso (nginx/caddy)
      para a porta `STRIPE_WEBHOOK_PORT`.
- [ ] Definir `PUBLIC_BASE_URL=https://...` no .env.
- [ ] Serviço com reinício automático (systemd/Docker) — exemplo em `SETUP.md §6`.
- [ ] Cron de backup (`scripts/backup.py --include-files`) + cópia externa.
- [ ] Cofre/secrets manager para o `.env` (nunca commitar).

## Dentro do Discord (pós-deploy)

- [ ] `/config logs #canal` — canal privado de registros.
- [ ] `/vendedor adicionar @...` para cada vendedor autorizado.
- [ ] Cadastrar produtos (`/produto criar` ou `/painel vendedor`), conferindo
      tipo de entrega, estoque e alerta de estoque baixo.
- [ ] Teste de ponta a ponta em modo de teste: recarga → compra → entrega →
      estorno de teste (`stripe listen` mostra os eventos).
- [ ] Conferir `/painel admin → Transações/Auditoria` após os testes.

## Antes de virar a chave para produção

- [ ] `DEV_GUILD_ID` vazio (comandos globais).
- [ ] Chaves `live` + webhook do Dashboard configurados.
- [ ] Banco de produção novo ou migrado; dados de teste descartados.
- [ ] Backup agendado e **um restore de teste executado**.
- [ ] Equipe ciente de que ajustes manuais exigem motivo auditável.

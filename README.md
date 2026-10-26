# ◐ Eclipse Store

Bot profissional de vendas para Discord: carteira com recarga via **Stripe**,
catálogo de produtos digitais com estoque atômico, entrega privada e painel
completo de gestão (vendedores e administrador) dentro do próprio Discord.

> Pagamentos são confirmados **somente pelo webhook do Stripe com assinatura
> validada** — o retorno da página de checkout nunca credita saldo nem libera
> pedido. Todo valor é armazenado em **centavos inteiros** e toda movimentação
> gera lançamento auditável no ledger.

---

## Funcionalidades

### Carteira e pagamentos
- Carteira vinculada ao **ID numérico do usuário** no Discord (nunca ao apelido).
- `/carteira saldo` e `/carteira historico` respondem **em privado** (efêmero).
- `/carteira adicionar` cria uma **Stripe Checkout Session** (fluxo hospedado e
  seguro — o bot nunca vê dados de cartão).
- Crédito confirmado exclusivamente por webhook (`checkout.session.completed`
  com `payment_status = paid`), com **idempotência** por event id + status:
  eventos repetidos não creditam duas vezes.
- Ledger financeiro append-only: identificador da transação, ID Discord, valor,
  moeda, data, tipo, status e referência Stripe (`session`/`payment_intent`/
  `charge`/`dispute`).
- Estornos (`charge.refunded`) e disputas (`charge.dispute.*`) ajustam saldo e
  pedido de forma rastreável.
- Moeda inicial **BRL**, configurável por `CURRENCY`.

### Catálogo, estoque e entrega
- Produtos com nome, descrição, categoria, preço, moeda, status ativo/inativo,
  tipo de entrega (`text`, `file`, `code`, `link`, `custom`), estoque com
  alerta de baixo, vendedor responsável e datas de criação/alteração.
- Compra com saldo da carteira (débito atômico) **ou** pagamento direto por
  Stripe Checkout com reserva de estoque e expiração automática.
- Estoque nunca negativa e nunca vende a última unidade duas vezes
  (UPDATE condicional dentro da transação).
- Entrega **privada**: resposta efêmera + cópia por DM; se a DM estiver
  bloqueada, o cliente é avisado e usa `/meus-pedidos` (alternativa privada).
- Status de entrega registrado (`delivered`, `dm_blocked`, `failed`) e
  histórico consultável pela equipe (`/pedidos listar|ver`).

### Equipe e permissões
- `/vendedor adicionar|remover`, `/vendedores listar` (dono/admin).
- Permissões checadas **no servidor a cada comando e a cada clique de botão**
  (dono → admin → vendedor); UI nunca é confiada como segurança.
- Ajuste manual de saldo (`/ajuste saldo`) exige **motivo** e registra valor,
  usuário afetado, autor e data no ledger + auditoria + canal de logs.

### Painéis no Discord
- `/loja` — catálogo público com selects de categoria/produto e botões de compra.
- `/painel vendedor` — produtos, preço, estoque, descrição, entrega, remoção
  (com confirmação), pedidos com reentrega.
- `/painel admin` — vendedores, ajuste de saldo, canal de logs, transações,
  auditoria e transferência de posse.
- Modais para edição de preço/estoque/descrição/entrega/ajustes.
- Logs operacionais em canal privado configurável (`/config logs`).

---

## Estrutura do projeto

```
Eclipse-Store/
├── bot/
│   ├── main.py               # entrada: bot + webhooks + tarefas de fundo
│   ├── config.py             # variáveis de ambiente validadas
│   ├── app.py                # contêiner de dependências (bot.app)
│   ├── db/                   # SQLite WAL + transações atômicas
│   │   ├── core.py           # conexão, PRAGMAs, Database.tx()
│   │   ├── migrations.py     # migrações versionadas e atômicas
│   │   ├── wallets.py        # carteiras, ledger, recargas
│   │   ├── catalog.py        # produtos e reservas de estoque
│   │   ├── orders.py         # pedidos e status de entrega
│   │   ├── staff.py          # vendedores e configurações da guild
│   │   └── audit.py          # auditoria + idempotência de eventos Stripe
│   ├── services/
│   │   ├── money.py          # centavos inteiros, parse/format pt-BR
│   │   ├── stripe_service.py # Checkout Sessions + verificação de assinatura
│   │   ├── purchase.py       # casos de uso atômicos (compra, recarga, estorno)
│   │   ├── delivery.py       # entrega privada (efêmero/DM/meus-pedidos)
│   │   ├── permissions.py    # dono/admin/vendedor com checagem viva
│   │   └── notifier.py       # logs em canal privado
│   ├── ui/                   # tema visual, views, modais
│   ├── cogs/                 # comandos de barra (/loja, /carteira, ...)
│   └── web/webhook_server.py # POST /stripe/webhook (aiohttp)
├── branding/avatar.png       # avatar oficial (eclipse violeta/ciano)
├── scripts/
│   ├── backup.py             # backup online SQLite + entregáveis + retenção
│   ├── restore.py            # restauração com verificação de integridade
│   └── set_avatar.py         # aplica o avatar no bot
├── tests/                    # 18 testes (fluxos + webhook ponta a ponta)
├── docs/                     # guias de setup, backup, checklist, identidade
├── .env.example              # modelo sem credenciais
└── requirements.txt
```

## Início rápido

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # preencha token, OWNER_ID e chaves Stripe
python -m bot                 # sobe bot + servidor de webhooks
```

Guias completos:

| Guia | Conteúdo |
| --- | --- |
| [`docs/SETUP.md`](docs/SETUP.md) | Discord, Stripe, webhook, comandos, teste → produção |
| [`docs/BACKUP.md`](docs/BACKUP.md) | Backup, retenção e recuperação de desastres |
| [`docs/CHECKLIST_EXTERNO.md`](docs/CHECKLIST_EXTERNO.md) | O que o proprietário precisa configurar fora do código |
| [`docs/IDENTIDADE_VISUAL.md`](docs/IDENTIDADE_VISUAL.md) | Paleta, avatar e uso da marca |
| [`docs/LIMITACOES.md`](docs/LIMITACOES.md) | Limitações que dependem de configuração externa |

## Testes

```bash
pip install -r requirements-dev.txt
pytest            # 18 testes: atomicidade, idempotência, webhook assinado, backup
```

## Segurança (resumo)

- Segredos somente em variáveis de ambiente; filtro de logs redige token/chaves.
- Webhook valida `Stripe-Signature`; assinatura inválida → HTTP 400.
- Transações `BEGIN IMMEDIATE` garantem atomicidade de saldo+estoque+ledger.
- Nada sensível em canal público: saldo, extratos, códigos, links, arquivos e
  ações administrativas são sempre efêmeros/privados.

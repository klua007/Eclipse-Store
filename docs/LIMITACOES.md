# Limitações conhecidas (dependentes de configuração externa)

Nenhuma destas limitações quebra os fluxos principais; todas decorrem de
limites das plataformas ou de escolhas de implantação documentadas.

1. **URL pública para webhooks (produção).** O servidor embutido escuta em
   `0.0.0.0:STRIPE_WEBHOOK_PORT`, mas o Stripe só entrega eventos em HTTPS
   público. Sem `PUBLIC_BASE_URL` + TLS/proxy reverso, use o modo de teste com
   `stripe listen` (forward para localhost). Em produção o proxy é
   **obrigatório** (`docs/SETUP.md §4.3`).

2. **Páginas de sucesso/cancelamento.** `success_url`/`cancel_url` do Checkout
   apontam para `/sucesso` e `/cancelado` do próprio servidor. Sem domínio
   público, o usuário vê erro de conexão ao voltar do pagamento — **isso não
   afeta o crédito**, que vem do webhook; apenas a página de retorno fica
   indisponível. Configure `PUBLIC_BASE_URL` para resolver.

3. **Entrega por DM.** Se o cliente tiver DMs bloqueadas (privacidade do
   servidor), a entrega principal continua privada via resposta efêmera e o
   cliente recupera o conteúdo em `/meus-pedidos`. O pedido fica com
   `delivery_status = dm_blocked` para a equipe acompanhar.

4. **Comandos globais.** Sem `DEV_GUILD_ID`, a publicação global de comandos
   pode levar até 1 hora para aparecer (limite do Discord). Com
   `DEV_GUILD_ID`, os comandos existem apenas naquele servidor.

5. **Views não persistentes.** Painéis (botões/selects) expiram após ~10 min
   por segurança; basta rodar o comando novamente. Isso evita interações
   órfãs após reinícios do bot.

6. **Arquivos de entrega ≤ 8 MB** (limite de anexo do Discord sem Nitro).
   Para binários maiores, use entrega tipo `link` apontando para um storage
   privado com URL assinada.

7. **Moeda única por instalação.** `CURRENCY` define a moeda da loja (padrão
   BRL). Carteiras multi-moeda simultâneas não são suportadas nesta versão
   (o ledger já grava a moeda de cada operação, facilitando a evolução).

8. **Estorno de produto digital já entregue.** Quando um pedido pago no
   Stripe é estornado/disputado, o status e o ledger são ajustados, mas o
   conteúdo já entregue não é "recolhido"; a equipe decide bloqueios manuais
   (o log operacional destaca esses casos).

9. **SQLite single-node.** O banco é atômico e durável, mas pensado para um
   único processo/servidor. Escala horizontal (várias instâncias do bot)
   exigiria mover o repositório para Postgres — as camadas `bot/db/*`
   concentram todo o SQL para facilitar essa migração.

10. **Sem intent de conteúdo de mensagens.** O bot não lê mensagens de
    canais; toda interação é por comandos de barra, botões, selects e modais.

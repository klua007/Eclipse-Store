# Identidade visual — Eclipse Store

## Conceito

Um **eclipse artificial**: o disco negro central é ocultado por um anel de
luz que transiciona do **violeta** (energia, marca) ao **ciano** (tecnologia,
confiança), envolto por um anel técnico segmentado em grafite — leitura de
"interface de sistema"/cyber. Símbolo original, sem referência a marcas
existentes.

## Arquivo oficial

- `branding/avatar.png` — avatar quadrado 512×512, fundo preto, alto contraste
  e poucos elementos: permanece legível em 16×16 (lista de membros) e em
  128×128 (perfil).

Aplicação: `python scripts/set_avatar.py` (lê `DISCORD_TOKEN` do ambiente).

## Paleta

| Uso | Cor | Hex |
| --- | --- | --- |
| Primária (marca, embeds) | Violeta eclipse | `#8B5CF6` |
| Acento (informação, links) | Ciano luminescente | `#22D3EE` |
| Fundo profundo | Preto | `#0B0B0F` |
| Superfícies/painéis | Grafite | `#101014` |
| Sucesso | Verde | `#22C55E` |
| Erro/perigo | Rosa-avermelhado | `#F43F5E` |
| Atenção | Âmbar | `#F59E0B` |

A paleta está implementada em `bot/ui/theme.py` e é usada por todos os embeds,
botões e páginas HTML do Checkout (`/sucesso`, `/cancelado`).

## Regras de uso

1. **Fundo sempre escuro** (preto/grafite): o brilho do anel depende do
   contraste; nunca aplicar o símbolo sobre fundo claro.
2. **Não recolorir** o anel: o gradiente violeta→ciano é a assinatura.
3. **Área de respiro**: ao compor banners, manter margem ≥ 10% em volta do
   símbolo.
4. **Tipografia**: sem serifa geométrica (ex.: Inter, Space Grotesk) em pesos
   600–800 para títulos; mono (ex.: JetBrains Mono) para IDs, pedidos e chaves.
5. **Voz da marca**: direta, técnica e segura ("pagamento confirmado pelo
   webhook", "entrega privada") — os textos do bot seguem esse tom em pt-BR.
6. Não usar logotipos, nomes ou símbolos de terceiros (Discord, Stripe, etc.)
   como parte da marca Eclipse Store.

## Aplicação no produto

- Embeds: título violeta, campos de valor em ciano, erros em rosa, sucessos em
  verde; rodapé fixo `Eclipse Store ◐ loja digital segura`.
- Páginas do Checkout: cartão grafite com borda violeta e texto ciano.
- Logs operacionais: embeds grafite com ícones semânticos (💳 📦 ⚖️ 👥).

# ADR 0009 — Aids de leitura/tradução e conversa livre

**Data:** 2026-11-21
**Status:** aceito

## Contexto

O tutor já conversava por voz, mas o aprendiz de japonês/chinês não tinha como
ler a resposta nem entender o que foi dito sem sair do app. Também faltava um
modo sem cenário, em que o aprendiz conduz o tema.

## Decisão

1. **Leitura determinística, sem LLM**
   - japonês: `pykakasi` → kana (hiragana) + romaji + segmentos;
   - chinês: `pypinyin` com marcas de tom;
   - `en`/`de`: sem leitura.
   Bibliotecas locais pequenas, entram nas dependências base (não nos extras de
   áudio); `lru_cache` por `(language, text)`.
2. **Tradução PT-BR via LLM local**
   - prompt dedicado, `temperature=0`, JSON `{translation_pt}`;
   - best-effort: falha não quebra o turno;
   - cache em processo (256 entradas).
3. **Modos de tradução**: `auto` (padrão) e `manual` (só pelo botão “Traduzir”).
   A escolha é do usuário no setup/cabeçalho e pode mudar em sessão via
   `aids.update`.
4. **Entrega**: leitura sai imediatamente em `assistant.aux`; a tradução automática
   roda em task e pode chegar depois de `turn.end`. O cliente faz merge por
   `turn_id`.
5. **UI**: botão `あ` (ja), `拼` (zh) ou `PT` (en/de) em cada resposta do tutor abre
   um popover acessível com Kana/Romaji/Pinyin/Tradução.
6. **Conversa livre**: `mode="free"` usa um `Scenario` sintético (`id="free"`,
   sem `opening_line`), prompt sem cenário, o aprendiz inicia; correções por turno
   continuam; o banco grava `scenario_id="free"` sem migração.

## Consequências

- Nenhuma chamada extra ao LLM para leitura; uma chamada em background para a
  tradução automática.
- `session.ready` ganhou `mode`, `opening` e `aids`; o protocolo WS ganhou
  `assistant.aux`, `translate.request`, `translation.result` e `aids.update`.
- A leitura pode errar nomes/kanji ambíguos e o pinyin pode errar polifônicos;
  é auxiliar, não substitui o áudio.
- Aux não é persistido: o review e sessões antigas não mostram leitura/tradução
  (follow-up).
- O modo livre não resume temas no perfil (follow-up).
- O popover é uma sobreposição ancorada; telas muito estreitas podem precisar de
  bottom-sheet em uma iteração futura.

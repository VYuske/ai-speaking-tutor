# Plano — Leitura assistida, tradução e conversa livre

**Data:** 2026-11-21
**Status:** implementado (2026-11-21) — AST-045..049 concluídas.
**Escopo:** aids de aprendizagem para ja/zh + modo de conversa livre.
**Evidência:** 549 testes backend, 22 vitest, 14 e2e; turno real ja com
kana+romaji+PT e sessão livre real no container. ADR: `docs/adr/0009-aids-free-mode.md`.

---

## 1. Objetivo

1. **Leitura assistida para línguas orientais**
   - Japonês: **romaji** + **kana (furigana)** da resposta do tutor.
   - Chinês: **pinyin com tons**.
   - Serve para aprender o silabário e a pronúncia sem sair do chat.
2. **Tradução para PT-BR**
   - Menu com **Automática** (padrão) e **Desligada** (traduz só ao clicar no botão).
   - A tradução automática roda em background e não atrasa o áudio.
3. **Tooltip ao tocar**
   - Um toque no ícone da resposta abre leitura + tradução daquela mensagem.
4. **Modo conversa livre**
   - Sem cenário fixo; o aprendiz escolhe o tema e inicia a conversa.
   - O tutor segue o tema, mantém as correções por turno e a memória do perfil.
   - Selecionável **no setup** (trocar exige nova sessão).

---

## 2. Decisões de produto (confirmadas)

| Tema | Decisão |
|---|---|
| Leitura ja | romaji + kana (furigana) |
| Leitura zh | pinyin com marcas de tom |
| Tradução | menu Auto / Desligada; padrão Auto; com Desligada existe botão “Traduzir” |
| Modo livre | só no setup, com correções por turno |
| Exibição | tooltip/popover ao tocar na mensagem |

Fora do MVP: leitura/tradução do texto do **aprendiz**, persistir auxiliares no banco,
tradução de mensagens antigas de outras sessões, seletor de granularidade do furigana.

---

## 3. UX

### 3.1 Setup

- **Modo**: `Cenário` (padrão) / `Conversa livre`.
  - Em `Conversa livre`, a grade de cenários some e o botão inicia direto.
- **Ajudas** (nova seção):
  - `Leitura`: ligada por padrão para ja/zh; informativa (sem custo de LLM).
  - `Tradução`: `Automática` (padrão) / `Desligada (botão)`.
- Preferências em `localStorage` e enviadas na sessão.

### 3.2 Chat

- Cabeçalho: `Conversa livre` quando não há cenário; caso contrário, título do cenário.
- Cada resposta do tutor tem um botão pequeno:
  - ja: `あ` · zh: `拼` · en/de: `PT`.
  - Abre o popover ancorado na mensagem.
- Popover (uma seção por linha, fonte pequena, copiável):
  - **Kana** (ja): leitura em hiragana da frase.
  - **Romaji** (ja) / **Pinyin** (zh): linha fonética.
  - **Tradução**: PT-BR; se `Desligada`, um botão `Traduzir` com estado `traduzindo…`.
- Fechar: clique fora, `Esc` ou segundo toque. Não pausa o áudio.
- Modo livre: sem mensagem de abertura; estado vazio “Comece falando sobre o que quiser”.
- Se a tradução automática chegar depois do `turn.end`, o popover atualiza sozinho.

---

## 4. Arquitetura backend

### 4.1 Romanização (sem LLM)

Novo `backend/app/core/romanize.py`:

```python
@dataclass(frozen=True)
class Segment:
    text: str
    reading: str      # kana ou pinyin
    romaji: str | None

@dataclass(frozen=True)
class Reading:
    kind: Literal["furigana", "pinyin"]
    text: str         # romaji ou pinyin completo
    kana: str | None  # hiragana completo (ja)
    segments: list[Segment]

def reading_for(text: str, language: str) -> Reading | None
```

- `ja`: `pykakasi` → tokens `{orig, hira, hepburn}`; `kana` = concatenação `hira`,
  `text` = `hepburn`, `segments` = tokens.
- `zh`: `pypinyin.lazy_pinyin(text, style=Style.TONE)`; `text` = sílabas com tons,
  `segments` por caractere.
- `en`/`de`: `None`.
- Determinístico e barato; cache `lru_cache` por `(language, text)`.
- Preservar pontuação/emoji; não romanizar markdown (o texto exibido é limpo).

Dependências novas (base, não extras): `pykakasi`, `pypinyin` — pequenas e puras.
`uv lock` + rebuild da imagem base.

### 4.2 Tradução (LLM local)

Novo `backend/app/agent/translate.py`:

```python
TRANSLATION_SCHEMA = {"type":"object","properties":{"translation_pt":{"type":"string"}},...}

async def translate_pt(llm, text: str, language: str) -> str
```

- Prompt dedicado: traduzir a fala do tutor para PT-BR natural e concisa, sem
  explicar, sem markdown; `temperature=0`; `chat_json`.
- Cache em processo por `(language, text)` (LRU ~256) para abertura/repetições.
- Best-effort: falha não emite erro ao usuário; o botão continua disponível.
- Não usar o mesmo JSON do avaliador (separa correção de tradução e mantém o
  snapshot da avaliação estável).

### 4.3 TurnRunner e eventos

`TurnRunner` recebe `aids: AidsConfig(translation: "auto"|"manual")` e
`reading_enabled: bool` (default ja/zh).

- **Abertura e cada resposta do tutor**:
  1. `reading_for()` roda síncrono e barato.
  2. Se `translation == "auto"`, dispara `translate_pt()` em task paralela.
  3. Emite `assistant.aux` assim que cada parte estiver pronta. Pode chegar
     **depois** de `turn.end`; o cliente faz merge por `turn_id`.
- **Manual/desligada**: não dispara tradução; `translate.request` sob demanda.
- **`aids.update`** no meio da sessão: alterna Auto/Desligada sem reiniciar.

Novos eventos WS:

```text
session.start  {language, level, mode: "scenario"|"free", scenario_id?, profile?,
                aids: {translation: "auto"|"manual", reading: bool}}
session.ready  {mode, aids, scenario, ...}

assistant.aux  {turn_id, reading?: {kind, text, kana?, segments?}, translation_pt?}
translate.request {turn_id}
translation.result {turn_id, translation_pt}
aids.update    {translation?: "auto"|"manual"}
```

### 4.4 Modo conversa livre

- `session.start.mode = "free"`; `scenario_id` é ignorado.
- `app/agent/scenario.py`: `free_scenario(language) -> Scenario` sintético
  (`id="free"`, `title="Conversa livre"`, `opening_line=""`, goal genérico).
  Não entra em `/api/scenarios` nem no diretório de cenários.
- `prompts.py`: `build_free_conversation_messages()` — sem bloco de cenário;
  “o aprendiz escolhe o tema, siga a lead, 1–3 frases, uma pergunta por turno,
  não corrija no roleplay”.
- `TurnRunner.speak_opening()`: não faz nada se `opening_line` for vazio; o
  handler só chama quando há abertura.
- `session.ready`: `mode="free"`, `scenario` sintético, `opening=false`.
- Avaliador usa o cenário sintético (`Conversa livre`); correções continuam.
- Banco: `scenario_id="free"` (sem migração); review mostra “Conversa livre”.
- Memória: `finalize_session` inalterado; temas ainda não são resumidos (follow-up).

---

## 5. Frontend

Arquivos: `types.ts`, `App.tsx`, `SetupScreen.tsx`, `ChatScreen.tsx`,
`MessageBubble.tsx`, novo `ReadingPopover.tsx`, `e2e/app.e2e.ts`.

- `types.ts`: `SessionMode`, `TranslationMode`, `Aids`, `Reading`, `Segment`,
  `assistant.aux`, `translation.result`, `aids.update`; `ChatMessage.aux`.
- `App.tsx`: envia `mode`/`aids`; guarda preferências; faz merge de aux por
  `turn_id`; pede tradução sob demanda; alterna `aids.update`.
- `SetupScreen.tsx`: toggle de modo, seção “Ajudas” (tradução), esconde cenários
  no livre; `onStart` habilitado sem cenário.
- `ChatScreen.tsx`: header “Conversa livre”; estado vazio; prop de aids.
- `MessageBubble.tsx`: botão de leitura/tradução (assistente, inclusive abertura).
- `ReadingPopover.tsx`: popover acessível (focus, `Esc`, clique fora); seções
  Kana/Romaji/Pinyin/Tradução; botão Traduzir quando manual.
- Preferências: `localStorage` (`ast.translation`, `ast.mode`).
- Sem dependências novas no frontend.

---

## 6. Testes (test-first)

Backend:
- `test_romanize.py`: ja “今日は” → kana `きょうは` + romaji; zh “你好” → `nǐ hǎo`;
  pontuação/emoji; en/de → `None`; cache.
- `test_translate.py`: prompt/schema, resposta válida, falha best-effort, cache.
- `test_free_mode.py`: prompt livre sem cenário; `speak_opening` não emite abertura;
  WS `mode=free` + `text.input` chega a `turn.end`; review `scenario_id="free"`.
- `test_aids_events.py`: Auto emite `assistant.aux`; Desligada não emite até
  `translate.request`; `aids.update` alterna; aux após `turn.end` não quebra.
- Snapshots: `free_conversation_*` e `translation_*` (golden).

Frontend:
- Vitest: merge de aux por turno; popover abre/fecha; botão Traduzir.
- Playwright: setup livre (sem cenários, inicia), chat sem abertura, tooltip com
  kana/romaji/pinyin/tradução mockados, modo Desligada + botão Traduzir.
- `make check` verde.

Validação real (após implementar): turno em ja/zh no container, abrir o tooltip,
conferir leitura e tradução; um turno livre sem cenário.

---

## 7. Tasks e waves sugeridas

| Task | Conteúdo | Depende |
|---|---|---|
| AST-045 | `core/romanize.py` + testes | — |
| AST-046 | `agent/translate.py` + prompt + testes | — |
| AST-047 | Aids no `TurnRunner`/WS + eventos + testes | 045, 046 |
| AST-048 | Modo livre backend (cenário/prompt/WS) + testes | — |
| AST-049 | Frontend: tooltip, menu de tradução, tipos | 047 |
| AST-050 | Frontend: modo livre + e2e | 048 |

Waves:
- **A**: AST-045 + AST-046 (arquivos disjuntos).
- **B**: AST-047 (turn/ws).
- **C**: AST-048 (turn/ws; conflita com 047 → sequencial).
- **D**: AST-049 + AST-050 (ambas frontend; um goldfish ou sequencial).
- **E**: e2e/docs/ADR + aceite.

Alternativa mais rápida: juntar 047+048 num goldfish backend e 049+050 num
goldfish frontend → 3 waves.

---

## 8. Riscos e mitigações

| Risco | Mitigação |
|---|---|
| pykakasi erra nomes/kanji ambíguo | Leitura é auxiliar; não bloqueia; tooltip pode dizer “leitura aproximada” |
| pypinyin erra polifônicos | Dicionário de frases do pypinyin; fallback caractere |
| Tradução do LLM 4B ruim | Prompt dedicado, `temperature=0`, medir no harness; cache |
| Custo/latência da tradução auto | Background, não bloqueia áudio; cache; toggle Desligada |
| Tooltip polui/atrapalha o áudio | Um popover por vez, fecha com Esc/clique fora, não pausa o player |
| Modo livre sem contexto de cenário | Avaliador recebe “Conversa livre”; correções focam gramática/registro |
| `session.ready.scenario` nulo quebrar a UI | Cenário sintético com id `free`; tipos atualizados |
| Rebuild da imagem por dependências base | `uv lock` + build com extras; testar no container |

---

## 9. Decisões em aberto

1. Granularidade do furigana: linha de kana + romaji (MVP) ou ruby por token
   (segments já previstos para depois).
2. Tradução “Desligada” deve permitir o botão sempre, ou existir um terceiro
   estado “nunca traduzir”? Plano atual: dois estados (Auto/Desligada com botão).
3. Persistir `translation_pt`/leitura no banco para a revisão e o histórico?
   Fica para follow-up.
4. Mostrar leitura também para a transcrição do aprendiz? Follow-up.
5. Modelo de tradução: o mesmo `qwen3.5:4b` ou um modelo dedicado (ex.: NLLB)
   se a qualidade não bastar. Medir primeiro.
6. Cache da tradução em disco entre sessões? Follow-up.

---

## 10. Verificação

```bash
make check
docker compose exec -T api python -c "from app.core.romanize import reading_for; print(reading_for('今日はいい天気ですね','ja'))"
docker compose exec -T api python -c "from app.core.romanize import reading_for; print(reading_for('你好，今天天气很好','zh'))"
```

Checklist manual:
1. ja, nível 2, cenário: resposta com `あ` abre kana + romaji + tradução PT.
2. zh, nível 2: `拼` abre pinyin tonal + tradução.
3. Tradução Desligada: tooltip sem tradução, botão “Traduzir” funciona.
4. Modo livre: sem abertura, primeira fala inicia, correção por turno, review
   “Conversa livre”.
5. Encerrar e abrir nova sessão: preferências preservadas.

# Code review — MVP (aids + conversa livre)

**Data:** 2026-11-21
**Escopo:** `backend/app/**` e `frontend/src/**` + `frontend/e2e/**`.
**Método:** dois revisores read-only (backend e frontend), sem alteração de código.
**Plano:** `docs/plans/learning-aids-free-mode.md`; correções: AST-053/054.

## Resultado

| Área | P0 | P1 | P2 | P3 |
|---|---|---|---|---|
| Backend | 0 | 4 | 13 | 11 |
| Frontend | 1 | 8 | 8 | 14 |

O P0 do frontend era o microfone permanecer aberto após um toque mais rápido que
o `getUserMedia`/AudioWorklet. Todos os P0/P1 e os P2 baratos de alto risco foram
corrigidos antes do commit. O restante está no backlog AST-057..064.

## Corrigido antes do commit

### Backend (AST-053)

| # | Achado | Correção | Teste |
|---|---|---|---|
| B1 | JSON escalar/`turn_id` inválido derrubava o WS | validação de objeto + `_turn_ref` no explain | `test_review_fixes` (4 casos) |
| B2 | `explain` ignorava o `turn_id` | `turn_pairs` por turno; desconhecido → `nothing_to_explain` | 3 casos, 2 turnos |
| B3 | falha da tradução automática era silenciosa | `assistant.aux.translation_failed=true` | `test_aids_events` atualizado |
| B4 | `avg_logprob` vazava como confiança | `exp()` clampado em 0..1 | 7 casos do mapper |
| B5 | desconexão deixava `status='active'` | `end_session()` no `finally` | status + idempotência |
| B6 | task de TTS órfã em falha de leitura/avaliador | `try/finally` com cancel+gather; leitura best-effort | 2 casos |
| B7 | cache de providers por idioma duplicava modelos | chave sem `language` | 2 idiomas → 1 instância |
| B8 | `SystemExit` do spaCy escapava do fallback | `except (Exception, SystemExit)` | builder que sai do processo |
| B9 | tradução automática competia com o TTS | spawn após `await speaker` | TTS bloqueado + ordem |
| B10 | `timings_json` corrompido → 500 no review | parser tolerante | 2 casos |
| B11 | falha de persistência invisível | `warning code="persist_failed"` | 2 casos |
| B12 | mensagem do usuário ficava no histórico sem resposta | rollback no caminho vazio/falha | 2 casos |

### Frontend (AST-054)

| # | Achado | Correção | Teste |
|---|---|---|---|
| F1 | **P0** hot mic após toque rápido | máquina idle/starting/started, cancelar start, teardown, blur/visibility | e2e PTT + unit recorder |
| F2 | “Ouvir” tocava só a última frase | `audioUrls[]` por `seq` + `playInOrder` | e2e 3 sentenças |
| F3 | texto sumia e `busy` travava com socket fechado | `send` booleano; limpar só no sucesso | e2e socket fechado |
| F4 | Espaço roubava botões focados | ignora `activeElement` interativo; PTT com teclado | e2e teclado |
| F5 | perder foco segurando Espaço deixava gravando | cancelar em `blur`/`visibilitychange` | e2e blur |
| F6 | `atob` no render derrubava a árvore | try/catch + ErrorBoundary | e2e wav inválido + crash |
| F7 | “Explicar” podia mostrar outro turno | compara `turn_id`; modal mostra `Turno N` | e2e 2 turnos |
| F8 | header ignorava `session.ready.aids` | espelha o aids efetivo | e2e auto→manual |
| F9 | “carregando tradução…” eterno | `translation_failed`/fim do turno → botão | unit + e2e |
| F10 | object URLs nunca revogadas | revoga ao substituir/fechar/resetar | e2e revoke |

## Decisões de implementação

- `translation_failed` é um campo de `assistant.aux` (auto); o caminho manual mantém
  `warning code="translation_failed"`.
- `confidence` passa a ser probabilidade 0..1 (não log-prob).
- O fallback de providers degrada em qualquer `Exception` e `SystemExit`; um
  provider genuinamente mal configurado vira fake com warning, em vez de matar a
  sessão.
- `blur`/`visibilitychange` **descarta** o áudio parcial; `Esc` mantém o
  cancelamento com `audio.end`.
- O fallback de tradução após `turn.end` pode mostrar `Traduzir` por instantes
  enquanto uma tradução legítima atrasada chega; o texto substitui o botão.
- O turno de abertura não é explicável (`nothing_to_explain`).

## Backlog pós-commit

| ID | Item |
|---|---|
| AST-057 | SQLite fora do event loop (`to_thread` + lock + WAL/timeout) |
| AST-058 | Lock de síntese/transcrição em Kokoro/faster-whisper |
| AST-059 | Timeout/progresso no primeiro load de modelo (`model_loading`) |
| AST-060 | A11y de modais/toasts, contraste, foco |
| AST-061 | Keepalive/reconnect WS, cancelar fila no `session.end`, EventSink limitado |
| AST-062 | Extrair reducer do `App`; testes de `TutorSocket`, aux duplicado, free com 0 turnos |
| AST-063 | `Origin` no handshake WS; não vazar `str(exc)` ao cliente |
| AST-064 | Concorrência real do TTS (semáforo morto) + semântica de `level` no perfil |

## Lacunas de teste restantes

- Sem teste de áudio real no headless (PTT é stubado).
- Sem teste de `TutorSocket`/reducer isolados.
- Sem teste de `assistant.aux` duplicado e de free mode com zero turnos.
- Sem teste de carga/backpressure do `EventSink`.

## Validação

- `make check` verde: **578 pytest**, **44 vitest**, **27 e2e**, build OK.
- Rebuild `api`/`web` com `--extra stt --extra tts`; perfil `compact`.
- Turno real ja no container: `llm ok`, `translation` + `reading`, review
  `finished`.

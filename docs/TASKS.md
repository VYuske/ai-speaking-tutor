# Ledger de tarefas

Legenda: `backlog` · `ready` · `in-flight` · `review` · `done` · `blocked`
Regras: `docs/agents/ELEPHANT.md`. Definition of Ready/Done no fim.

---

## Waves concluídas

### Wave 1 (2026-11-18)

| ID | Tarefa | Resultado |
|---|---|---|
| AST-001 | Preflight + benchmark | aceito com follow-ups |
| AST-002 | Contrato do provider LLM | aceito; stub HTTP local |
| AST-012 | Thinking + `[DONE]` | aceito; turno real 376 ms |

### Wave 2 (2026-11-18)

| ID | Tarefa | Resultado |
|---|---|---|
| AST-013 | Endurecer `bench_llm.py` | cold/warm, `n`, deadline, `--force` |
| AST-015 | Precisão do `preflight_gpu.py` | multi-GPU, `[N/A]`, CSV |
| AST-016 | Warm-up do LLM | `warmup.done`, `cold_start_ms` |

### Wave 3 (2026-11-18)

| ID | Tarefa | Resultado |
|---|---|---|
| AST-003 | E2E do frontend | 5 testes Playwright |
| AST-014 | Testes dos scripts | 54 testes |
| AST-007 | Memória do aprendiz | perfil por idioma |

### Wave 4 (2026-11-18)

| ID | Tarefa | Resultado |
|---|---|---|
| AST-019 | Card de correção duplicado | corrigido; e2e exige 1 card |
| AST-021 | Perfil em disconnect | `finalize()` no `finally` |
| AST-022 | E2E no typecheck | `tsc` cobre `e2e/` |

### Wave 5 (2026-11-18)

| ID | Tarefa | Resultado |
|---|---|---|
| AST-020 | Gate canônico | `make check` em ~10 s |
| AST-009 | 8 cenários por idioma | 32 cenários + 35 testes |
| AST-017 | UI de warm-up | pill pendente/pronto/indisponível |

### Wave 6 (2026-11-18)

| ID | Tarefa | Resultado |
|---|---|---|
| AST-018 | Cancelar warm-up no fim | `cancel_warmup()` em end/disconnect |
| AST-008a | Snapshots de prompt | 20 golden files, 26 testes |
| AST-008b | Harness de avaliação | `eval_prompts.py` + 37 testes |

### Wave 7 (2026-11-18)

| ID | Tarefa | Resultado |
|---|---|---|
| AST-025 | Validar entradas dos prompts | `TutorError` para nível/idioma/focus; sessão sobrevive |
| AST-026 | Alinhar o harness ao cenário | roteiro por cenário + warm-up descartado |
| AST-010 | VRAM + fallback OOM | `session.ready.vram`, warning e degrade STT/TTS→fake |

Evidência consolidada:

- `make check` verde; **305 testes backend**, 7 e2e, 7 vitest, build limpo.
- Suíte completa 3× e `make check` sem flake.
- Eval real alinhado: 8/8 aderência, 8/8 JSON válido, p50 778 ms, `de` sem desvio.
- Relatório canônico em `docs/spikes/eval-prompts-2026-11-18.md`.

### Wave 8 (2026-11-18)

| ID | Tarefa | Resultado |
|---|---|---|
| AST-027 | Precisão do avaliador | filtro `original`/`corrected` + prompt; 17→9 correções |
| AST-024 | Nomes de arquivo zh | renomeados, ids intactos, teste de prefixo |

Evidência:

- `make check` verde; **319 testes backend**.
- Eval real: 8/8 aderência, 8/8 JSON válido, **9 correções (1,12/turn)**, p50 696 ms.
- Integração do elefante: os fakes passaram a citar a fala real do aprendiz, senão o
  novo filtro derrubava 6 testes que dependiam de uma correção inventada.

### Wave 9 (2026-11-19)

| ID | Tarefa | Resultado |
|---|---|---|
| AST-030 | Dedupe + pontuação | filtro determinístico + 6 testes; 11→8 correções |
| AST-028 | Fonte única de VRAM | `vram_profiles.toml` lido pela API e pelo preflight |
| AST-029 | Canned por cenário | dry-run alinhado; `de` fala de trem |

Evidência:

- `make check` verde; **352 testes backend** (2 repetições).
- Eval real: 8/8 aderência, 8/8 JSON válido, **8 correções (1,00/turn)**, p50 867 ms.
- Relatório canônico: `docs/spikes/eval-prompts-2026-11-19.md`.

### Wave 10 (2026-11-19)

| ID | Tarefa | Resultado |
|---|---|---|
| AST-031 | Avaliação sem `repr` | labels crus, 24 testes; 16 snapshots intactos |
| AST-033 | Dedupe por sobreposição | fragmento + frase inteira → 1; 7 testes |

Evidência:

- `make check` verde; **383 testes backend**.
- Eval (noisy entre runs): 9 correções (1,12/turn), p50 863 ms; sonda controlada
  mostra `zh` 3→1 e nenhuma correção perdida por aspas.
- **Incidente:** o AST-031 apagou `tests/snapshots/` com `rm -rf` e regenerou.
  Integridade conferida por sha256: só os 4 arquivos de avaliação mudaram; os 16
  de roleplay/explain estão byte-idênticos. Regra “nunca apagar” adicionada ao
  brief dos goldfish.

### Wave 11 (2026-11-19)

| ID | Tarefa | Resultado |
|---|---|---|
| AST-034 | Parsers de fala | `literal_eval` só para linhas entre aspas; 27 testes |
| AST-032 | Capitalização alemã | caso puro mantido em `de`, com e sem `user_text`; 13 testes |

Evidência:

- `make check` verde; **423 testes backend**.
- Probe de produção: `de+user_text` mantém `Ich spreche gut Deutsch`; `en+user_text`
  descarta. O AST-032 inicial falhava nesse caso; o follow-up AST-032b corrigiu.

### Wave 12 (2026-11-20)

| ID | Tarefa | Resultado |
|---|---|---|
| AST-035 | Harness language-aware | passa o idioma ao `normalize`; 58 testes |

Evidência:

- `make check` verde; **425 testes backend**.
- Eval: 8/8 aderência, 8/8 JSON válido, **8 correções (1,00/turn)**, p50 922 ms.
- Relatório: `docs/spikes/eval-prompts-2026-11-20.md`.

---

## AST-000 — resolvido (proxy socat)

O `ollama-proxy` no `compose.yaml` expõe `127.0.0.1:11434` em
`172.17.0.1:11435` só para a bridge Docker, sem `sudo` e sem alterar o serviço.
A API usa `host.docker.internal:11435`; `/api/health` retorna `reachable: ok` e
um turno real no container respondeu (1º token 3,1 s; cold start 2,8 s).
ADR: `docs/adr/0007-ollama-proxy.md`.

---

### Wave 13 (2026-11-20)

| ID | Tarefa | Resultado |
|---|---|---|
| AST-004 | Áudio real no Docker | STT real nos 4 idiomas; TTS real en/ja/zh; de pendente Piper |
| AST-037 | Calibração do STT | large-v3-turbo → small; p50 5,6 s → 2,26 s |

Evidência:

- `make check` verde; **425 testes**.
- Smoke real no container: `en` first_audio 2,2 s; ja/zh p95 ~2,9 s.
- Trade-off do `small`: em áudio espeak, "large" virou "orange"; validação com
  voz humana pendente (AST-040).

### Wave 14 (2026-11-21)

| ID | Tarefa | Resultado |
|---|---|---|
| AST-005 | Piper para alemão | voz real `de_DE-thorsten-medium`; 4 idiomas com STT+TTS |
| AST-039 | Latência do TTS | medição: 49–51% do first_audio é a 1ª sentença Kokoro |

Evidência:

- `make check` verde; 425 testes; imagem 5,22 GB; smoke real nos 4 idiomas.
- p50 `first_audio` 2,69 s; ja/zh p95 **3,3–3,5 s**; `en` chegou a 4,1 s quando o
  LLM emitiu markdown (`**orange**`) e o Kokoro falou os asteriscos.
- Warm-up de TTS testado e rejeitado: ganho 0 ms (o custo é por sentença).
- Relatório: `docs/spikes/audio-tts-latency-2026-11-21.md`.

### Wave 15 (2026-11-21)

| ID | Tarefa | Resultado |
|---|---|---|
| AST-041 | Sanitizar + fatiar a 1ª sentença | markdown fora do TTS; corte por cláusula/CJK |
| AST-043 | Threads do Kokoro | 10 → 6 threads; −32% a −36% por sentença |

Evidência:

- `make check` verde; **464 testes** (39 novos), 1 skip (`torch` no host).
- Smoke real (6 runs + confirmação do elefante): `first_audio` p50
  **en 2,39 s · ja 2,01 s · zh 1,93 s · de 1,76 s**; **meta de 2,5 s cumprida**.
- `tts1_ms` caiu 55% (ja) e 67% (zh). Texto do `tts.audio` sem markdown; transcript
  preserva o original.
- Relatório: `docs/spikes/audio-latency-wave15-2026-11-21.md`.

Riscos residuais: `en` tem cauda (frase sem vírgula, 4,1 s de áudio → 2,9 s);
corte por caractere no `zh` pode partir um composto; heurísticas (10/8 chars)
merecem escuta com voz humana.

## Wave 16 — aceite do MVP

| ID | Status | Tarefa | Quem |
|---|---|---|---|
| AST-040 | ready | Validação com voz humana (4 frases) | usuário |
| AST-044 | ready | Teste manual no navegador (mic real + PTT) | elefante |

### AST-040 — Voz humana

Gravar uma frase por idioma (`docs/spikes/human/`), rodar o STT real e comparar com
o texto esperado. O `small` errou "large" → "orange" em espeak; decidir se o
perfil `compact` fica com `small` ou se `[languages.*].stt` recebe um modelo maior.

### AST-044 — Navegador

Abrir `http://localhost:8080`, sessão em cada idioma, push-to-talk com microfone
real, conferir transcrição, áudio, card de correção e revisão.

---

## Wave 17 — planejada (aids + conversa livre)

Plano completo: `docs/plans/learning-aids-free-mode.md`.

| ID | Status | Tarefa | Escopo | Depende |
|---|---|---|---|---|
| AST-045 | done | Romanização ja/zh | `core/romanize.py`; 16 testes; `tenki`/`nǐ hǎo` reais |
| AST-046 | done | Tradução PT-BR | `agent/translate.py`; 20 testes; cache + best-effort |
| AST-047 | done | Aids no TurnRunner/WS | `assistant.aux` real (kana+romaji+PT), `translate.request`, `aids.update`; 20 testes |
| AST-048 | done | Modo livre backend | cenário sintético, prompt livre, sem abertura; 23 testes |
| AST-049 | done | Frontend: aids + modo livre | tooltip `あ/拼/PT`, menu PT, modo livre; 22 vitest + 14 e2e |

Evidência da Wave A: `make check` verde; **522 testes**; leituras reais no container
(`天気`→`tenki`/`てんき`, `你好`→`nǐ hǎo`). Heurística documentada: pykakasi lê
`今日は` como saudação; o módulo separa a partícula de tópico após composto de 2 kanji.

Evidência do AST-047: payload real no container com
`reading.kind=furigana`, `kana`, `segments` e `translation_pt` em PT; `turn.end`
não é atrasado pela tradução (teste com tradução estacionada).
Incidente: disco chegou a 100%; o elefante podou 5,3 GB de build cache do Docker
(sem tocar nas imagens de outros projetos) e liberou 5,8 GB.

Evidência do AST-048: **549 testes**; sessão real `mode=free` no container começa
sem abertura, responde por texto e grava `scenario_id="free"`; `/api/scenarios`
continua com 8 por idioma (free não aparece). 4 snapshots novos do prompt livre;
os 20 antigos permaneceram byte-idênticos.

Evidência do AST-049: **22 vitest + 14 e2e**; turno real ja com
`assistant.aux` (kana+romaji+PT) e sessão livre real (`mode=free`, sem abertura,
tradução PT, review `scenario_id=free`). Preferências de modo/tradução persistem.

## Wave 18 — revisão e primeiro commit

| ID | Status | Tarefa | Evidência |
|---|---|---|---|
| AST-051 | done | ADR 0009 + README com badges/stack | `docs/adr/0009`, `README.md` |
| AST-052 | pending | Teste humano no navegador (mic + tooltip + livre) | usuário |
| AST-053 | done | Correções backend da review | 29 testes novos; **578** no total |
| AST-054 | done | Correções frontend da review | **44 vitest + 27 e2e** |
| AST-055 | done | README: 19 badges + tabela Stack | link/URL check 19/19 |
| AST-056 | done | Relatório da review + stage do commit | `docs/reviews/2026-11-21-mvp-review.md` |

Review completa em `docs/reviews/2026-11-21-mvp-review.md`: backend sem P0,
frontend com 1 P0 (hot mic) e 8 P1 — todos corrigidos antes do commit.

Backlog pós-commit: AST-057 (SQLite off-loop/WAL), AST-058 (locks de inferência),
AST-059 (timeout de load), AST-060 (a11y/contraste), AST-061 (WS keepalive/fila),
AST-062 (reducer + testes restantes), AST-063 (Origin/erros internos),
AST-064 (concorrência TTS + semântica de nível).

Pendências conhecidas: aux não é persistido no review; furigana só em linha
(sem ruby por token); leitura sempre ligada; popover pode estourar em telas
estreitas; qualidade da tradução do modelo 4B a medir com uso real.

Decisões: ja = romaji + kana; zh = pinyin tonal; tradução Auto (padrão) /
Desligada com botão; modo livre só no setup, com correções por turno; tooltip ao tocar.

---

## Backlog priorizado

| ID | Status | Tarefa | Depende |
|---|---|---|---|
| AST-006 | backlog | Adapters Qwen3-ASR / Qwen3-TTS (GPU) | AST-004 |
| AST-011 | backlog | Contrato de STT/TTS (`@pytest.mark.models`) | AST-004 |
| AST-036 | backlog | Normalizar `language` (`de-DE` → `de`) no avaliador | AST-032 |
| AST-038 | backlog | `SystemExit` no TTS não pode derrubar o WS | AST-004 |
| AST-040 | backlog | Validação com voz humana (accuracy do `small`) | AST-037 |

---

## Definition of Ready

- Objetivo em uma frase verificável.
- Lista fechada de arquivos permitidos e proibidos.
- Contrato relevante citado.
- Critérios de aceite expressos como teste ou comando verificável.
- Comandos de validação exatos.
- Dependências e conflitos de arquivo identificados.

## Definition of Done

- Critérios de aceite reproduzíveis.
- **Teste escrito antes do código**, com a falha inicial registrada no relatório
  (ou comando de validação equivalente para docs/config).
- Testes novos cobrindo caminho feliz e um erro.
- `make check` verde.
- Nenhum arquivo fora do escopo.
- Relatório com comandos e saídas reais.
- Revisão aprovada com `docs/agents/REVIEW_CHECKLIST.md`.
- `PROJECT.md` e este ledger atualizados pelo elefante.

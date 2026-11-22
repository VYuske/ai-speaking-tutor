# AI Speaking Tutor — memória do projeto (elefante)

> Documento vivo. O elefante atualiza isto ao fim de cada wave. Se uma informação
> aqui divergir do código, o código está errado ou este documento está desatualizado —
> resolver antes de seguir.

**Última atualização:** 2026-11-19
**Fase:** MVP — esqueleto andante concluído; áudio real é o próximo marco.

---

## 1. O que é

Sistema local para treinar **speaking** em inglês, japonês, chinês e alemão.
O aprendiz conversa por voz com um tutor que simula situações cotidianas, mantém
memória do perfil e devolve correções em PT-BR.

Princípios inegociáveis:

1. **100% local.** Nenhuma chamada de rede para fora da máquina do usuário.
2. **Modelo é configuração, não código.** Trocar LLM/STT/TTS não exige alterar Python.
3. **Cascata STT → LLM → TTS.** Sem speech-to-speech no MVP (6 GB de VRAM).
4. **Orquestração determinística.** O LLM conversa; o código controla o fluxo.
5. **Docker isola a aplicação.** O host não precisa de Python/Node instalados.
6. **`skeleton` sempre funciona.** Perfil sem modelos, para desenvolvimento e testes.

---

## 2. Estado atual (verificado)

| Item | Estado |
|---|---|
| `docker compose up` | api healthy em `:8000`, web em `:8080`, proxy do Ollama ativo |
| LLM no Docker | `ollama-proxy` (socat) → `reachable: ok`; turno real no container |
| Endpoints | `GET /api/health`, `/api/config`, `/api/scenarios`, `/api/sessions/{id}`, `WS /ws` |
| Perfil default no compose | `skeleton` (LLM real configurado, STT/TTS fake) |
| LLM | `qwen3.5:4b` Q4_K_M (3,3 GB) no Ollama do host |
| Turno real (host) | validado: stream + avaliação JSON, 1º token 376 ms, total 2,3 s |
| Benchmark LLM | `docs/spikes/bench-llm-2026-11-18.md` (warm TTFB 59–61 ms, cold ~110 ms) |
| Thinking do LLM | desligado por padrão (`reasoning_effort = "none"`, ADR 0005) |
| STT real | `faster-whisper` implementado (não instalado por padrão) |
| TTS real | `kokoro` implementado (não instalado por padrão) |
| `qwen3_asr`, `qwen3_tts`, `piper`, `chatterbox` | stubs com fallback para fake |
| Cenários | 8 por idioma (32 no total) |
| Memória | turnos + correções + perfil do aprendiz por idioma (determinístico) |
| Warm-up | LLM aquecido após `session.ready`; `cold_start_ms` nos timings |
| Testes backend | 578 passando (`uv run pytest -q`) |
| Snapshots | 20 golden files de prompt (roleplay/eval/explain) |
| Eval | `docs/spikes/eval-prompts-2026-11-20.md` (8 correções, 1,00/turn, p50 922 ms) |
| VRAM | `session.ready.vram` + warning; OOM local degrada STT/TTS para fake |
| Áudio | STT real nos 4 idiomas; TTS real (Kokoro en/ja/zh, Piper de) |
| Latência de áudio | `first_audio` p50: en 2,4 s · ja 2,0 s · zh 1,9 s · de 1,8 s |
| Aids | kana+romaji (ja), pinyin tonal (zh), tradução PT auto/manual, tooltip |
| Modo livre | sem cenário e sem abertura; aprendiz inicia; correções por turno |
| Testes frontend | 44 unit (`vitest`) + 27 e2e (`playwright`) |
| Gate | `make check` (lint + pytest + vitest + e2e + build) em ~10 s |
| Review | 2026-11-21: backend sem P0; frontend P0/P1 corrigidos; AST-057..064 no backlog |
| Build Docker | imagens `ai-speaking-tutor-api` e `ai-speaking-tutor-web` OK |

### AST-000 — resolvido via proxy (ADR 0007)

O serviço `ollama-proxy` (socat, `network_mode: host`) expõe `127.0.0.1:11434`
em `172.17.0.1:11435` **apenas para a bridge Docker**. A API usa
`http://host.docker.internal:11435/v1`; `/api/health` reporta `reachable: ok`.

Verificado no container: turno real em inglês respondeu, com
`first_token_ms=3100`, `cold_start_ms=2757`, `total_ms=5647`.

O host continua usando `127.0.0.1:11434` (scripts de bench/eval). A correção
canônica (`OLLAMA_HOST`) segue documentada no ledger, mas não é necessária.

---

## 3. Hardware de referência

- GPU: **NVIDIA RTX 3050 Laptop, 6 GB** (sem NVIDIA Container Toolkit instalado).
- CPU: 16 threads.
- Ollama 0.40.1 no host, modelos já baixados: `qwen3.5:4b`, `qwen3.5:2b`, `qwen3.5:0.8b`.
- Host tem Python 3.14 e Node 26; **nada disso é usado pela aplicação** (Docker + Python 3.12).
- Consequência de design: **6 GB não comportam LLM + STT + TTS juntos com folga.**
  O perfil `compact` usa LLM na GPU e áudio na CPU.

---

## 4. Arquitetura em uma tela

```
Browser (React, PT-BR)
  ├─ AudioWorklet → PCM16 16 kHz
  ├─ WS: eventos + binário
  └─ fila de áudio ordenada por (turn, seq)

nginx (:8080) ──/api,/ws──► FastAPI (:8000)
                              ├─ TurnRunner (máquina de estados)
                              ├─ ProviderCache (1 instância por config)
                              ├─ PromptBuilder / Evaluator
                              ├─ SQLite + áudio em /data
                              └─ Providers: LLM OpenAI-compat, STT, TTS

Ollama (host, GPU) ◄── HTTP ── api
```

Contratos, fluxo do turno e resolução de config: `docs/ARCHITECTURE.md`.

---

## 5. Decisões registradas

| ADR | Decisão |
|---|---|
| `docs/adr/0001-docker-isolation.md` | Aplicação em containers, Ollama no host |
| `docs/adr/0002-cascaded-pipeline.md` | Cascata STT→LLM→TTS, não speech-to-speech |
| `docs/adr/0003-deterministic-orchestrator.md` | Sem LangGraph/LangChain; LLM só gera texto/JSON |
| `docs/adr/0004-elephant-goldfish-workflow.md` | Elefante orquestra, goldfish executam escopos |
| `docs/adr/0005-disable-thinking.md` | Thinking desligado no LLM de produto |
| `docs/adr/0006-test-first.md` | Teste antes do código, com evidência de falha |
| `docs/adr/0007-ollama-proxy.md` | Proxy socat expõe o Ollama só para a bridge Docker |
| `docs/adr/0008-compact-stt-small.md` | STT do perfil compact = Whisper `small` |
| `docs/adr/0009-aids-free-mode.md` | Leitura (kana/romaji/pinyin), tradução e conversa livre |

---

## 6. Invariantes técnicos

1. Áudio cliente→servidor: **PCM16 mono, 16 kHz**, little endian.
2. Áudio servidor→cliente: WAV base64, 24 kHz (Kokoro/Qwen3-TTS), um evento por sentença.
3. O WebSocket tem **um único escritor** (`EventSink`); nunca enviar direto de tasks paralelas.
4. Transições de estado passam por `orchestrator/state.py`; transição inválida é forçada e logada.
5. Feedback é **best-effort**: falha na avaliação não derruba o turno.
6. TTS é **best-effort**: falha de áudio mantém o texto e emite `warning`.
7. Nenhum teste padrão carrega modelo. Testes de modelo usam `@pytest.mark.models`.
8. `api_key` nunca aparece em `/api/config` nem em logs.
9. Config é resolvida por `arg > AST_PROFILE > languages.<lang>.profile > default_profile`.
10. **Test-first:** comportamento novo começa por um teste que falha; docs/config
    começam por um comando de validação que falha. A evidência vai no relatório.

---

## 7. Como verificar o projeto

```bash
docker compose up -d --build
curl -s localhost:8000/api/health | jq
curl -s localhost:8080/ -o /dev/null -w '%{http_code}\n'

cd backend && uv run ruff check . && uv run pytest -q
cd frontend && npx tsc --noEmit && npx vitest run && npm run build
```

Checklist manual: abrir `http://localhost:8080`, escolher idioma/cenário, enviar
texto, ouvir resposta, receber card de correção, encerrar e ver a revisão.

---

## 8. Dívidas conhecidas

- Sem testes de contrato dos adapters reais (STT/TTS) — AST-011.
- `en` tem cauda de latência quando o LLM gera frase longa sem vírgula (~2,9 s).
- Corte por caractere no `zh` pode partir um composto; heurísticas de corte (10/8)
  precisam de escuta com voz humana.
- `torch.set_num_threads` é global ao processo; o turno permite 2 sínteses. Revisar
  se houver sessões concorrentes.
- O `small` confunde palavras em áudio espeak ("large" → "orange"); validar com
  voz humana — AST-040.
- Aids não são persistidos: review/histórico não mostram leitura/tradução.
- Furigana só em linha (sem ruby por token); leitura sempre ligada; o popover pode
  estourar em telas estreitas.
- Tradução automática depende da qualidade do `qwen3.5:4b`; medir com uso real.
- Modo livre não resume os temas no perfil do aprendiz.
- `SystemExit` do spaCy pode derrubar o WebSocket no TTS — AST-038.
- `hf_cache` ainda guarda o `large-v3-turbo` (1,6 GB) sem uso; podar quando estável.
- `language` é comparado literalmente (`de`); `de-DE` cairia na política padrão —
  AST-036 (polimento opcional).
- `frontend/tsconfig.json` inclui `types: ["node"]`, então `src/` pode referenciar
  globals de Node sem erro (tradeoff do AST-022; separar configs é opcional).
- `EventSink` engole eventos após falha de socket; não há reconexão.
- `OrderedAudioQueue` não libera blob URLs antigos.
- Sem `eslint`/`prettier` no frontend; gate atual é `tsc --noEmit`.
- Health não distingue “API ok” de “LLM ok” na UI além do badge de modelo.
- `scripts/` está fora do gate de lint/teste do backend (AST-014).
- Warm-up cobre só o LLM; o TTS carrega junto com a fala de abertura.
- `warmup.done`/`warmup.failed` já estão no contrato de tipos, mas a UI ainda não
  mostra “carregando modelo” (AST-017).
- Um warm-up pendente não é cancelado no `session.end` (AST-018).
- O benchmark mostra cold/warm; nesta máquina a diferença ficou dentro do ruído
  (`cached_tokens` reportado no relatório).

## 8.1 Últimos achados (revisão da wave 1)

1. **Bloqueante corrigido:** `qwen3.5:4b` é um modelo de raciocínio; sem
   `reasoning_effort: none`, ele consumia os 220 `max_tokens` pensando e devolvia
   `content` vazio (`finish_reason=length`, ~880 chars de reasoning). O provider
   agora envia `reasoning_effort` no `/v1` e `think: false` no `/api/chat`, com
   retry único sem o campo se o servidor responder 400.
2. **`[DONE]` agora encerra o stream** (antes era `continue`, então deltas após
   `[DONE]` ainda seriam entregues).
3. **Benchmark (resolvido na wave 2):** o p50 antigo era cache-warm. O relatório
   agora separa cold/warm, mostra `n` e min–max; nesta máquina a diferença ficou
   dentro do ruído, e o `cached_tokens` do servidor é reportado.
4. **Escopo da wave 1 confirmado:** apenas `scripts/`, `docs/spikes/` e os três
   arquivos do provider LLM foram tocados; o `README.md` modificado era do elefante.
5. **Wave 2:** benchmark endurecido (cold/warm, `n`, deadline, `--force`,
   config-driven), preflight corrigido (multi-GPU, `[N/A]`, CSV) e warm-up do LLM
   implementado. Relatório regenerado; 72 testes no backend.
6. **AST-023 cancelada:** os quatro cenários legados já estavam com `guardrails`
   no idioma-alvo. A premissa do brief anterior estava errada; verificado nos
   arquivos antes de abrir a tarefa.
7. **Wave 7:** prompts validam nível/idioma/focus; o eval ganhou roteiro por
   cenário e warm-up descartado (cold start fora do p50); a API reporta VRAM e
   degrada STT/TTS para fake em OOM local. 305 testes; relatório de eval canônico
   em `docs/spikes/`.
8. **Wave 8:** o avaliador só aceita `original` presente na fala do aprendiz e
   `corrected` diferente; prompt endurecido. Correções caíram de 17 para 9 no run
   real. Os fakes passaram a citar a fala real (integração do elefante). 319 testes.
9. **Wave 9:** dedupe + descarte de mudanças só de pontuação/caixa; VRAM com fonte
   única (`vram_profiles.toml`); canned do dry-run por cenário. Correções no run
   real: 8 (1,00/turn), p50 867 ms. 352 testes.
10. **Wave 10:** prompt de avaliação sem `repr` (o modelo não copia mais aspas) e
    dedupe por sobreposição (fragmento + frase inteira → 1). 383 testes. Um goldfish
    apagou os snapshots por engano; os 16 arquivos fora do escopo foram conferidos
    por sha256 e estão intactos. Regra “nunca apagar” adicionada ao brief.
11. **Wave 11:** parsers de fala não interpretam mais texto cru como literal Python;
    capitalização alemã é preservada no produto, com e sem `user_text` (o primeiro
    AST-032 falhava no caminho real; AST-032b corrigiu). 423 testes.
12. **Wave 12:** o harness de eval passa o idioma ao `normalize`, então a política
    alemã entra na métrica. 425 testes; eval 8 correções (1,00/turn), p50 922 ms.
13. **Wave 13:** áudio real no Docker. STT real nos 4 idiomas, TTS real en/ja/zh
    (alemão pendente Piper). O STT foi calibrado de `large-v3-turbo` para `small`:
    first_audio p50 5,6 s → 2,26 s; p95 ja/zh ~2,9 s. Imagem 5 GB com extras.
    Trade-off de accuracy pendente de validação com voz humana.
14. **Wave 14:** Piper alemão real (imagem 5,22 GB) e medição fina do TTS. O p95
    ja/zh ficou em 3,3–3,5 s; 49–51% do `first_audio` é a primeira sentença Kokoro,
    e o warm-up de TTS foi rejeitado com números (ganho 0 ms). Markdown do LLM
    (`**orange**`) faz o TTS falar asteriscos. AST-041/043 atacam isso.
15. **Wave 15:** sanitizador de markdown, corte da 1ª sentença por cláusula/CJK e
    Kokoro com 6 threads. `first_audio` p50 en 2,4 s · ja 2,0 s · zh 1,9 s ·
    de 1,8 s — **meta de 2,5 s cumprida**. 464 testes. Resta o aceite com voz
    humana e o teste no navegador.
16. **Wave 17:** aids de aprendizagem e conversa livre. Leitura determinística
    (pykakasi/pypinyin), tradução PT via LLM local com cache, tooltip `あ/拼/PT`,
    modo livre sem cenário/abertura. Turno real ja com kana+romaji+PT e sessão
    livre real confirmados no container.
17. **Wave 18:** code review backend/frontend e correções P0/P1. Hot mic, replay
    multi-sentença, perda silenciosa de texto, teclado, ErrorBoundary, `explain`
    por turno, tradução falha com retry, frames WS inválidos, status de sessão no
    disconnect e ordenação da tradução. 578 pytest, 44 vitest, 27 e2e. Relatório:
    `docs/reviews/2026-11-21-mvp-review.md`.

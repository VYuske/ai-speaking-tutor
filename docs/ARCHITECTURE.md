# Arquitetura

## 1. Componentes e responsabilidades

| Componente | Responsabilidade | Não faz |
|---|---|---|
| `frontend/` | captura de mic, playback, transcrição visível, correções | chamar modelo direto |
| `api/ws.py` | ciclo de vida da sessão, roteamento de eventos, buffer de áudio | gerar texto/áudio |
| `orchestrator/turn.py` | máquina de estados do turno, paralelismo LLM/TTS, persistência | montar prompts |
| `orchestrator/chunker.py` | dividir stream em sentenças faláveis | sintetizar |
| `agent/prompts.py` | política de idioma, nível, cenário | decidir fluxo |
| `agent/evaluator.py` | correções estruturadas e progresso | bloquear o turno |
| `providers/` | adapters de LLM/STT/TTS com contratos estáveis | conhecer cenário |
| `db/database.py` | sessões, turnos, correções, revisão | conter regra de negócio |
| `nginx` | servir SPA e fazer proxy de `/api` e `/ws` | autenticar |

## 2. Fluxo de um turno

```
cliente                  api                        modelos
  │ audio.start ─────────►│ estado=LISTENING
  │ PCM16 (frames) ──────►│ buffer (cap 30 s)
  │ audio.end ───────────►│ estado=TRANSCRIBING
  │                       │──── STT ─────────────────►
  │◄──── stt.final ───────│
  │                       │ estado=THINKING
  │                       │──── LLM stream ─────────►
  │◄──── llm.delta* ──────│
  │                       │ chunker → sentenças
  │                       │──── TTS por sentença ───►
  │◄──── tts.audio (seq) ─│
  │                       │──── avaliador JSON ─────►
  │◄──── feedback.card ───│
  │◄──── turn.end ────────│ estado=IDLE
```

Regras de concorrência:

- Um turno por sessão por vez (`busy` no handler).
- `EventSink` é o único escritor do WebSocket (fila FIFO + task de drenagem).
- TTS roda em até 2 tasks concorrentes (semáforo); o cliente ordena por `seq`.
- Avaliação começa depois do último token e corre em paralelo com o TTS restante.

## 2.1 Ollama no host

O Ollama escuta em `127.0.0.1:11434`. O serviço `ollama-proxy` (socat,
`network_mode: host`) expõe `172.17.0.1:11435` **apenas para a bridge Docker**.
A API usa `http://host.docker.internal:11435/v1`; o host continua usando
`127.0.0.1:11434`. Ver `docs/adr/0007-ollama-proxy.md`.

## 3. Contratos de provider

```python
class LLMProvider:
    async def chat_stream(messages, **overrides) -> AsyncIterator[str]
    async def chat_json(messages, schema, **overrides) -> dict

class STTProvider:
    async def transcribe(pcm16, sample_rate, language) -> Transcript

class TTSProvider:
    async def synthesize(text, language, voice) -> AudioChunk
```

`Transcript(text, language, confidence, duration_s)`
`AudioChunk(wav_bytes, sample_rate, duration_s)`

Regras:

- Todo adapter é construído a partir de um `dict` de config e falha com
  `ProviderUnavailable` (mensagem acionável) quando falta dependência/modelo.
- STT/TTS caem para `fake` com `warning`; LLM não cai (sem LLM não há sessão).
- Novos providers entram em `providers/registry.py`; esse arquivo é do elefante,
  não de goldfish, para evitar conflito.

## 4. Resolução de configuração

Ordem de precedência do perfil:

```
parâmetro explícito da sessão
  > AST_PROFILE (env)
  > languages.<lang>.profile (config/models.toml)
  > default_profile
```

Overrides de idioma são aplicados em profundidade sobre o perfil (`_deep_merge`).
`AST_OLLAMA_BASE_URL` e `AST_LLM_MODEL` sobrescrevem o LLM resolvido.

## 5. Persistência

SQLite em `/data/app.db` (volume `ast_data`).

- `sessions`: idioma, nível, cenário, perfil, status, timestamps.
- `turns`: texto do usuário, texto do assistente, confiança do STT, modelos, timings.
- `corrections`: por turno, com tipo/severidade/confiança.

Áudio não é persistido no MVP (decisão em aberto).

## 6. Protocolo WS (resumo)

Cliente → servidor: `session.start`, `audio.start`, binário PCM16, `audio.end`,
`text.input`, `explain.request`, `session.end`, `ping`.

Servidor → cliente: `session.ready`, `assistant.message`, `stt.final`, `llm.delta`,
`tts.audio`, `feedback.card`, `explain.result`, `state`, `warning`, `error`,
`review`, `turn.end`, `pong`.

Payloads completos: `frontend/src/types.ts` é a fonte de verdade do cliente;
`backend/app/orchestrator/turn.py` e `api/ws.py` do servidor. Alterações de
protocolo são mudanças de contrato: exigem atualização dos dois lados + teste.

## 7. Modos de falha tratados

| Falha | Comportamento |
|---|---|
| STT vazio/ruído | `warning` + volta para `idle`, sem penalizar |
| Áudio < 0,2 s | `warning audio_too_short` |
| LLM off | `error llm_failed` recuperável |
| JSON inválido na avaliação | retry 1×; se falhar, card vazio |
| TTS falha em uma sentença | texto permanece, `warning`, áudio segue nas outras |
| Modelo indisponível no perfil | fallback fake para STT/TTS + `warnings` na sessão |
| Cliente desconecta | `EventSink` para de drenar e o handler encerra |

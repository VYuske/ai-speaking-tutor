# AI Speaking Tutor

<!-- Badges estáticos (shields.io, flat-square). Sem badge de CI: o gate é local, `make check`. -->

![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI 0.115](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white)
![SQLModel sobre SQLite](https://img.shields.io/badge/SQLModel-SQLite-003B57?style=flat-square&logo=sqlite&logoColor=white)
![uv 0.13.9](https://img.shields.io/badge/uv-0.13.9-7C3AED?style=flat-square&logo=uv&logoColor=white)
![Ruff](https://img.shields.io/badge/Ruff-lint-D7FF64?style=flat-square&logo=ruff&logoColor=black)
![pytest 8](https://img.shields.io/badge/pytest-8-0A9EDC?style=flat-square&logo=pytest&logoColor=white)

![React 19](https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react&logoColor=black)
![TypeScript 5.7](https://img.shields.io/badge/TypeScript-5.7-3178C6?style=flat-square&logo=typescript&logoColor=white)
![Vite 6](https://img.shields.io/badge/Vite-6-646CFF?style=flat-square&logo=vite&logoColor=white)
![Tailwind CSS 4](https://img.shields.io/badge/Tailwind_CSS-4-06B6D4?style=flat-square&logo=tailwindcss&logoColor=white)
![Vitest 3](https://img.shields.io/badge/Vitest-3-6E9F18?style=flat-square&logo=vitest&logoColor=white)
![Playwright 1.65](https://img.shields.io/badge/Playwright-1.65-2EAD33?style=flat-square)

![Docker Compose](https://img.shields.io/badge/Docker_Compose-infra-2496ED?style=flat-square&logo=docker&logoColor=white)
![nginx 1.27](https://img.shields.io/badge/nginx-1.27-009639?style=flat-square&logo=nginx&logoColor=white)
![Ollama qwen3.5:4b](https://img.shields.io/badge/Ollama-qwen3.5:4b-000000?style=flat-square&logo=ollama&logoColor=white)
![faster-whisper small](https://img.shields.io/badge/STT-faster--whisper_small-1F6FEB?style=flat-square)
![Kokoro 0.9.2](https://img.shields.io/badge/TTS-Kokoro_0.9.2-FF6F61?style=flat-square)
![Piper 1.8](https://img.shields.io/badge/TTS_Piper-1.8-4B8BBE?style=flat-square)
![Licença MIT](https://img.shields.io/badge/Licen%C3%A7a-MIT-3DA639?style=flat-square)

Treino de conversação por voz, **100% local**, em inglês, japonês, chinês e alemão.
O tutor simula situações do dia a dia, responde falando e devolve correções em
português. Os modelos são trocáveis por configuração.

## Status

MVP funcional em Docker:

- interface de conversa com push-to-talk, streaming de texto e áudio por sentença;
- áudio real no perfil `compact`: Whisper `small` (4 idiomas), Kokoro (en/ja/zh) e
  Piper (de); `first_audio` p50 entre 1,8 s e 2,4 s;
- **leitura assistida** para japonês (kana + romaji) e chinês (pinyin tonal) e
  **tradução PT-BR** automática ou sob botão, num tooltip por resposta;
- **modo conversa livre** (sem cenário, você inicia o tema);
- 32 cenários (8 por idioma), memória do aprendiz, correções e revisão;
- LLM `qwen3.5:4b` local via Ollama;
- 578 testes backend, 44 vitest e 27 e2e Playwright.

O compose inclui um `ollama-proxy` (socat) que expõe o Ollama do host
(`127.0.0.1:11434`) apenas para a bridge Docker, em `172.17.0.1:11435`. A API usa
`host.docker.internal:11435`; o host continua usando `127.0.0.1:11434`. Turno real
validado dentro do container.

## Stack

O que roda hoje, com a fonte de cada versão. Não há CI hospedado: o gate é local
(`make check`, ADR 0006).

| Camada | Ferramenta | Papel | Versão/fonte |
|---|---|---|---|
| runtime | Python | Roda a API, os providers e os scripts | 3.12 (`requires-python = ">=3.12,<3.13"`, `backend/pyproject.toml`) |
| runtime | uv | Resolve e instala dependências com lock (`uv sync --frozen --no-dev`) | 0.13.9 (`docker/api.Dockerfile`) |
| runtime | FastAPI + Uvicorn | HTTP e WebSocket da API | FastAPI ≥0.115, Uvicorn ≥0.30 (`backend/pyproject.toml`) |
| runtime | Pydantic 2 + pydantic-settings | Contratos de request/response e config por env | ≥2.8,<3 |
| runtime | SQLModel + SQLite | Sessões, memória do aprendiz e correções em `/data` | SQLModel ≥0.0.22 |
| runtime | httpx | Cliente do LLM OpenAI-compatible | ≥0.27,<1 |
| agent/orchestration | Orquestrador próprio (`TurnRunner` + máquina de estados) | Fluxo do turno determinístico; o LLM só gera texto/JSON | `backend/app/orchestrator/` — sem LangGraph/LangChain (ADR 0003) |
| agent/orchestration | LLM `qwen3.5:4b` via Ollama | Roleplay, correções e tradução PT-BR | `backend/config/models.toml`, endpoint `/v1` OpenAI-compatible (thinking desligado, ADR 0005) |
| audio | faster-whisper `small` | STT nos 4 idiomas (CPU, int8) | extra `stt`, ≥1.0,<2 (1.2.1 no lock) |
| audio | Kokoro | TTS en/ja/zh (WAV 24 kHz) | extra `tts`, ≥0.9.2 (0.9.4 no lock) |
| audio | Piper | TTS alemão `de_DE-thorsten-medium` (Kokoro não tem `de`) | extra `tts`, ≥1.8,<2 (1.8.0 no lock; voz baixada no build) |
| audio | pykakasi + pypinyin | Kana/romaji (ja) e pinyin tonal (zh), determinístico, sem LLM | 2.3.0 e 0.55.0 (`backend/uv.lock`) |
| frontend | Node | Build do bundle estático | 22 (`docker/web.Dockerfile`) |
| frontend | React | UI da conversa | 19 (`frontend/package.json`) |
| frontend | TypeScript | Tipos; `tsc --noEmit` no build e no gate | 5.7 (`frontend/package.json`) |
| frontend | Vite | Dev server com proxy e build | 6 (`frontend/package.json`) |
| frontend | Tailwind CSS | Estilo, via plugin `@tailwindcss/vite` | 4 (`frontend/package.json`) |
| frontend | Vitest | Testes unitários | 3 (`make test`) |
| frontend | Playwright | e2e com WS e API mockados | 1.65 (`make e2e`) |
| infra | Docker Compose | Serviços `api`, `web` e `ollama-proxy` | `compose.yaml` |
| infra | nginx | Serve o SPA e faz proxy de `/api` e `/ws` | 1.27 (`docker/web.Dockerfile`, `docker/nginx.conf`) |
| infra | socat (`alpine/socat`) | Expõe o Ollama do host só para a bridge Docker | `compose.yaml` (ADR 0007) |
| infra | Volume `ast_data` | Persiste SQLite e áudio entre `up`/`down` | `compose.yaml` |
| quality | pytest | Testes backend (models só com `-m models`) | 8 (`make test`; 578 testes) |
| quality | Ruff | Lint e ordem de imports (`line-length = 100`) | ≥0.6,<1 (`make lint`) |
| quality | mypy | Tipagem estática com plugin pydantic | ≥1.11,<2 (`[tool.mypy]`, `backend/pyproject.toml`) |
| quality | Make | Gate único: lint + test + e2e + build | `Makefile` (`make check`) |

## Como rodar

Requisitos: Docker + Docker Compose e um Ollama com `qwen3.5:4b` na máquina.

```bash
cp .env.example .env
docker compose up -d --build
```

- Interface: http://localhost:8080
- API: http://localhost:8000/api/health

O `docker compose` usa o perfil `skeleton` por padrão (sem baixar modelos de áudio).
Para usar o perfil com áudio real na CPU (imagem maior):

```bash
AST_PROFILE=compact AST_INSTALL_EXTRAS="--extra stt --extra tts" \
  docker compose up -d --build
```

Parar: `docker compose down` (dados ficam no volume `ast_data`).

## Desenvolvimento

Gate canônico — um comando valida tudo (lint, testes, e2e e build):

```bash
make check
```

`make help` lista os alvos; cada um roda sozinho quando você quer isolar a falha:

| Alvo | O que roda |
|---|---|
| `make lint` | `ruff check . ../scripts` dentro de `backend/` |
| `make test` | `pytest -q` (backend) + `vitest run` (frontend) |
| `make e2e` | `playwright test` (frontend, WS e API mockados) |
| `make build` | `tsc --noEmit` + `vite build` |
| `make check` | `lint` + `test` + `e2e` + `build`, nesta ordem |

O `Makefile` encontra o `uv` em `~/.local/bin` sozinho, sem editar o `PATH`
(sobrescreva com `make check UV=/caminho/para/uv` se necessário).

Para subir os servidores de desenvolvimento:

```bash
# backend
cd backend
uv sync --extra dev
uv run uvicorn app.main:app --reload --port 8000

# frontend
cd frontend
npm install --include=dev
npm run dev          # proxy para localhost:8000
```

Equivalentes manuais, se `make` não estiver disponível:

```bash
cd backend && uv run ruff check . ../scripts && uv run pytest -q
cd frontend && npx tsc --noEmit && npx vitest run
cd frontend && npx playwright test
cd frontend && npm run build
```

## Estrutura

```
backend/app/          FastAPI, orquestrador, agentes, providers, SQLite
backend/config/       models.toml (perfis e overrides por idioma)
backend/scenarios/    cenários YAML por idioma
frontend/src/         React + Vite + Tailwind
docker/               Dockerfiles e nginx
docs/                 memória, arquitetura, ADRs, ledger e guias de agentes
scripts/              preflight e benchmarks (saída em docs/spikes)
```

## Documentação

- `docs/PROJECT.md` — memória viva do projeto e invariantes.
- `docs/ARCHITECTURE.md` — fluxo do turno, contratos e falhas.
- `docs/TASKS.md` — ledger, waves e critérios de aceite.
- `docs/agents/` — como o elefante briefa, revisa e integra.
- `docs/adr/` — decisões arquiteturais.

## Licença

MIT — ver `LICENSE`.

# ADR 0007 — Proxy socat para o Ollama (sem OLLAMA_HOST)

**Data:** 2026-11-20
**Status:** aceito

## Contexto

O Ollama roda como serviço systemd e escuta apenas em `127.0.0.1:11434`.
Containers não alcançam esse endereço. A correção canônica é reiniciar o serviço
com `OLLAMA_HOST=172.17.0.1:11434`, mas isso exige `sudo` interativo e faria o
host perder o acesso via `127.0.0.1` (usado pelos scripts de benchmark/eval).

## Decisão

Adicionar um serviço `ollama-proxy` no `compose.yaml` usando `alpine/socat` em
`network_mode: host`:

```
socat TCP-LISTEN:11435,bind=172.17.0.1,fork,reuseaddr TCP:127.0.0.1:11434
```

O container da API usa `http://host.docker.internal:11435/v1`; o host continua
usando `http://127.0.0.1:11434/v1`. Nenhum `sudo` é necessário e o Ollama não é
exposto à LAN.

## Consequências

- `docker compose up -d` sobe o proxy junto com a stack.
- O `172.17.0.1` é o gateway da bridge padrão do Docker; o proxy não cobre redes
  Docker customizadas com outro gateway. Se isso mudar, ajuste o bind.
- A correção canônica (`OLLAMA_HOST`) continua válida e dispensa o proxy; nesse
  caso, troque a URL de volta para `host.docker.internal:11434`.
- O proxy adiciona uma cópia TCP local; impacto de latência desprezível.

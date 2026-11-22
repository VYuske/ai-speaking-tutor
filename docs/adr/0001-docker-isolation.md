# ADR 0001 — Isolamento com Docker

**Data:** 2026-11-19
**Status:** aceito

## Contexto

O host tem Python 3.14 (sem wheels para o stack de áudio), Node 26 e Ollama na GPU.
Misturar a aplicação com o ambiente local gera divergência de dependências e
dificulta reproduzir falhas.

## Decisão

A aplicação roda em dois containers: `api` (Python 3.12 + uv) e `web` (build Vite
servido por nginx). O Ollama permanece no host, acessado por
`host.docker.internal` via `extra_hosts: host-gateway`.

- Portas: `8080` (web) e `8000` (api).
- `api` roda com usuário não-root (uid 10001).
- Dados em volume nomeado (`ast_data`); cache de modelos em `hf_cache`.
- Perfil default do compose: `skeleton` (sem modelos de áudio).
- GPU no container é um override opcional (`compose.gpu.yaml`), bloqueado hoje
  pela ausência do NVIDIA Container Toolkit.

## Consequências

- Trocar de máquina exige só Docker + Ollama, não Python/Node.
- Build da imagem `api` não instala extras de áudio por padrão (imagem leve).
- O áudio real na CPU dentro do container é o caminho do perfil `compact`.
- O acesso ao Ollama depende de ele escutar além de `127.0.0.1` (AST-000).

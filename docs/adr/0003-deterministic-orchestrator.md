# ADR 0003 — Orquestrador determinístico, sem LangGraph/LangChain

**Data:** 2026-11-19
**Status:** aceito

## Contexto

O LLM local é pequeno (4B, Q4). Modelos nesse porte são pouco confiáveis em tool
calling e em manter múltiplas responsabilidades. O fluxo do produto é simples:
turno, cenário, memória, correção.

## Decisão

O código controla o fluxo (máquina de estados, cenário, memória, TTS, persistência).
O LLM faz duas chamadas separadas:

1. Roleplay em streaming, texto livre.
2. Avaliação estruturada, JSON Schema, temperatura 0.

Sem LangGraph, sem LangChain. Cliente HTTP próprio contra endpoint
OpenAI-compatible (`httpx`), com contrato `LLMProvider`.

## Consequências

- Menos dependências e prompts totalmente visíveis (crítico em modelo pequeno).
- JSON confiável via structured outputs do Ollama (ou GBNF em llama.cpp).
- Multiagente/human-in-the-loop exigiriam reavaliar a decisão; não estão no MVP.
- Testes podem usar fakes determinísticos sem framework de agentes.

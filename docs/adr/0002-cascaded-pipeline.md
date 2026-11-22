# ADR 0002 — Cascata STT → LLM → TTS

**Data:** 2026-11-19
**Status:** aceito

## Contexto

A GPU disponível tem 6 GB. Speech-to-speech multilíngue local exige modelos que
não cabem ou não cobrem os quatro idiomas (Moshi é basicamente EN/FR; Qwen3-Omni e
MiniCPM-o são grandes demais).

## Decisão

Pipeline em cascata: VAD/trim → STT → LLM → TTS, cada etapa substituível por
configuração. No MVP, o STT transcreve a utterance inteira após o push-to-talk,
sem streaming.

## Consequências

- Latência soma etapas; meta é primeiro áudio < 2,5 s p95.
- Podemos medir e trocar cada peça isoladamente.
- TTS por sentença com fila ordenada no cliente reduz a latência percebida.
- Streaming de STT e barge-in ficam para depois (avaliar Pipecat/LiveKit nessa fase).

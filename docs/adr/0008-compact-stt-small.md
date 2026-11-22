# ADR 0008 — STT do perfil compact = Whisper small

**Data:** 2026-11-20
**Status:** aceito

## Contexto

O perfil `compact` roda STT na CPU (o container não tem NVIDIA Container Toolkit).
Com `faster-whisper large-v3-turbo` int8, o `first_audio_ms` medido ficou em
4,9–6,8 s, muito acima da meta de < 2,5 s. O gargalo era o STT (3,7–5,1 s), não o
LLM (~0,38 s) nem o TTS (~0,6–1,4 s).

## Decisão

Usar `faster-whisper small` int8 em `profiles.compact`.

Medições (áudio espeak, pesos quentes, mesmo harness):

| Modelo | pesos | STT p50 | first_audio p50 | p95 aprox. |
|---|---|---|---|---|
| large-v3-turbo | 1,6 GB | 4386 ms | 5632 ms | 6143 ms |
| **small** | **464 MB** | **869 ms** | **2262 ms** | **2923 ms** |
| base | 142 MB | 766 ms | 2042 ms | 4975 ms (ja ruim) |

`base` foi rejeitado: 0/4 transcrições japonesas utilizáveis. `small` mantém
transcrições semanticamente corretas nos 4 idiomas (com um erro em inglês no áudio
sintético: "large" → "orange").

## Consequências

- p50 de first audio: 2,26 s (meta cumprida); p95 de ja/zh ~2,9 s (meta não
  cumprida por ~0,4 s; o gargalo restante é o Kokoro/G2P — AST-039).
- Perfil `balanced` continua com Qwen3-ASR (GPU), sem alteração.
- A accuracy em voz humana ainda não foi medida; o áudio espeak é uma aproximação
  pessimista. AST-040 cobre isso.
- `hf_cache` mantém o large-v3-turbo (1,6 GB) sem uso; pode ser podado.

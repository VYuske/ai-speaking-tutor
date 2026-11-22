# AST-005 — Piper TTS para alemão no container

**Data:** 2026-11-20 (execuções às ~21:30 local, UTC−4)
**Perfil:** `compact` (`AST_PROFILE=compact`), imagem reconstruída com
`AST_INSTALL_EXTRAS="--extra stt --extra tts"`.
**Pacote:** `piper-tts 1.8.0` (onnxruntime 1.32.0, CPU), voz `de_DE-thorsten-medium`
(63 MB `.onnx` + 6 KB `.onnx.json`).
**Harness:** `scripts/audio_smoke.py` (WS real, um turno push-to-talk por idioma).

## 1. Resultado

| Idioma | STT | TTS | `stt.final` | `tts.audio` | Verdicto |
|---|---|---|---|---|---|
| en | `faster_whisper` | `kokoro` (`af_heart`) | "I would like a orange coffee, please." | 3 chunks | ok |
| ja | `faster_whisper` | `kokoro` (`jf_alpha`) | "これはコヒメス" | 3 chunks | ok |
| zh | `faster_whisper` | `kokoro` (`zf_xiaobei`) | "我要一杯咖啡" | 2 chunks | ok |
| de | `faster_whisper` | **`piper`** (`de_DE-thorsten-medium`) | "Ich möchte bitte eine Fahrkarte nach Berlin." | 2 chunks | ok |

Nenhum idioma cai para `fake`. `de` agora exige `tts == "piper"` no smoke e sai com
`session.ready.models = {"tts": "piper"}` e `warnings: []`.

Saída real da execução final (quente, dentro do container):

```
$ docker compose exec -T api python /app/scripts/audio_smoke.py
audio smoke → ws://127.0.0.1:8000/ws (en, ja, zh, de)
[en] ok in 7.5s
[ja] ok in 7.7s
[zh] ok in 6.8s
[de] ok in 12.1s

lang stt             tts      stt_ms  ttok_ms  audio_ms total_ms
----------------------------------------------------------------
en   faster_whisper  kokoro      903     1349      1978     6703
ja   faster_whisper  kokoro      923     1391      2672     6666
zh   faster_whisper  kokoro      946     1362      2690     5816
de   faster_whisper  piper      1148     1560      2414    11969

[en] 'I would like a orange coffee, please.' (3 tts chunk(s), 2.33s of input audio)
[ja] 'これはコヒメス' (3 tts chunk(s), 1.37s of input audio)
[zh] '我要一杯咖啡' (2 tts chunk(s), 1.66s of input audio)
[de] 'Ich möchte bitte eine Fahrkarte nach Berlin.' (2 tts chunk(s), 2.48s of input audio)

OK: real STT and real TTS (kokoro en/ja/zh, piper de) for every language
```

`de` fecha com `first_audio_ms` 2414 ms (1148 ms de STT + 1560 ms até o primeiro token);
o trecho de TTS do Piper fica em ~0,85 s para a primeira sentença a 22 050 Hz, na mesma
ordem do Kokoro (en ~0,63 s, ja ~1,3 s) mesmo sendo um segundo modelo carregado na sessão.

## 2. Test-first

O critério de aceite é o harness, atualizado **antes** da implementação: o caso `de`
passou de `tts_provider = None` (warning "pending AST-005") para `"piper"` obrigatório,
mais o warning removido. Rodado contra a imagem **sem** Piper (estado anterior):

```
$ docker compose exec -T api python /app/scripts/audio_smoke.py --languages de
audio smoke → ws://127.0.0.1:8000/ws (de)
[de] FAIL in 9.9s

lang stt             tts      stt_ms  ttok_ms  audio_ms total_ms
----------------------------------------------------------------
de   faster_whisper  fake       1004     6828      6922     9646

[de] 'Ich möchte bitte eine Fahrkarte nach Berlin.' (3 tts chunk(s), 2.48s of input audio)
[de] warning: tts provider 'piper' is not wired yet; using fake TTS
[de] FAIL: tts provider is 'fake', expected 'piper'

FAILED: 1 language(s) with contract violations, 0 error(s)
EXIT=1
```

Depois da implementação e do rebuild da imagem, o mesmo comando passa:

```
$ docker compose exec -T api python /app/scripts/audio_smoke.py --languages de
audio smoke → ws://127.0.0.1:8000/ws (de)
[de] ok in 11.4s

lang stt             tts      stt_ms  ttok_ms  audio_ms total_ms
----------------------------------------------------------------
de   faster_whisper  piper      1138     4757      5273     9967

[de] 'Ich möchte bitte eine Fahrkarte nach Berlin.' (2 tts chunk(s), 2.48s of input audio)

OK: real STT and real TTS (kokoro en/ja/zh, piper de) for every language
EXIT=0
```

## 3. Eventos WS `tts.audio` do `de` — antes/depois

Antes (`tts` fake): 3 chunks de um senoide de 24 000 Hz gerado por `FakeTTS`, com o
warning `tts provider 'piper' is not wired yet; using fake TTS` e `models.tts == "fake"`.

Depois (Piper), capturado no container:

```
session.ready models: {'llm': 'qwen3.5:4b', 'stt': 'faster_whisper', 'tts': 'piper'} warnings: []
tts.audio source=opening seq=0 sample_rate=22050 duration_s=2.624 wav_bytes=115756
          wav_header=22050Hz/1ch/16bit frames=57856 text='Guten Morgen! Wohin möchten Sie fahren?'
```

O WAV é mono 16-bit a 22 050 Hz (taxa nativa da voz Piper, não os 24 000 Hz do fake), com
duração proporcional ao texto — áudio real, não placeholder.

## 4. O que mudou

- `backend/app/providers/tts/piper.py` (novo): `PiperTTS` (`name = "piper"`) com
  `synthesize(text, language, voice) -> AudioChunk`. O `PiperVoice` (sessão onnxruntime)
  é criado no primeiro uso por idioma e cacheado com lock; `use_cuda=False` fixo (CPU
  only, o container não tem NVIDIA Container Toolkit). O PCM int16 devolvido pelo Piper é
  embrulhado em WAV por `app.core.audio.wav_bytes` e a duração vem de
  `pcm16_duration_s`. Voz: `voice` do argumento → `voice_by_lang[language]` → `tts.voice`.
- `backend/app/providers/registry.py`: `piper` saiu do conjunto "not wired yet" e entrou no
  mesmo `_with_fallback(...)` do Kokoro, com import lazy dentro do `build_tts`
  (`from app.providers.tts.piper import PiperTTS`), para o venv do host (sem extras)
  continuar importando o registry.
- `backend/pyproject.toml` + `backend/uv.lock`: `piper-tts>=1.8,<2` no extra `tts`
  (`uv lock` → `piper-tts 1.8.0`, `onnxruntime 1.32.0`, `pathvalidate 3.3.1`). O índice
  CPU do torch (`https://download.pytorch.org/whl/cpu`, 11 referências no lock) ficou
  intacto.
- `docker/api.Dockerfile`: no estágio *builder*, depois do `uv sync` dos extras,
  `python -m piper.download_voices --download-dir /app/voices de_DE-thorsten-medium`
  (o downloader oficial do próprio pacote, fonte `rhasspy/piper-voices`), seguido de
  `test -s` nos dois arquivos. Bloco `if import piper` → no-op quando a imagem é
  construída sem o extra `tts`. O `/app/voices` é copiado para o runtime e o
  `chown` já existente entrega os arquivos ao usuário `app`.
- `backend/config/models.toml`: só `[languages.de]` ganhou `model_dir = "/app/voices"`
  (a voz já estava configurada). `compact`, en/ja/zh e `balanced` intocados.
- `scripts/audio_smoke.py`: `de` exige `piper` + ≥1 chunk, warning "pending AST-005"
  removido, mensagem final ajustada.

## 5. Caminho de erro

O construtor valida a presença dos arquivos da voz (barato, sem carregar o modelo) para o
registry poder cair para `fake`; o carregamento do ONNX segue lazy:

```
$ docker compose exec -T api python -c "... build_tts({..., 'model_dir': '/app/voices-missing'}, w)"
registry -> fake
warning  -> tts provider 'piper' unavailable (piper voice files are missing in
            /app/voices-missing: de_DE-thorsten-medium.onnx, de_DE-thorsten-medium.onnx.json;
            rebuild the api image with AST_INSTALL_EXTRAS='--extra stt --extra tts'
            (docker/api.Dockerfile downloads the voices) or run
            'python -m piper.download_voices --download-dir /app/voices-missing
            de_DE-thorsten-medium'); using fake tts
direct   -> ProviderUnavailable: piper voice files are missing in /app/voices-missing: ...
no voice -> ProviderUnavailable: piper has no voice configured for 'fr'; set tts.voice in config/models.toml
```

No host sem o extra `tts` (venv de dev), o registry também cai para `fake` sem quebrar:

```
$ cd backend && uv run python -c "from app.providers.registry import build_tts; w=[]; print(build_tts({'provider':'piper','voice':'de_DE-thorsten-medium','model_dir':'/app/voices'}, w).name, w)"
fake ["tts provider 'piper' unavailable (piper-tts is not installed; build the api image with AST_INSTALL_EXTRAS='--extra tts'); using fake tts"]
```

Se a voz sumir depois do build, `synthesize` levanta `ProviderUnavailable` e o
orquestrador (`_emit_tts`) emite `warning tts_failed` e segue com texto — o turno não cai.

## 6. Dependências de sistema e tamanho

Nenhum pacote de sistema novo foi adicionado. O wheel `piper-tts` é `cp39-abi3`
manylinux e já traz `espeak-ng-data` embutido (~400 arquivos) e `espeakbridge.so`, que só
liga em `libc`:

```
$ ldd .../site-packages/piper/espeakbridge.so
	linux-vdso.so.1 (0x00007bd224bde000)
	libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007bd22494a000)
```

O `onnxruntime` usa `libgomp1`, que a imagem já instala para o torch CPU. A síntese foi
executada no runtime final (`22050 Hz`, 115 756 bytes de WAV) sem erro de biblioteca,
o que confirma a ausência de dependência faltante.

Custo na imagem: `+61 MB` (`/app/voices`) `+46 MB` (`piper`) `+78 MB` (`onnxruntime`) ≈
`185 MB`; imagem final `5.22 GB`. Nada de rede no runtime: a voz é baixada no build.

## 7. `make check`

```
$ make check
...
425 passed, 1 warning in 2.78s        # backend (host, sem piper instalado)
7 passed (4.1s)                       # playwright
✓ built in 584ms                      # tsc --noEmit + vite build
make check: lint, test, e2e e build verdes
```

`ruff check . ../scripts` limpo. O venv do host não instala o extra `tts`: o import do
Piper é lazy e coberto pelo fallback.

## 8. Follow-ups / questões abertas

- `audio_ms` do `de` (2,0–2,7 s em três execuções) fica entre `en` e `ja`; o `stt_ms` de
  `de` oscila (1,1–1,9 s) — é ruído do Whisper em áudio espeak, não do Piper. Não mexi
  (AST-037 cobre latência de ja/zh).
- O smoke usa `espeak-ng` como entrada falada; a validação com voz humana continua em
  AST-040.
- A voz escolhida (`thorsten-medium`) é 22 050 Hz; se algum cliente assumir 24 kHz do
  Kokoro, precisa ler `sample_rate` do evento (o `tts.audio` já manda o campo).
- Vozes Piper adicionais (outros idiomas) bastam entrar em `[languages.<code>]` + uma
  entrada no passo `piper.download_voices` do `docker/api.Dockerfile`.
- `docker compose build` sem `AST_INSTALL_EXTRAS` gera imagem sem Piper (o passo de
  download é no-op e o registry cai para `fake`): é a convenção herdada do AST-004.


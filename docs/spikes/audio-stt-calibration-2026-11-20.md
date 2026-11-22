# AST-037 — calibração do STT do perfil `compact` para a meta de latência

**Data:** 2026-11-20
**Perfil:** `compact` (`AST_PROFILE=compact`, LLM `qwen3.5:4b` no host via `ollama-proxy`,
Kokoro-82M na CPU, STT `faster_whisper` int8 na CPU)
**Hardware:** igual ao AST-004 — RTX 3050 Laptop 6 GB **sem NVIDIA Container Toolkit**, então
o container é CPU-only; CPU do host com 16 threads.
**Harness:** `scripts/audio_smoke.py` (WS real, um turno push-to-talk por idioma), sem
alteração. Imagem **não** foi reconstruída: `backend/config/` é montado em `/app/config:ro`,
então trocar `models.toml` + `docker compose restart api` basta.

**Config final (escolhida):**

```toml
[profiles.compact]
llm = "default"
stt = { provider = "faster_whisper", model = "small", device = "cpu", compute_type = "int8" }
tts = { provider = "kokoro", device = "cpu", voice_by_lang = { en = "af_heart", ja = "jf_alpha", zh = "zf_xiaobei" } }
```

Só a chave `model` do STT mudou (`large-v3-turbo` → `small`). `device`, `compute_type`, LLM,
TTS, `profiles.balanced` e os overrides de `[languages.*]` ficaram intactos.

## 1. Test-first (exceção para config, sem teste unitário)

Não existe teste unitário para "escolha de modelo": o critério é uma medição. O comando de
validação foi definido **antes** da mudança e ele falhava antes dela:

```bash
docker compose exec -T api python /app/scripts/audio_smoke.py   # imprime `audio_ms` (first_audio_ms)
```

Critério: `first_audio_ms` p95 < 2500. Na linha de base (seção 3) os quatro idiomas ficam
**4797–6143 ms** — o critério falha por ~2×. O comando é o mesmo depois da mudança; o que
muda é o número que ele imprime (seção 4). `make check` cobre a parte de config (parse do
TOML + testes que carregam `models.toml`).

## 2. Método

- **Repetições:** como o harness roda 1 turno por idioma, n é por idioma. `n = 3` para
  `large-v3-turbo` (linha de base), `n = 4` para `base`, `n = 5` para `small`. Todas as
  medições com pesos **já em cache** (quente).
- **p95:** com n ≤ 5 o p95 por posto (nearest-rank) é o **máximo observado**; reporto `p50`
  (mediana) e `p95 ≈ max` por idioma e o `p50` agregado dos 4 idiomas (n = 12–20 turnos).
- **Troca de modelo:** editar `models.toml` → `docker compose restart api` (sem rebuild) →
  rodar o harness. Cada modelo baixa uma vez para o volume `hf_cache`.

## 3. Linha de base — `large-v3-turbo` int8 (n = 3)

Saída real (primeira execução, quente):

```
$ docker compose exec -T api python /app/scripts/audio_smoke.py
audio smoke → ws://127.0.0.1:8000/ws (en, ja, zh, de)
[en] ok in 7.9s
[ja] ok in 13.0s
[zh] ok in 9.3s
[de] ok in 8.2s

lang stt             tts      stt_ms  ttok_ms  audio_ms total_ms
----------------------------------------------------------------
en   faster_whisper  kokoro     3734     4103      4797     7102
ja   faster_whisper  kokoro     4192     4597      5685    11974
zh   faster_whisper  kokoro     4462     4845      5862     8460
de   faster_whisper  fake       5030     5469      5858     8149

[en] 'I would like a large coffee, please.' (3 tts chunk(s), 2.33s of input audio)
[ja] 'これはコヒです' (3 tts chunk(s), 1.37s of input audio)
[zh] '我要一杯咖啡' (3 tts chunk(s), 1.66s of input audio)
[de] 'Ich möchte bitte eine Fahrtkarte nach Berlin.' (2 tts chunk(s), 2.48s of input audio)
[de] warning: tts provider 'piper' is not wired yet; using fake TTS
[de] warning: tts provider is 'fake'; German TTS is pending AST-005 (Piper)

OK: real STT for all languages; real TTS for en/ja/zh (de pending AST-005)
```

Agregado das 3 execuções (`stt_ms` / `audio_ms` = `first_audio_ms`):

| Idioma | `stt_ms` p50 | `first_audio_ms` p50 | `first_audio_ms` min–max | p95 ≈ |
|---|---|---|---|---|
| en | 4010 | 4994 | 4797–5053 | 5053 |
| ja | 4192 | 5685 | 5579–5933 | 5933 |
| zh | 4462 | 6128 | 5862–6143 | 6143 |
| de | 4563 | 5329 | 5179–5858 | 5858 |
| **todos** | **4386** | **5632** | 4797–6143 | **6143** |

Reproduz o AST-004 (4,9–6,8 s): **meta de 2,5 s perdida por ~2,3×**, gargalo no STT.

## 4. `small` int8 (n = 5) — escolhida

Última execução (a que fica no repo como validação final):

```
$ docker compose exec -T api python /app/scripts/audio_smoke.py
audio smoke → ws://127.0.0.1:8000/ws (en, ja, zh, de)
[en] ok in 11.9s
[ja] ok in 13.0s
[zh] ok in 9.1s
[de] ok in 7.5s

lang stt             tts      stt_ms  ttok_ms  audio_ms total_ms
----------------------------------------------------------------
en   faster_whisper  kokoro      917     1314      1902     6219
ja   faster_whisper  kokoro      867     1255      2466    10393
zh   faster_whisper  kokoro      848     1216      2391     5774
de   faster_whisper  fake       1017     1396      1654     6937

[en] 'I would like a orange coffee, please.' (3 tts chunk(s), 2.33s of input audio)
[ja] 'これはコヒメス' (4 tts chunk(s), 1.37s of input audio)
[zh] '我要一杯咖啡' (3 tts chunk(s), 1.66s of input audio)
[de] 'Ich möchte bitte eine Fahrtkarte nach Berlin.' (2 tts chunk(s), 2.48s of input audio)
[de] warning: tts provider 'piper' is not wired yet; using fake TTS
[de] warning: tts provider is 'fake'; German TTS is pending AST-005 (Piper)

OK: real STT for all languages; real TTS for en/ja/zh (de pending AST-005)
```

Agregado das 5 execuções:

| Idioma | `stt_ms` p50 | `first_audio_ms` p50 | `first_audio_ms` min–max | p95 ≈ |
|---|---|---|---|---|
| en | 917 | 2012 | 1878–2274 | 2274 |
| ja | 859 | 2352 | 2249–2871 | 2871 |
| zh | 868 | 2644 | 2391–2923 | 2923 |
| de | 1017 | 1654 | 1357–1964 | 1964 |
| **todos** | **869** | **2262** | 1357–2923 | **2923** |

STT caiu **~4,6×** (4386 → 869 ms). Pesos: 464 MB (`Systran/faster-whisper-small`), baixados
uma vez para `hf_cache` no primeiro `session.ready` (a primeira execução levou 65 s de
relógio, quase todo download).

## 5. `base` int8 (n = 4) — medida, **não** escolhida

```
$ # models.toml: model = "base"; docker compose restart api; docker compose exec -T api python /app/scripts/audio_smoke.py
lang stt             tts      stt_ms  ttok_ms  audio_ms total_ms
----------------------------------------------------------------
en   faster_whisper  kokoro      369      756      1609     8571
ja   faster_whisper  kokoro     3079     3526      4708     7959
zh   faster_whisper  kokoro      472      862      2250    10771
de   faster_whisper  fake       1238     1654      1787    13669

[en] 'I would like a orange coffee please.' (2 tts chunk(s), 2.33s of input audio)
[ja] 'あははひめす' (2 tts chunk(s), 1.37s of input audio)
[zh] '我要议呗咖啡' (2 tts chunk(s), 1.66s of input audio)
[de] 'Ich möchte mit einer Fahrkatter auf Berlin.' (3 tts chunk(s), 2.48s of input audio)
```

Agregado das 4 execuções:

| Idioma | `stt_ms` p50 | `first_audio_ms` p50 | `first_audio_ms` min–max | p95 ≈ |
|---|---|---|---|---|
| en | 324 | 1566 | 1260–1814 | 1814 |
| ja | **2844** | **4244** | 3474–4975 | **4975** |
| zh | 428 | 2362 | 2250–2812 | 2812 |
| de | 1212 | 1800 | 1715–1833 | 1833 |
| **todos** | **766** | **2042** | 1260–4975 | **4975** |

`base` é mais rápido em en/zh/de, mas **explode em ja** (1914–3669 ms de STT, ~9× o `small`):
o modelo pequeno não fecha bem o japonês com VAD + `beam_size=1`, gera mais segmentos e
entra em repetição (transcrições `あははひめす`, `あさかひです`, `トファコひです`,
`はっはっはひです` nas 4 execuções). Pior: p95 agregado 4975 ms, **pior que o p50 do
`small`**.

## 6. Comparação e decisão

| Modelo | pesos | `stt_ms` p50 (todos) | `first_audio_ms` p50 (todos) | pior idioma p50 | p95 ≈ | p50 < 2,5 s? | p95 < 2,5 s? |
|---|---|---|---|---|---|---|---|
| `large-v3-turbo` int8 | 1,6 GB | 4386 | 5632 | zh 6128 | 6143 | não | não |
| **`small` int8** | **464 MB** | **869** | **2262** | zh 2644 | **2923** | **sim** | **não** (ja/zh) |
| `base` int8 | 142 MB | 766 | 2042 | ja 4244 | 4975 | sim | não (ja) |

**Escolhido: `small` int8.** Critérios:

1. Melhor `first_audio_ms` p50 **agregado** (2262 ms; média dos p50 por idioma = 2166 ms)
   entre as três opções — `base` tem p50 agregado 2042 ms, mas só porque en/zh/de melhoram
   muito enquanto ja piora 1,8×; a média dos p50 por idioma do `base` é 2493 ms, **pior**.
2. Cumpre a regra do brief de seguir adiante só se p50 > 3,0 s: os quatro p50 do `small` são
   1,65 / 2,01 / 2,35 / 2,64 s — nenhum passa de 3,0 s.
3. Transcrições **não vazias e semanticamente corretas** nos quatro idiomas (seção 7);
   `base` degrada en, ja e zh.
4. Metade do `large-v3-turbo` em disco e ~4,6× menos STT, com o mesmo contrato:
   `session.ready.models.stt == "faster_whisper"`, TTS real em en/ja/zh, `de` ainda `fake`
   (AST-005).

## 7. Acurácia: o trade-off, sem maquiagem

Áudio de entrada é o WAV do `espeak-ng` do harness (voz sintética, não humana), o mesmo
para os três modelos. Frase-alvo e resultado por execução:

| Idioma | frase enviada pelo espeak | `large-v3-turbo` (3 runs) | `small` (5 runs) | `base` (4 runs) |
|---|---|---|---|---|
| en | I would like a large coffee, please. | `I would like a large coffee, please.` **(3/3 idêntico)** | `I would like a orange coffee, please.` (5/5; "large" → "orange") | `I would like a orange coffee please.` (4/4; pior: também perde a vírgula) |
| ja | これはコーヒーです。 | `これはコヒです` (3/3) | `これはコヒメス` (5/5; erra a terminação です) | `あははひめす` / `あさかひです` / `トファコひです` / `はっはっはひです` (0/4 útil) |
| zh | 我要一杯咖啡。 | `我要一杯咖啡` (3/3) | `我要一杯咖啡` (5/5 idêntico) | `我要议呗咖啡` (4/4; 一→议, 杯→呗) |
| de | Ich möchte bitte eine Fahrkarte nach Berlin. | `Ich möchte bitte eine Fahrtkarte nach Berlin.` (3/3) | `Ich möchte bitte eine Fahrkarte nach Berlin.` (5/5 **melhor** que a base) | `Ich möchte mit einer Fahrkatter auf Berlin.` / `… auf Berlin.` / `… aus Berlin.` / `… ausmelieren.` (0/4) |

Degradação declarada, do melhor para o pior: `large-v3-turbo` > `small` > `base`.

- **en**: `small` e `base` trocam "large" por "orange" (vogal longa do espeak). Para um tutor
  de conversação isso é ruído tolerável; o tutor corrige o aprendiz, então um substantivo
  errado na fala do aluno **muda o feedback**. É a degradação mais relevante para produto.
- **ja**: `small` mantém o conteúdo (`コヒ`) e erra só a cópula; `base` perde a frase inteira.
  Como ja/zh são escritas não-latinas, é onde `small` mais sofre em fala humana — **este é o
  risco a validar com áudio real**, não com espeak.
- **zh**: `small` empata com a base em 5/5 execuções; `base` erra dois caracteres.
- **de**: `small` é o **único** que acerta "Fahrkarte" (a base escreve "Fahrtkarte" em 3/3,
  hipercorreção de quem já viu muito alemão).

Nenhuma das três opções devolve transcrição vazia — o critério de aceite "transcrição não
vazia" vale para todas. O critério de desempate foi a qualidade por idioma + p50 agregado.

## 8. Onde o tempo ficou e por que o p95 ainda não fecha

Decomposição em p50 (do início do turno; `first_audio_ms` inclui o STT):

| Modelo | STT | LLM TTFB (`ttok − stt`) | 1ª sentença Kokoro (`first_audio − ttok`) | `first_audio` |
|---|---|---|---|---|
| `large-v3-turbo` (en) | 4010 | 384 | 984 | 4994 |
| **`small` (en)** | **917** | **397** | **1095** | **2012** |
| `small` (ja) | 859 | 396 | 1093 | 2352 |
| `small` (zh) | 868 | 380 | 1396 | 2644 |
| `small` (de) | 1017 | 379 | 637 | 1654 |

O STT deixou de ser o gargalo: com `small` ele custa ~0,87 s e o que sobra é **LLM TTFB
(~0,38 s) + primeira sentença do Kokoro (0,6–1,4 s)**. en e de fecham sob 2,5 s de forma
estável (p95 2,27 s e 1,96 s); **ja e zh não** (p95 2,87 s e 2,92 s) porque o primeiro chunk
do Kokoro nesses idiomas custa ~1,1–1,4 s (pipeline `misaki` + `pyopenjtalk`/G2P zh
inicializados na primeira sentença).

## 9. Veredito da meta

- **p50 `first_audio_ms`: 2,26 s agregado (1,65–2,64 s por idioma) — meta de 2,5 s
  cumprida.** Antes: 5,63 s.
- **p95 `first_audio_ms`: 2,92 s (ja 2,87 s, zh 2,92 s) — meta de 2,5 s NÃO cumprida.**
  Resíduo: **+0,37 s a +0,42 s** em ja/zh, 100% na primeira sentença do TTS. Não é mais
  problema de STT.
- `first_audio_ms` p95 de en = 2,27 s e de de = 1,96 s: dentro da meta.

## 10. Próximas alavancas (em ordem de custo/benefício)

1. **Aquecer o Kokoro no `session.ready`** (sintetizar ~1 sentença curta descartável por
   idioma, ou inicializar o `KPipeline` + G2P no construtor do provider). É exatamente o
   resíduo de ja/zh (1,1–1,4 s → ~0,6 s esperado). Toca `app/providers/tts/kokoro.py`, fora
   do escopo desta task.
2. **Fatiar a primeira sentença**: hoje o `first_audio` só sai quando a primeira *sentença*
   inteira do LLM está pronta; mandar por cláusula corta mais algumas centenas de ms.
3. **Compartilhar a instância do STT entre idiomas**: `ProviderCache` é chaveado por idioma,
   então cada `session.ready` recarrega o Whisper do disco (~1 s com `large-v3-turbo`,
   ~0,2 s com `small`). Não afeta `first_audio_ms` (o `ready` precede o turno), mas afeta o
   tempo até poder falar.
4. **Se a acurácia em ja/zh com fala humana real doer**, há espaço para override por idioma
   (`[languages.ja].stt`) com um modelo maior só nesses casos — **não** fiz: o brief proíbe
   tocar `[languages.*]`, e isso vira decisão do elefante.
5. **GPU**: `balanced`/Qwen3-ASR segue bloqueado por NVIDIA Container Toolkit no host
   (AST-006).

## 11. Validação (saída real)

```
$ docker compose restart api && docker compose ps
NAME                      IMAGE                   COMMAND                  SERVICE   CREATED          STATUS                   PORTS
ai-speaking-tutor-api-1   ai-speaking-tutor-api   "uvicorn app/main:ap…"   api       18 minutes ago   Up 59 seconds (healthy)  0.0.0.0:8000->8000/tcp

$ docker compose ps
NAME                               IMAGE                   COMMAND                  SERVICE        CREATED          STATUS                    PORTS
ai-speaking-tutor-api-1            ai-speaking-tutor-api   "uvicorn app/main:ap…"   api            18 minutes ago   Up 59 seconds (healthy)   0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp
ai-speaking-tutor-ollama-proxy-1   alpine/socat            "socat TCP-LISTEN…"      ollama-proxy   55 minutes ago   Up 55 minutes
ai-speaking-tutor-web-1            ai-speaking-tutor-web   "/docker-entrypoint…"    web            45 hours ago     Up About an hour          0.0.0.0:8080->80/tcp, [::]:8080->80/tcp

$ make check
425 passed, 1 warning in 2.44s
 ✓ src/lib/audio.test.ts (7 tests)
 Test Files  1 passed (1) / Tests  7 passed (7)
Running 7 tests using 7 workers … 7 passed (3.9s)
> tsc --noEmit && vite build → ✓ built in 524ms
make check: lint, test, e2e e build verdes
```

Estado do `hf_cache` depois da calibração (nada foi apagado):

```
models--Systran--faster-whisper-base                 142M
models--Systran--faster-whisper-small                464M
models--mobiuslabsgmbh--faster-whisper-large-v3-turbo 1,6G
models--hexgrad--Kokoro-82M
```

## 12. O que mudou

- `backend/config/models.toml`: `profiles.compact.stt.model` = `large-v3-turbo` → `small`
  (uma linha; `device=cpu`, `compute_type=int8` mantidos).
- `docs/spikes/audio-stt-calibration-2026-11-20.md`: este relatório.

Nada mais: sem app code, sem Dockerfile, sem dependências, sem mudança de contrato,
`profiles.balanced`/`skeleton` e `[languages.*]` intactos. Sem rebuild de imagem.

## 13. Riscos e pendências

- **Acurácia medida com `espeak-ng`**, não com fala humana: a degradação real de `small`
  em ja/en pode ser maior ou menor. Precisa de uma rodada com gravações humanas
  (as mesmas 4 frases) antes de fechar a decisão de produto.
- p95 de ja/zh em 2,9 s: a meta só fecha com a alavanca 1 ou 2 da seção 10.
- `hf_cache` guarda agora três modelos (~2,2 GB). Não apaguei nada; se o disco incomodar,
  o `large-v3-turbo` (1,6 GB) é o candidato natural a sair — decisão do elefante.
- O número absoluto depende da carga da máquina (o LLM roda no host, competindo por CPU
  quando o Whisper roda): os `total_ms` variam de 3,8 s a 18 s entre execuções; o que
  reporto é `stt_ms` e `first_audio_ms`, que são estáveis.

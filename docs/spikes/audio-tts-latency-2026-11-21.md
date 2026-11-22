# AST-039 — latência do primeiro áudio em ja/zh: decomposição por estágio

**Data:** 2026-11-21 (~21:30–22:00 local, UTC−4)
**Perfil:** `compact` (`AST_PROFILE=compact`), LLM `qwen3.5:4b` no host via
`ollama-proxy`, Kokoro-82M na CPU (16 threads lógicas, 8 físicas), STT
`faster_whisper` `small` int8 na CPU. Imagem inalterada (sem rebuild).
**Harness:** `scripts/audio_smoke.py` (WS real, um turno push-to-talk por idioma),
agora com as colunas `tts1_ms` / `tts1_s`. Carga do host durante a sessão:
`load average` 5–7,5 (o Ollama roda no host e compete por CPU).

## 1. Veredito

- **A hipótese do warm-up de TTS está rejeitada com medição**: o custo de
  inicialização do Kokoro (criação do `KPipeline` + primeira chamada) é real —
  ~1,3 s em ja e ~2,1 s em zh por processo — mas **já é pago por
  `speak_opening`**, que sintetiza a fala de abertura antes do primeiro turno.
  No turno, a primeira chamada não é mais lenta que a segunda (§4).
  `_warmup` **não foi alterado**: ganho medido = 0 ms de `first_audio_ms`, e um
  warm-up concorrente à abertura exigiria um lock novo em `_pipeline`.
- **p95 < 2,5 s continua NÃO cumprido em ja/zh** (p95 ≈ 3,55 s / 3,31 s) e
  também em `en` (4,10 s) nesta janela.
- O estágio dominante em ja/zh é a **primeira sentença do Kokoro**: 49–51 % do
  `first_audio_ms` (§5). Não é inicialização, é **custo por sentença
  proporcional à duração do áudio** (0,33–0,59 s de síntese por segundo de
  áudio), somado ao tamanho da sentença que o LLM gera (2,6–5,4 s de áudio).
- A alavanca que fecha a meta é **fatiar o primeiro chunk por cláusula** no
  `SentenceChunker` (`、，,`), fora do escopo desta task — estimativa em §6.1.

## 2. Método

- **Repetições:** o harness roda 1 turno por idioma, então `n = 8` por idioma
  (32 turnos). Todas as execuções com pesos em cache (quente).
- **p95:** com `n = 8` o p95 por posto (nearest-rank) é o **máximo observado**;
  reporto `p50` (mediana) e `p95 ≈ max` por idioma, como no AST-037.
- **Estágios** (todos a partir do começo do turno, que é `audio.end`):
  - `stt_ms` — Whisper (`turn.end.timings.stt_ms`);
  - `ttok_ms` — até o primeiro token do LLM, **cumulativo** (inclui o STT);
  - `tts1_ms = first_audio_ms − ttok_ms` — espera pela primeira sentença
    completa + síntese dela;
  - `tts1_s` — duração do áudio dessa primeira sentença do Kokoro.
- Para separar "esperar o LLM terminar a sentença" de "sintetizar", uma sonda
  fora do repo (WS + `llm.delta` com timestamp, `§5`) marca o instante em que o
  texto acumulado já contém a primeira sentença.

## 3. Baseline e resultado por estágio (n = 8)

Oito execuções consecutivas com o harness já instrumentado (mesmo container,
mesma config, sem rebuild entre elas). Bloco de 4 linhas = uma execução do
`audio_smoke.py` (en, ja, zh, de):

```
lang stt             tts      stt_ms  ttok_ms  tts1_ms  tts1_s  audio_ms total_ms
---------------------------------------------------------------------------------
en   faster_whisper  kokoro      992     1414     1579    1.82      2993     9727
ja   faster_whisper  kokoro      938     1354     1301    3.17      2655    12038
zh   faster_whisper  kokoro      988     1386     1435    3.45      2821     6587
de   faster_whisper  piper      1032     1672      195    1.92      1867     4691

en   faster_whisper  kokoro     1031     1463     1287    1.95      2750     7114
ja   faster_whisper  kokoro      963     1375     2178    4.60      3553    12271
zh   faster_whisper  kokoro      946     1349     1319    2.70      2668     6668
de   faster_whisper  piper      1180     1594      254    2.33      1848    10250

en   faster_whisper  kokoro      973     1402     1317    2.58      2719     7085
ja   faster_whisper  kokoro      946     1359     1541    3.38      2900    14018
zh   faster_whisper  kokoro      970     1371     1168    2.58      2539     6525
de   faster_whisper  piper      1218     1642      369    3.02      2011    10680

en   faster_whisper  kokoro     1002     1424      841    1.77      2265     7079
ja   faster_whisper  kokoro      862     1278     1228    3.17      2506    12080
zh   faster_whisper  kokoro     1054     1455     1380    2.70      2835     4271
de   faster_whisper  piper      1293     1706      247    2.04      1953    11208

en   faster_whisper  kokoro      957     1384     1768    3.30      3152     9814
ja   faster_whisper  kokoro      912     1328     1424    3.17      2752     6612
zh   faster_whisper  kokoro      996     1401     1616    3.50      3017     5961
de   faster_whisper  piper      1230     1646      286    2.39      1932     7015

en   faster_whisper  kokoro     1029     1469      996    2.27      2465     6539
ja   faster_whisper  kokoro     1007     1429     1303    3.17      2732    13823
zh   faster_whisper  kokoro     1004     1406     1904    3.23      3310     5623
de   faster_whisper  piper      1349     1777      249    2.07      2026     4808

en   faster_whisper  kokoro     1057     1489     2609    5.40      4098     7490
ja   faster_whisper  kokoro      935     1354     1396    3.17      2750    12083
zh   faster_whisper  kokoro     1029     1428     1343    3.17      2771     4244
de   faster_whisper  piper      1175     1581      236    1.70      1817    15326

en   faster_whisper  kokoro      982     1380      692    1.48      2072     9486
ja   faster_whisper  kokoro      861     1255     1538    3.25      2793    14257
zh   faster_whisper  kokoro      896     1263     1372    3.42      2635     4985
de   faster_whisper  piper      1247     1648      102    0.94      1750    15418
```

Agregado (p50 de 8 por idioma, 32 no total):

| Idioma | `stt_ms` p50 | `ttok_ms` p50 | `tts1_ms` p50 | `tts1_s` p50 | `first_audio_ms` p50 | min–max | p95 ≈ |
|---|---|---|---|---|---|---|---|
| en | 997 | 1419 | 1302 | 2,11 s | 2735 | 2072–4098 | **4098** |
| ja | 937 | 1354 | **1410** | 3,17 s | 2751 | 2506–3553 | **3553** |
| zh | 992 | 1394 | **1376** | 3,20 s | 2796 | 2539–3310 | **3310** |
| de | 1224 | 1647 | 248 | 2,07 s | 1900 | 1750–2026 | 2026 |
| **todos** | **999** | **1410** | **1487** | — | **2694** | 1750–4098 | 4098 |

Janela anterior para comparar (4 execuções ~21:40, mesma imagem e config, antes
de o harness imprimir as colunas; `first_audio`): en p50 2119 / p95 2295; ja
p50 2543 / p95 2651; zh p50 2712 / p95 3001; de p50 1705 / p95 2234. A
diferença **não é do código**: `tts1_s` (tamanho da primeira
sentença gerada pelo LLM) subiu de ~1,8 s para 2,11 s em en e o host ficou mais
carregado (+40 ms de STT e +20 ms de TTFB no mesmo período). O que é estável
nas duas janelas é a **estrutura**: `stt ≈ 1,0 s`, `ttfb ≈ 0,4 s`,
`tts1 ≈ 1,4 s` em ja/zh e `0,25 s` em de.

## 4. A hipótese do warm-up de TTS: medida e rejeitada

### 4.1 Primeira chamada vs segunda (processo novo, mesmo texto)

```
$ docker compose exec -T api python -c "... KokoroTTS; KPipeline; 3x _synthesize ..."
ja: KPipeline creation=1011ms | call#1=1054ms call#2=761ms call#3=740ms | audio=3.17s
zh: KPipeline creation=1272ms | call#1=1665ms call#2=892ms call#3=840ms | audio=3.50s
```

A inicialização existe (criação do pipeline 1,0–1,3 s + ~0,3 s ja / ~0,8 s zh
de primeira chamada), mas **não é por chamada**: da segunda em diante o custo é
o da duração do áudio.

### 4.2 Onde essa inicialização é paga hoje: na abertura, não no turno

Servidor recém-reiniciado (`docker compose restart api`), primeira sessão de
cada idioma = processo sem pipeline daquele idioma:

| Sessão | `tts.audio` da abertura | `first_audio_ms` do turno 1 |
|---|---|---|
| ja **fria** (1ª após restart) | 6358 ms | **2560** |
| zh **fria** (1ª após restart) | 3431 ms | **2541** |
| ja quente (sessão seguinte) | 1097 ms | 3489 |
| zh quente (sessão seguinte) | 1038 ms | 3681 |

`speak_opening` roda logo depois de `session.ready` e antes de qualquer turno,
então a criação do `KPipeline` (e o primeiro uso do G2P `pyopenjtalk`/zh) é paga
ali: o `first_audio_ms` do turno **não piora** com o processo frio (2560/2541 ms
contra 2506–3553 ms nas execuções quentes; a diferença é o tamanho da sentença
que o LLM gerou, não a inicialização).

Confirmado de novo na sequência de validação, `docker compose restart api` e
smoke imediato (a 1ª sessão de cada idioma é a fria):

```
=== RUN 1 (post-restart) ===
en   faster_whisper  kokoro      987     1411     1936    4.78      3347     7156
ja   faster_whisper  kokoro      951     1352     1059    3.17      2411    13064
zh   faster_whisper  kokoro      927     1300     1880    4.75      3180     7282
de   faster_whisper  piper      1127     1534      209    1.62      1743     4008
```

ja frio deu **2411 ms**, o menor `first_audio` da noite — a inicialização do
Kokoro não entra no caminho crítico do turno.

### 4.3 Consequência para `TurnRunner._warmup`

- Ganho de `first_audio_ms`: **0 ms** (a inicialização já aconteceu antes do
  turno).
- Risco: hoje `KokoroTTS._pipeline` **não tem lock** (`if code not in
  self._pipelines`). Um warm-up criado em `session.ready` roda concorrente ao
  `speak_opening` do mesmo idioma e construíria o `KPipeline` duas vezes (para
  ja, serializado pelo `_JA_PATCH_LOCK`: +1,0 s na abertura).
- **Decisão: não alterar `turn.py` nem `kokoro.py`.** O menor diff que satisfaz
  o critério é só o harness. O warm-up de TTS fica registrado como
  contraindicado enquanto a abertura existir.

## 5. Onde o tempo vai (4 estágios, n = 3 por idioma)

Sonda fora do repo: WS real + timestamp de cada `llm.delta`; `llm_finish` é o
instante em que o texto acumulado já contém a primeira sentença (o chunker
emite no terminador) e `kokoro_1` é o resto até o `tts.audio` — ou seja, a
síntese daquela sentença.

| Idioma | STT | TTFB do LLM (pós-STT) | LLM até fechar a 1ª frase | **Kokoro 1ª frase** | `first_audio` | áudio da 1ª frase |
|---|---|---|---|---|---|---|
| en | 909 | 697 | 91 | 522 | 2199 | 1,63 s |
| en | 1025 | 419 | 47 | 1088 | 2576 | 1,48 s |
| en | 1043 | 430 | 115 | 1051 | 2635 | 2,00 s |
| ja | 933 | 413 | 113 | **1202** | 2655 | 3,18 s |
| ja | 949 | 414 | 117 | **1036** | 2509 | 3,18 s |
| ja | 982 | 416 | 115 | **1292** | 2797 | 3,18 s |
| zh | 1074 | 393 | 156 | **1374** | 2991 | 3,50 s |
| zh | 1003 | 400 | 140 | **1496** | 3024 | 3,48 s |
| zh | 943 | 393 | 160 | **2052** | 3541 | 3,45 s |

Leitura (p50 da janela de 8 execuções, §3):

| Idioma | STT | TTFB | Kokoro 1ª frase | `first_audio` | share do Kokoro |
|---|---|---|---|---|---|
| ja | 937 (34 %) | 417 (15 %) | 1410 (51 %) | 2751 | **51 %** |
| zh | 992 (35 %) | 402 (14 %) | 1376 (49 %) | 2796 | **49 %** |
| en | 997 (36 %) | 422 (15 %) | 1302 (48 %) | 2735 | 48 % |
| de | 1224 (64 %) | 423 (22 %) | 248 (13 %) | 1900 | 13 % |

- O **LLM não é o gargalo**: 0,39–0,43 s de TTFB e 47–160 ms para fechar a
  primeira frase. *Keep-alive*/prompt cache não move a agulha.
- O **STT está no alvo** do AST-037 (~0,94–1,0 s).
- O que sobra é **síntese proporcional ao áudio**: 0,33–0,41 s de síntese por
  segundo de áudio em ja e 0,39–0,59 s em zh. Uma sentença de 3,2 s custa
  ~1,1–1,3 s; uma de 4,6 s custa ~2,2 s. É por isso que ja/zh estouram: o
  Kokoro tem vozes mais lentas que o Piper (de: 0,25 s para 2 s de áudio, ~8×
  mais rápido por segundo de áudio).

## 6. Próximas alavancas (com número medido ou estimado)

### 6.1 Fatiar o primeiro chunk por cláusula — **fecha a meta** (fora do escopo)

`backend/app/orchestrator/chunker.py` só corta em terminador de frase
(`。！？；`, `.!?…`) e, acima de `max_chars=400`, faz `rfind(" ")` — que **nunca
casa em ja/zh** (sem espaço). Resultado: o primeiro chunk é sempre a sentença
inteira (2,6–5,4 s de áudio), e o áudio só começa depois da síntese inteira.

Estimativa com os tempos medidos acima, cortando o primeiro chunk na primeira
vírgula (`、`, `，`, `,`) ou num teto curto de caracteres para CJK:

| Idioma | 1º chunk atual | 1º chunk por cláusula | `first_audio` estimado |
|---|---|---|---|
| ja | `コーヒーを温めてください。` (3,18 s de áudio, 1,1–1,3 s de síntese) | `はい、` (~0,6 s, ~0,2 s) | 937 + 417 + ~80 + ~200 ≈ **1,6 s** |
| zh | `好的，您要点什么菜吗？` (3,5 s, 1,4–2,0 s) | `好的，` (~0,5 s, ~0,15 s) | 992 + 402 + ~80 + ~150 ≈ **1,6 s** |

Sobra folga grande para 2,5 s e, de brinde, mata a variância (a execução 7 de
`en` foi a 4,1 s porque a primeira sentença tinha 5,4 s de áudio). Precisa de
teste novo no chunker (`min_clause_chars` para não virar `嗯、`) — **não
implementado aqui**: arquivo fora da lista permitida desta task.

### 6.2 Threads intra-op do Kokoro: 10 → 6 — medido, -0,15 a -0,65 s

O `torch` abre 10 threads nesta máquina; o Whisper (ctranslate2) e o Ollama do
host também querem CPU. Bench intercalado (8 repetições por configuração, mesmo
processo, `torch.set_num_threads`):

| Config | ja, 1 sentença de 3,18 s | zh, 1 sentença de 3,50 s | ja, turno de 3 sentenças | zh, turno de 3 sentenças |
|---|---|---|---|---|
| 10 threads (default do torch) | 732 ms | 837 ms | 2651 ms | 2725 ms |
| **6 threads** | **583 ms** | **617 ms** | **2266 ms** | **2074 ms** |
| 4 threads | 673 ms | 727 ms | 2316 ms | 2385 ms |

-15 % (ja) a -24 % (zh) no turno inteiro; ~-0,2 s no primeiro áudio de ja e
~-0,25/-0,4 s no de zh — o que colocaria ja em ~2,55 s e zh em ~2,4 s de p50,
mas ainda não garante p95 < 2,5 s sozinho. **Não implementado**: (a) a imagem
bakeia `backend/app` (precisa rebuild, não `restart`), (b) o venv do host não
tem `torch`, então o teste do comportamento não roda no `make check` — precisa
de uma task própria com casa de teste (ex.: extra `tts` + `@pytest.mark.models`).

### 6.3 Rejeitadas

- **Warm-up de TTS no `_warmup`**: 0 ms de ganho e corrida com a abertura (§4.3).
- **LLM keep-alive / prompt cache**: o TTFB já é 0,4 s (§5).
- **Outro TTS para ja/zh**: o Piper medido em `de` gasta 0,25 s para 2 s de
  áudio, mas não tem voz ja/zh oficial; trocar de engine é decisão de produto
  (AST-006).

## 7. O que mudou

- `scripts/audio_smoke.py`: colunas `tts1_ms` (`first_audio_ms − ttok_ms`) e
  `tts1_s` (duração do áudio da primeira sentença do reply) na tabela, mais uma
  linha por idioma com o texto dessa sentença. Sem isso não dá para distinguir
  "primeira sentença longa" de "síntese lenta" lendo só `audio_ms`. Nenhum
  check existente mudou (`tts_chunks`, STT não vazio, `feedback.card`,
  provider real), nenhuma dependência nova.
- `docs/spikes/audio-tts-latency-2026-11-21.md`: este relatório.
- **Nada mais**: `kokoro.py`, `turn.py`, `chunker.py`, config, registry,
  prompts, avaliador, snapshots e frontend intocados. Sem rebuild de imagem.

## 8. Validação (saída real)

Antes da mudança do harness (sem as colunas de estágio), 1ª de 4 execuções
(linhas de detalhe/transcrição omitidas):

```
$ docker compose exec -T api python /app/scripts/audio_smoke.py
audio smoke → ws://127.0.0.1:8000/ws (en, ja, zh, de)
[en] ok in 7.2s / [ja] ok in 13.1s / [zh] ok in 6.9s / [de] ok in 7.0s

lang stt             tts      stt_ms  ttok_ms  audio_ms total_ms
----------------------------------------------------------------
en   faster_whisper  kokoro      898     1334      2295     6480
ja   faster_whisper  kokoro      864     1293      2332    12070
zh   faster_whisper  kokoro      853     1244      3001     5960
de   faster_whisper  piper      1084     1498      2234     6876
...
OK: real STT and real TTS (kokoro en/ja/zh, piper de) for every language
```

Depois (1ª execução de §3):

```
$ docker compose exec -T api python /app/scripts/audio_smoke.py
lang stt             tts      stt_ms  ttok_ms  tts1_ms  tts1_s  audio_ms total_ms
---------------------------------------------------------------------------------
en   faster_whisper  kokoro      992     1414     1579    1.82      2993     9727
ja   faster_whisper  kokoro      938     1354     1301    3.17      2655    12038
zh   faster_whisper  kokoro      988     1386     1435    3.45      2821     6587
de   faster_whisper  piper      1032     1672      195    1.92      1867     4691

[ja] first reply sentence (3.17s of audio): 'コーヒーを温めてください。'
[zh] first reply sentence (3.45s of audio): '好嘞，您想要什么菜呢？'
OK: real STT and real TTS (kokoro en/ja/zh, piper de) for every language
```

Gate canônico na raiz:

```
$ cd backend && uv run ruff check . ../scripts
All checks passed!
$ uv run pytest -q
425 passed, 1 warning in 2.80s
$ cd frontend && npx vitest run
 Test Files  1 passed (1) / Tests  7 passed (7)
$ npx playwright test
 7 passed (4.3s)
$ npm run build
✓ 39 modules transformed.  ✓ built in 624ms
make check: lint, test, e2e e build verdes
```

O harness mudou, então a validação dele é a execução real acima (o script não
tem teste unitário; nenhum teste em `backend/tests` importa `audio_smoke`).

## 9. Riscos e pendências

- A meta de p95 **não** fecha com o que está dentro do escopo desta task; o
  caminho é o corte por cláusula (§6.1), que muda o áudio e precisa de teste
  próprio no chunker (e de uma escuta para não picar a fala).
- As medições usam `espeak-ng` como entrada falada e o LLM real: a variância de
  `first_audio` vem do **tamanho da primeira sentença gerada** (2,6–5,4 s de
  áudio), não do código. Comparar janelas com carga de host diferente engana.
- `torch.set_num_threads` é global no processo; se o Kokoro virar multi-sessão
  concorrente, revisar (o semáforo do `turn.py` permite 2 sínteses em paralelo).
- Observado de passagem (fora do escopo): o LLM às vezes devolve markdown na
  fala (`"You'd like an **orange** coffee, please."`) e o Kokoro sintetiza a
  sentença com os asteriscos — 4,78 s de áudio em vez de ~2 s, que sozinho
  levou o `en` a 3,3 s nessa execução. Vale um sanitizador antes do TTS.

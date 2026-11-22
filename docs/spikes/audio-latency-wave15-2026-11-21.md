# AST-041 + AST-043 — primeira cláusula limpa e threads do Kokoro

**Data:** 2026-11-21 (~22:00–23:00 local, UTC−4)
**Perfil:** `compact` (`AST_PROFILE=compact`), LLM `qwen3.5:4b` no host via
`ollama-proxy`, Kokoro-82M na CPU (16 lógicas / 8 físicas, `cpu_threads = 6`),
STT `faster_whisper` `small` int8 na CPU, Piper para `de`. Imagem reconstruída
com `AST_INSTALL_EXTRAS="--extra stt --extra tts"` (5,22 GB).
**Harness:** `scripts/audio_smoke.py` (WS real, 1 turno push-to-talk por idioma).
Providers reais em todas as execuções (`stt=faster_whisper`, `tts=kokoro`
en/ja/zh, `tts=piper` de).

## 1. Veredito

- **Meta cumprida em ja/zh.** `first_audio` p50: ja **2005 ms**, zh **1930 ms**
  (antes 3163 / 3292 ms). O p95 (máximo de 6 execuções, nearest-rank) também
  fecha: ja **2197 ms**, zh **2256 ms** — os dois abaixo de 2,5 s.
- **Markdown não chega mais ao TTS.** Medição direta do Kokoro: a mesma frase com
  `**orange**` custa **1045 ms e 4,78 s de áudio**; sanitizada, **409 ms e 2,45 s**
  (−61 % de tempo, −2,3 s de áudio). Uma lista com heading e backticks: 854 ms /
  4,88 s → 463 ms / 2,30 s. Nenhum `*`, `` ` ``, `#` ou `](` em nenhum
  `tts.audio` real (sonda com 3 turnos reais, en/ja/zh).
- **Threads do Kokoro: −32 % a −36 % por sentença** medidas neste host
  (`torch.set_num_threads(6)` aplicado em `KokoroTTS.__init__`).
- **`en` deixou de estourar por markdown**, mas continua variável por outro
  motivo: quando o LLM emite uma primeira frase **sem vírgula** de 4 s de áudio,
  o corte por cláusula não tem onde cortar (execução 4: 2924 ms, `tts1_s` 4,10).
  Lever restante no §6.

## 2. Método

- **Antes:** 3 execuções do harness na imagem anterior (sem sanitizer, sem
  threads, chunker por sentença), mesmo container, mesma config; providers reais.
- **Depois:** 6 execuções na imagem nova (`docker compose build api` com extras +
  `docker compose up -d api`), providers reais nos 4 idiomas em todas.
- Estágios iguais aos do AST-039, todos a partir de `audio.end`:
  `stt_ms` (Whisper), `ttok_ms` (cumulativo até o 1º token do LLM),
  `tts1_ms = first_audio_ms − ttok_ms` (espera pelo primeiro chunk falável +
  síntese dele), `tts1_s` = duração do áudio desse chunk.
- p50 = mediana; p95 = máximo observado (nearest-rank com n pequeno), como nos
  AST-037/039. `en` da execução 1 do "antes" tem `ttok_ms = 6252` (LLM frio no
  primeiro turno do processo) — está registrado, não escondido.

## 3. Antes (n = 3 por idioma, imagem anterior)

```
lang stt             tts      stt_ms  ttok_ms  tts1_ms  tts1_s  audio_ms total_ms
---------------------------------------------------------------------------------
en   faster_whisper  kokoro     1082     6252     2246    2.45      8498    14082
ja   faster_whisper  kokoro     1022     1564     1599    3.17      3163     7519
zh   faster_whisper  kokoro     1125     1558     2097    3.52      3655     6706
de   faster_whisper  piper      1240     1741      365    2.31      2106     6764

en   faster_whisper  kokoro     1776     2243      936    1.77      3179     7877
ja   faster_whisper  kokoro      999     1466     2104    4.12      3570    14141
zh   faster_whisper  kokoro      942     1363     1929    4.38      3292     4401
de   faster_whisper  piper      1302     1754      403    2.71      2157     7661

en   faster_whisper  kokoro     1005     1458     1261    2.48      2719     9176
ja   faster_whisper  kokoro      916     1361     1290    3.17      2651    13694
zh   faster_whisper  kokoro      972     1368     1131    2.67      2499     6255
de   faster_whisper  piper      1087     1527      182    1.52      1709     7084
```

Primeiros chunks (sentença inteira, como o AST-039 descreveu):

```
[ja] 'コーヒーを温めてください。' (3.17s) / 'おっしゃいましたが、コーヒーを温めてください。' (4.12s)
[zh] '好的，来一杯咖啡不辣吗？' (3.52s) / '没问题，您想喝什么口味的咖啡？' (4.38s)
```

| Idioma | `stt_ms` p50 | `ttok_ms` p50 | `tts1_ms` p50 | `tts1_s` p50 | `first_audio_ms` p50 | máx (p95 ≈) |
|---|---|---|---|---|---|---|
| en | 1082 | 2243 | 1261 | 2,45 s | 3179 | **8498** |
| ja | 999 | 1466 | 1599 | 3,17 s | **3163** | **3570** |
| zh | 972 | 1368 | 1929 | 3,52 s | **3292** | **3655** |
| de | 1240 | 1741 | 365 | 2,31 s | 2106 | 2157 |

(`en` com `ttok_ms` 2243 e máximo 8498: a primeira execução do processo paga o
`warmup`/cold start do LLM e não é comparável turno a turno.)

## 4. Depois (n = 6 por idioma, imagem nova)

```
=== RUN 1 ===
en   faster_whisper  kokoro      959     1408     1352    3.48      2760     8738
ja   faster_whisper  kokoro      823     1257      521    1.40      1778    13282
zh   faster_whisper  kokoro      821     1379      520    1.43      1899     6315
de   faster_whisper  piper      1194     1658      192    1.50      1850     4460

=== RUN 2 ===
en   faster_whisper  kokoro     1382     1832      679    1.48      2511     9919
ja   faster_whisper  kokoro      920     1376      821    1.98      2197    11545
zh   faster_whisper  kokoro      883     1280      656    1.43      1936     4181
de   faster_whisper  piper      1246     1683      217    1.64      1900     7875

=== RUN 3 ===
en   faster_whisper  kokoro      967     1421      857    1.77      2278     6331
ja   faster_whisper  kokoro      886     1323      705    1.98      2028    12339
zh   faster_whisper  kokoro      893     1285      518    1.43      1803     5868
de   faster_whisper  piper      1129     1552      199    1.70      1751    10243

=== RUN 4 ===
en   faster_whisper  kokoro      873     1304     1620    4.10      2924     8632
ja   faster_whisper  kokoro      817     1232      730    1.95      1962    13023
zh   faster_whisper  kokoro     1044     1440      816    2.23      2256     6028
de   faster_whisper  piper      1057     1466      179    1.85      1645     9324

=== RUN 5 ===
en   faster_whisper  kokoro      901     1314      739    1.77      2053     6404
ja   faster_whisper  kokoro      851     1255      796    2.05      2051    13467
zh   faster_whisper  kokoro      897     1295      629    1.43      1924     3791
de   faster_whisper  piper      1012     1425      337    2.95      1762     9069

=== RUN 6 ===
en   faster_whisper  kokoro      911     1324      773    2.10      2097     8339
ja   faster_whisper  kokoro      875     1278      704    1.98      1982    10768
zh   faster_whisper  kokoro      897     1301      773    2.23      2074     6593
de   faster_whisper  piper      1177     1596      148    1.18      1744     4180
```

Primeiros chunks depois (cláusula ou janela de caracteres, nunca markdown):

```
[ja] 'はい、' (1.40s) / 'このコーヒーを' (1.98–1.98s) / 'このコーヒー、' (1.95s) / 'そのコーヒーは' (2.05s)
[zh] '好的，' (1.43s) / '您好，' (2.23s) / '您好！' (2.23s)
[en] "I'd like an orange latte," (2.10s) / 'That sounds great!' (1.77s)
```

| Idioma | `stt_ms` p50 | `ttok_ms` p50 | `tts1_ms` p50 | `tts1_s` p50 | `first_audio_ms` p50 | máx (p95 ≈) | antes p50 |
|---|---|---|---|---|---|---|---|
| en | 935 | 1366 | 815 | 1,94 s | 2394 | 2924 | 3179 |
| ja | 863 | 1268 | 718 | 1,98 s | **2005** | **2197** | 3163 |
| zh | 895 | 1298 | 642 | 1,43 s | **1930** | **2256** | 3292 |
| de | 1153 | 1574 | 196 | 1,67 s | 1756 | 1900 | 2106 |

`tts1_ms` caiu 55 % em ja (1599 → 718) e 67 % em zh (1929 → 642): é o chunk
menor, não uma síntese mais rápida (as threads entram como bônus no mesmo
`tts1_ms`).

Execução de confirmação, já com a imagem final e o serviço saudável
(`docker compose ps` → `Up 11 minutes (healthy)`):

```
en   faster_whisper  kokoro      854     1271      640    1.77      1911     8912
ja   faster_whisper  kokoro      843     1244      568    1.40      1812    13416
zh   faster_whisper  kokoro      906     1303      753    2.50      2056     5290
de   faster_whisper  piper      1193     1608      161    1.58      1769     7054

[ja] first reply chunk (1.40s of audio): 'はい、'
[zh] first reply chunk (2.50s of audio): '请稍等，'
OK: real STT and real TTS (kokoro en/ja/zh, piper de) for every language
```

Segunda confirmação, depois do rebuild final (só o docstring do `kokoro.py`
mudou desde o bloco de 6 execuções) e de um `docker compose up -d` novo — o
`en` desta vez pagou o LLM frio (`ttok_ms` 4336, não é o código):

```
en   faster_whisper  kokoro     1081     4336      638    1.48      4974     9172
ja   faster_whisper  kokoro      869     1302      610    1.40      1912    11420
zh   faster_whisper  kokoro      886     1272      971    2.85      2243     5274
de   faster_whisper  piper      1128     1563      241    2.00      1804     6503
OK: real STT and real TTS (kokoro en/ja/zh, piper de) for every language
```

## 5. Sanitização: custo medido do markdown no Kokoro

Mesmo processo, `KPipeline` já criado, `cpu_threads = 6`, uma chamada por linha:

```
raw      1045 ms audio=4.78s "You'd like an **orange** coffee, please."
clean     409 ms audio=2.45s "You'd like an orange coffee, please."

raw       368 ms audio=2.20s 'Ask the [menu](https://example.com/menu) first, please.'
clean     350 ms audio=2.20s 'Ask the menu first, please.'

raw       854 ms audio=4.88s '## Order\n- one `latte`\n- one mocha'
clean     463 ms audio=2.30s 'Order one latte one mocha'
```

Os asteriscos não são "lixo inofensivo": o Kokoro **fala** cada um (dobra a
duração do áudio) e paga a síntese proporcional a isso. Link e heading idem.

## 6. Threads do Kokoro: 10 → 6 (bench intercalado no container)

8 chamadas por configuração, `apply_torch_threads()` entre os blocos, mesma
sentença, segunda passada para descartar aquecimento:

```
threads=10 ja    1022 ms  audio=4.12s  'おっしゃいましたが、コーヒーを温めてください。'
threads=10 zh     841 ms  audio=3.52s  '好的，您想要什么菜呢？'
threads= 6 ja     644 ms  audio=4.12s
threads= 6 zh     575 ms  audio=3.52s
threads=10 ja     976 ms  audio=4.12s
threads=10 zh     820 ms  audio=3.52s
threads= 6 ja     653 ms  audio=4.12s
threads= 6 zh     557 ms  audio=3.52s
```

−36 % em ja e −32 % em zh por sentença (o AST-039 media −15 %/−24 % com o host
menos carregado; o sinal é o mesmo, a magnitude depende da carga). `torch`
abria **10** threads nesta máquina; agora abre 6, configurável por
`tts.cpu_threads` no `models.toml`.

## 7. O que mudou

- **`backend/app/core/text.py` (novo, 43 linhas)** — `sanitize_for_tts()`: remove
  links (`[label](url)` → `label`), ênfase (`**x**`, `*x*`, `__x__`), código
  (`` `x` ``), heading (`#`, `##`), marcadores de lista (`-`, `*`, `+`, `1.`) e
  qualquer `*`/`` ` `` sobrando; colapsa espaços. Preserva pontuação e prosódia
  (`、，,；;` continuam lá, senão o chunker perde as fronteiras) e CJK intacto.
  Texto sem markdown sai igual.
- **`backend/app/orchestrator/turn.py`** — `_emit_tts` sanitiza **antes** de
  `tts.synthesize` e emite o mesmo texto limpo em `tts.audio.text`. O transcript
  (`assistant.message`, `llm.delta`, `history`, review, banco) continua com o
  texto original do modelo. Um chunk que é só markdown (`**`) não chama o
  provider nem emite evento.
- **`backend/app/orchestrator/chunker.py`** — primeiro chunk do turno:
  1. fronteira de cláusula (`、`, `，`, `,`, `；`, `;`) quando a cláusula tem
     ≥ `min_clause_chars` = **10** caracteres (inclui o marcador); "はい、" e
     "嗯、" não viram chamada própria. O corte **para no terminador de frase**:
     `Of course! Would you like ...` nunca começa com a cláusula da 2ª frase;
  2. `1,000` não é fronteira (dígito dos dois lados);
  3. em CJK, se a primeira frase passa de `first_chunk_chars` = **8**, corta por
     caractere — preferindo uma cláusula dentro da janela (≥ 3 caracteres) e
     recuando para não partir palavra: run de kana (`ござい|ます`), kanji +
     okurigana (`温|めて`) e katakana (`コーヒー`) não são cortados; se não há
     lookahead suficiente (buffer termina no teto), espera o próximo delta;
  4. acima de `max_chars` sem espaço para cortar (ja/zh), corta por caractere em
     vez de nunca cortar.
  Depois do primeiro chunk, o comportamento por sentença é o de antes.
- **`backend/app/providers/tts/kokoro.py`** — `DEFAULT_CPU_THREADS = 6`,
  `cpu_threads_from_config()` (int > 0, erro acionável via `ProviderUnavailable`)
  e `apply_torch_threads()` (`torch.set_num_threads`) chamados no
  `KokoroTTS.__init__`. Mapa de voz/idioma intacto.
- **`backend/config/models.toml`** — `cpu_threads = 6` no `[profiles.compact]`,
  nada mais.
- **`scripts/audio_smoke.py`** — só texto: a coluna `tts1_ms` agora é descrita
  como o primeiro chunk falável (não a primeira sentença) e o rótulo virou
  `first reply chunk`. Nenhum check mudou.

## 8. Testes (escritos antes)

`backend/tests/test_chunker.py` (+16 casos, 24 no arquivo) e
`backend/tests/test_tts_text.py` (novo, 25 casos): sanitizador (markdown, link,
backticks, heading, bullets, texto normal e CJK inalterados, vazio), `_emit_tts`
com TTS gravador (texto sanitizado chega ao provider e ao `tts.audio`; chunk
puramente markdown é ignorado; transcript mantém o markdown), chunker (ja/zh em
`、`/`，`, mínimo configurável, cláusula curta não corta, cap por caractere, sem
perder texto, frase latina inalterada, não atravessa terminador, `1,000`,
kana/okurigana/lookahead) e Kokoro (`cpu_threads` default/parse/erro,
`models.toml` do perfil compact, `apply_torch_threads` com
`importorskip("torch")` — 1 skip no venv do host).

## 9. Validação (saída real)

```
$ cd backend && uv run pytest tests/test_chunker.py tests/test_tts_text.py -q
# ANTES da implementação:
29 failed, 12 passed, 1 skipped, 2 warnings in 0.23s

$ cd backend && uv run pytest tests/test_chunker.py tests/test_tts_text.py -q
# DEPOIS:
48 passed, 1 skipped, 1 warning in 0.15s

$ uv run pytest -q
464 passed, 1 skipped, 1 warning in 2.64s

$ uv run ruff check . ../scripts
All checks passed!

$ AST_INSTALL_EXTRAS="--extra stt --extra tts" docker compose build api
 Image ai-speaking-tutor-api Built

$ docker compose up -d api      # AST_PROFILE=compact
 Container ai-speaking-tutor-api-1 Started
$ curl -s http://127.0.0.1:8000/api/health
{"status":"ok","languages":["en","ja","zh","de"],...}

$ docker compose exec -T api python /app/scripts/audio_smoke.py   # 6x, §4
OK: real STT and real TTS (kokoro en/ja/zh, piper de) for every language

$ make check
cd backend && uv run ruff check . ../scripts → All checks passed!
cd backend && uv run pytest -q                → 464 passed, 1 skipped
cd frontend && npx vitest run                 → Test Files 1 passed / Tests 7 passed
cd frontend && npx playwright test            → 7 passed (4.3s)
cd frontend && npm run build                  → ✓ built in 564ms
make check: lint, test, e2e e build verdes
```

Sonda de markdown em turnos reais (WS, texto, providers reais):

```
[en] tts.audio chunks=3 markdown_found=False
   spoken: 'That sounds great!' / 'How would you like to pay?' / 'Do you have cash or a card?'
[ja] tts.audio chunks=4 markdown_found=False
   spoken: 'こんにちは！' / 'コーヒーは温めてほしいと、言いましたか？' / ... / '袋が必要ですか？'
[zh] tts.audio chunks=4 markdown_found=False
   spoken: '好的，' / '您要点哪一道菜呢？' / '顺便问一句，这杯咖啡要喝吗？' / '还是先点菜？'
```

## 10. Riscos e pendências

- **`en` com frase longa sem vírgula**: a execução 4 chegou a 2924 ms porque o
  LLM emitiu `An orange latte with oat milk and on the go sounds great.` — sem
  vírgula não há cláusula para cortar. Lever: teto de **palavras** para o
  primeiro chunk latino (ex.: 8 palavras → `An orange latte with oat milk and on
  the go` ≈ 2 s). Não implementado: muda a prosódia do inglês sem escuta, e o
  critério de aceite do `en` era só o markdown.
- **`_word_cut` depende de kana/kanji**: em japonês, texto sem kana ou chinês
  puro continuam cortáveis por caractere (é o caso do zh, que fecha a meta
  assim). Um corte de 8 caracteres em zh pode cair no meio de uma palavra
  composta (`您想喝什|么口味`); alternativas seriam um segmentador (dep nova) ou
  subir `first_chunk_chars`.
- **`torch.set_num_threads` é global no processo** e o `turn.py` permite 2
  sínteses em paralelo; com 6 threads as duas dividem o mesmo pool. Se aparecer
  sessão concorrente, revisar (limitar o semáforo a 1 ou subir `cpu_threads`).
- **`first_chunk_chars = 8` e `min_clause_chars = 10`** são heurísticas
  linguísticas: o teste cobre os casos observados no smoke, não uma amostra
  grande. Vale uma escuta de 10 turnos por idioma antes de fechar o tom.
- **`make check` passou 3x** nesta entrega (464 testes backend, 7 e2e, 7 vitest,
  build limpo). A suíte backend cresceu de 425 para 464 (+39 casos; 1 skip).
- **Duas notas de ambiente (não são do repo):** (a) o `docker compose build`
  falhou duas vezes com `No space left on device` (camada `chown`, e depois
  `/tmp` do container no smoke) porque o host estava com 100 % de disco: foi
  preciso `docker builder prune -f` (28,5 GB + 5,7 GB de cache de build órfão)
  — nenhuma imagem em uso, volume ou container foi removido; (b) `docker
  compose up` recria o `api` com `AST_PROFILE=skeleton` (default do
  `compose.yaml`), o que fez o smoke acusar `stt/tts = fake`; as medições usam
  `AST_PROFILE=compact docker compose up -d api`, como no AST-039.

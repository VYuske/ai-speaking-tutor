# ADR 0005 — Desligar o thinking do LLM de produto

**Data:** 2026-11-18
**Status:** aceito

## Contexto

`qwen3.5:4b` é um modelo com modo de raciocínio. Medido nesta máquina, com
`max_tokens = 220` e sem controle de raciocínio:

- `choices[0].message.content == ""`
- `message.reasoning` com ~880 caracteres
- `finish_reason == "length"`

Ou seja: o tutor devolvia uma resposta vazia e o usuário não ouvia nada. O benchmark
(AST-001) já contornava o problema enviando `"reasoning_effort": "none"`.

## Decisão

O provider de produto envia, por padrão:

- `/v1/chat/completions` (stream e JSON): `"reasoning_effort": "none"`;
- `/api/chat` (modo `ollama_native`): `"think": false`.

Configurável em `[llm.default] reasoning_effort`; valor vazio omite o campo.
Se o servidor rejeitar o campo com HTTP 400, há **um** retry sem ele, com warning.
`[DONE]` encerra o stream imediatamente.

## Consequências

- Turnos reais passam a produzir conteúdo (validado: 1º token em 376 ms).
- Perde-se raciocínio explícito, que não é necessário para roleplay e correção curta.
- Benchmarks e produto passam a medir o mesmo caminho.
- Servidores que não conhecem o campo funcionam via fallback.
- Se um dia quisermos um "modo explicação profunda", será um perfil separado com
  thinking ligado e orçamento de tokens maior.

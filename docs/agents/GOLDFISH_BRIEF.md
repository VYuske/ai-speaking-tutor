# Brief de goldfish (template)

> O elefante preenche este brief. O goldfish não negocia escopo: se algo faltar,
> ele para e devolve a pergunta no relatório.

## Cabeçalho

```
TASK: AST-___
TITLE: <verbo + objeto>
OWNER: goldfish
DEPENDS ON: <ids ou —>
FILES YOU MAY TOUCH:
  - path/a.py
  - tests/test_a.py
DO NOT TOUCH: <arquivos de contrato e vizinhos>
ESTIMATED BLAST RADIUS: <uma frase>
```

## 1. Objetivo

Uma frase verificável. Ex.: “Adicionar `bench_llm.py` que mede TTFB e tokens/s
contra um endpoint OpenAI-compatible e grava um markdown em `docs/spikes/`.”

## 2. Contexto mínimo

Somente o necessário para executar: contrato da interface, trecho relevante,
comando de execução. **Não** incluir roadmap, histórico de decisões ou arquivos
que o agente não vai editar.

## 3. Requisitos

- Lista numerada, cada item testável.
- Comportamento em erro explícito.
- Restrições de dependência: não adicionar pacote sem autorização.
- Convenções: Python 3.12, `ruff` line-length 100, TS strict, sem comentários óbvios.

## 3.1 Test-first (obrigatório)

Toda mudança de comportamento começa por um teste que falha.

1. Escreva o teste (ou o comando de verificação) que expressa o critério de aceite.
2. Rode e **cole a saída de falha** no relatório. Sem essa evidência, a entrega é rejeitada.
3. Só então implemente.
4. Rode de novo e mostre o verde.
5. Exceção: tarefa puramente documental/config, sem comportamento executável. Nesse
   caso, defina o comando de validação **antes** da mudança e mostre-o falhando;
   explique no relatório por que um teste unitário não se aplica.

Um teste que passaria mesmo sem a implementação não conta.

## 4. Fora de escopo

Lista explícita do que não fazer, incluindo “não refatore arquivos vizinhos” e
“não altere o protocolo WS”.

## 5. Critérios de aceite

- [ ] Critério observável 1
- [ ] Critério observável 2
- [ ] Teste escrito **antes** do código, com a falha inicial registrada no relatório
- [ ] Teste novo cobrindo o caminho feliz e um caminho de erro
- [ ] `uv run ruff check . ../scripts` limpo
- [ ] `uv run pytest -q` verde (ou `tsc --noEmit` + `vitest run` + `playwright test` no frontend)
- [ ] Nenhum arquivo fora da lista foi modificado

## 6. Comandos de validação

```bash
# exatos, executáveis na raiz indicada
```

## 7. Formato do relatório de retorno

```
STATUS: done | blocked | partial
FILES CHANGED: <lista>
WHAT CHANGED: <3–6 bullets>
TEST-FIRST: <comando do teste + saída de falha antes da implementação; ou o comando de validação que falhava, em docs/config>
COMMANDS RUN: <comando → resultado>
TESTS ADDED: <arquivo → casos>
RISKS / FOLLOW-UPS: <bullets>
OPEN QUESTIONS: <bullets ou None>
```

## Regras de conduta do goldfish

1. Escreva o teste antes do código e prove que ele falhava. Entrega sem essa
   evidência é rejeitada.
2. Só edite arquivos da lista. Se precisar de outro, pare e peça.
3. Não adicione dependência sem autorização explícita no brief.
4. **Nunca apague arquivos ou diretórios** (`rm -rf`, `rm`) fora de um alvo explicitamente
   autorizado. Para regenerar snapshots, escreva por cima; se precisar remover, pare e pergunte.
5. Não “melhore” código adjacente, não renomeie símbolos públicos, não reformate.
6. Não invente requisitos; lacuna vira `OPEN QUESTIONS`.
7. Rode os comandos de validação; relate a saída real, inclusive falhas.
8. Se um teste falhar por causa do ambiente (modelo não baixado), marque o teste
   com `@pytest.mark.models` e explique no relatório.
9. Entregue o menor diff que satisfaça os critérios.

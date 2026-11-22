# ADR 0006 — Test-first obrigatório

**Data:** 2026-11-18
**Status:** aceito

## Contexto

Nas waves 1–4, os testes foram escritos junto com (ou depois de) a implementação.
Isso permite um teste que apenas espelha o código, sem provar que ele faltava. Em
um projeto com modelos locais e prompts, regressões silenciosas são caras.

## Decisão

Toda mudança de comportamento começa por um teste que falha:

1. O goldfish escreve o teste (ou o comando de verificação) que expressa o aceite.
2. Roda e cola a saída de falha no relatório.
3. Só então implementa.
4. Roda de novo e mostra o verde.

Exceção: tarefas puramente documentais/config, sem comportamento executável. Nesse
caso, o comando de validação é definido antes da mudança e precisa falhar antes.

O elefante rejeita entrega sem essa evidência. O teste precisa falhar de verdade
sem a mudança — verificado executando, não presumido.

## Consequências

- O brief sempre inclui o comando de teste e o critério observável.
- O relatório ganha o campo `TEST-FIRST`.
- Waves ficam um pouco mais lentas no início, mas o aceite fica objetivo.
- Testes de snapshot/regressão passam a ser escritos antes de mudanças de prompt.

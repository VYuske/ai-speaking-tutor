# ADR 0004 — Modelo de trabalho Elephant / Goldfish

**Data:** 2026-11-19
**Status:** aceito

## Contexto

Tarefas longas em um único contexto degradam foco e memória, e revisões ficam
caras. O projeto precisa de paralelismo sem conflito de arquivos e sem perda de
coerência arquitetural.

## Decisão

- **Elefante (orquestrador):** mantém visão, invariantes, contratos, ADRs, ledger e
  estado verificado. Decompõe, briefa, revisa, integra e registra. Não implementa features.
- **Goldfish (executor):** recebe um escopo fechado (objetivo, arquivos permitidos,
  fora de escopo, critérios, comandos) e devolve diff + relatório. Não decide
  arquitetura e não amplia escopo.

Regras:

1. Máximo 3 goldfish simultâneos.
2. Um dono por arquivo por wave.
3. Arquivos de contrato são integrados apenas pelo elefante.
4. Toda entrega passa pelo `REVIEW_CHECKLIST`; aceite exige comandos reproduzíveis.
5. A cada wave, `docs/PROJECT.md` e `docs/TASKS.md` refletem o estado real.

## Consequências

- Contexto pequeno por agente, menos alucinação de escopo.
- Custo de coordenação sobe; o brief precisa ser bom.
- Conflitos caem para quase zero com escopo de arquivo explícito.
- A memória do projeto fica em documentos versionados, não no histórico de chat.

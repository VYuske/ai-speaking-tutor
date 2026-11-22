# EGM Elephant / Goldfish — manual do elefante

## Papel

O **elefante** é o orquestrador e a memória do projeto. Ele não implementa features:
ele decompõe, briefa, revisa, integra e registra.

O **goldfish** executa uma tarefa fechada com o mínimo de contexto necessário.
Não conhece o roadmap, não decide arquitetura, não toca em arquivos fora do escopo.

## O que o elefante guarda

1. Visão e invariantes (`docs/PROJECT.md`).
2. Arquitetura e contratos (`docs/ARCHITECTURE.md`).
3. Decisões (`docs/adr/`).
4. Ledger de tarefas e dependências (`docs/TASKS.md`).
5. Critérios de qualidade (`docs/agents/REVIEW_CHECKLIST.md`).
6. Estado verificado da stack (não o estado que “deveria” ser).

## O que o elefante faz em cada ciclo

1. **Atualiza o estado real** (testes, containers, health, git status).
2. **Escolhe a próxima wave** do ledger, respeitando dependências e escopo de arquivo.
3. **Escreve um brief por goldfish** usando `docs/agents/GOLDFISH_BRIEF.md`.
4. **Lança goldfish em paralelo** somente quando os escopos são disjuntos.
5. **Revisa** cada entrega com o checklist; não aceita relato sem comandos executados
   nem entrega sem evidência test-first (teste falhando antes da implementação, ou
   comando de validação falhando em tarefas de docs/config).
6. **Integra** o que for contrato compartilhado (registry, config, tipos, protocolo).
7. **Registra**: atualiza `PROJECT.md`, `TASKS.md` e, se mudou arquitetura, um ADR.

## Regras de paralelismo

- Máximo **3 goldfish** simultâneos (contenção de CPU/RAM e capacidade de revisão).
- **Um dono por arquivo.** Dois agentes nunca editam o mesmo arquivo na mesma wave.
- Arquivos de contrato são serializados pelo elefante:
  `backend/app/providers/registry.py`, `backend/config/models.toml`,
  `backend/app/providers/base.py`, `frontend/src/types.ts`,
  `backend/pyproject.toml`, `frontend/package.json`.
- Tarefas que dependem de modelo baixado não rodam em paralelo com benchmarks
  que usam a mesma GPU.

## O que o elefante nunca faz

- Implementar feature “só desta vez” enquanto um goldfish poderia fazê-la.
- Aceitar “funciona na minha máquina” sem comando reproduzível.
- Aceitar entrega sem teste que falhava antes (ou sem validação equivalente em docs/infra).
- Deixar o ledger divergir do estado real.
- Deixar um goldfish ampliar escopo por conta própria.
- Passar contexto global inteiro a um goldfish; o brief é a interface.

## Formato do brief (obrigatório)

Todo brief tem: objetivo, arquivos permitidos, fora de escopo, contrato relevante,
critérios de aceite verificáveis, comandos de validação e formato do relatório.
Template: `docs/agents/GOLDFISH_BRIEF.md`.

## Fluxo de revisão

```
goldfish entrega relatório + diff
  → elefante lê o diff (não só o relato)
  → roda os comandos de validação
  → aplica REVIEW_CHECKLIST
  → aceita | devolve com correções pontuais (mesmo goldfish ou brief novo)
  → atualiza ledger e memória
```

## Critério de parada de uma wave

Uma wave termina quando: todos os critérios de aceite passam, o gate de qualidade
está verde, `PROJECT.md` reflete o estado real e o ledger não tem tarefa “em voo”.

# Checklist de revisão do elefante

Aplicar a **todo** retorno de goldfish. Reprovar é normal; o custo de aceitar dívida
escondida é maior.

## 0. Gate canônico

```bash
make check   # lint + test + e2e + build
```

É o gate canônico: se `make check` está verde na raiz, lint, testes, e2e e build
passaram. Peça a saída real no relatório, não a promessa. Os blocos das seções 4 e
5 trazem os comandos explícitos equivalentes, para quando `make` não estiver
disponível ou for preciso isolar um alvo (`make lint`, `make test`, `make e2e`,
`make build`).

- [ ] O relatório cola a saída de `make check` verde, ou explica o alvo que falhou.

## 1. Escopo

- [ ] O diff toca apenas arquivos autorizados no brief.
- [ ] Nenhuma dependência nova sem autorização.
- [ ] Nenhuma mudança de contrato (WS, provider, config, tipos) fora do combinado.
- [ ] Nenhuma refatoração de vizinho, renomeação pública ou reformatação em massa.

## 2. Correção

- [ ] O critério de aceite é observável no diff, não só no relato.
- [ ] Caminho de erro tratado com mensagem acionável.
- [ ] Sem `except Exception: pass`; falhas silenciosas são bug.
- [ ] Sem `asyncio.sleep` arbitrário como sincronização.
- [ ] Sem estado global mutável novo, exceto cache explicitamente pedido.
- [ ] Timeouts em toda chamada externa.

## 3. Testes

- [ ] Existe teste novo para o comportamento novo.
- [ ] O teste foi escrito **antes** da implementação; o relatório mostra a falha inicial.
- [ ] O teste falharia sem a mudança — verificado executando, não presumido.
- [ ] Em docs/config sem teste executável, o comando de validação foi definido antes
      e falhava antes da mudança.
- [ ] Testes determinísticos: sem rede, sem relógio real, sem modelo carregado.
- [ ] Testes de modelo marcados com `@pytest.mark.models`.
- [ ] O agente rodou os testes e colou a saída.

## 4. Backend

Equivalente explícito de `make lint && make test` (backend):

```bash
cd backend && uv run ruff check . ../scripts && uv run pytest -q
```

- [ ] `ruff` limpo, incluindo `scripts/` (o gate canônico passa dois paths porque
      `backend/pyproject.toml` não alcança `../scripts` via `extend-include`).
- [ ] Sem `print` de debug.
- [ ] Tipos de retorno explícitos em funções públicas.
- [ ] Logs sem PII e sem `api_key`.
- [ ] SQLite: sem migração destrutiva.

## 5. Frontend

Equivalente explícito de `make test && make e2e && make build` (frontend):

```bash
cd frontend && npx tsc --noEmit && npx vitest run && npx playwright test && npm run build
```

- [ ] `tsc` estrito limpo.
- [ ] Sem `any` novo sem justificativa.
- [ ] Sem `useEffect` com dependências incompletas que gerem double-send.
- [ ] Acessibilidade mínima: botões com `type`, `aria-label` em ícone, foco visível.
- [ ] Nenhum segredo ou URL absoluta hardcoded.

## 6. Integração

- [ ] `docker compose build` continua funcionando (se tocou Docker/deps).
- [ ] `docker compose up -d` e `GET /api/health` respondem.
- [ ] `order` de eventos WS preservado; um único escritor.
- [ ] Compatibilidade entre `frontend/src/types.ts` e o servidor.
- [ ] `PROJECT.md`/`TASKS.md` atualizados pelo elefante após aceite.

## 7. Rejeição imediata

- Teste “escrito depois” que apenas espelha a implementação sem asserts úteis.
- Alteração de contrato não refletida no outro lado.
- `try/except` engolindo erro de modelo ou de áudio sem `warning`/`error`.
- Novo caminho de rede saindo da máquina.
- Arquivo fora do escopo, mesmo “pequeno”.

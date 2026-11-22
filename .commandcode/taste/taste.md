# Taste

## Tooling & Workflow
- Prefers applications to run isolated via Docker (containers) rather than depending on the host environment's installed runtimes/libraries. Confidence: 0.8
- Insists on test-first (TDD): the test must be written and shown failing before the implementation, and this rule should be baked into agent briefs, review checklists and definition-of-done docs. Confidence: 0.9
- Wants the main agent to act as an orchestrator ("elephant") holding whole-project context, delegating all implementation, review and test-writing to parallel scoped subagents ("goldfish") and reviewing their output rather than writing the code itself; will correct the agent when it starts implementing directly. Confidence: 0.95

## Documentation
- Prefers project documentation kept as markdown files in the repo, and gives the agent latitude to create new `.md` files to organize project docs. Confidence: 0.75
- Wants the README to showcase the tool stack visually — badges (shields.io) plus a stack/technologies table — not just prose. Confidence: 0.6

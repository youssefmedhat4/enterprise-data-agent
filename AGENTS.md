# Working in this repository

Enterprise Data Agent is an implemented, working system, not a project in
bootstrap. See [`README.md`](README.md) for what it does and how the pieces
fit together, and [`docs/decisions/`](docs/decisions/) for the reasoning
behind specific architectural choices (Wren as the governed metric provider,
the OPA authorization boundary, the MCP Toolbox adapter, the learning loop,
time intelligence, and others). Consult those before assuming a component is
still under evaluation — the ADRs record what was decided and why.

## Principles

1. Keep external integrations (LLM providers, database transport, semantic
   layer, authorization) behind typed gateways, not called directly from
   business logic. This is what lets a provider change without touching
   LangGraph or the API contract.
2. Prefer mature open-source infrastructure over custom implementations for
   solved problems (auth, model routing, semantic layers, catalogs), but don't
   add a dependency without a concrete reason tied to a real requirement.
3. Do not build large abstraction layers speculatively. A bug fix doesn't need
   surrounding cleanup; a one-shot operation doesn't need a helper.
4. Document architecturally important decisions as an ADR under
   `docs/decisions/` rather than only in code comments — comments should
   explain *why* something non-obvious is the way it is, not narrate what the
   code already says.
5. Run the relevant test suites, Ruff, and mypy after a meaningful change (see
   [`README.md#testing`](README.md#testing) for the exact commands). Report a
   failing test honestly rather than working around it.
6. Never weaken a security or authorization check to make a test pass.
7. Never commit secrets, and never use real confidential company data in
   development or tests — the Legacy ERP fixture and evaluation datasets are
   synthetic by design.
8. Keep code understandable to an engineer who did not write it.

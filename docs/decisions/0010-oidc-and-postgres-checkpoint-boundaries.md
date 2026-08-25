# ADR 0010: OIDC Identity and PostgreSQL Conversation Checkpoints

## Status

Accepted.

## Decision

Production identity is established by an `AuthenticationGateway` OIDC adapter. It discovers the
provider metadata and JWKS, accepts RS256 access tokens, verifies signature, issuer, audience, and
time validity, and maps configured claims into `UserIdentity`. Authorization remains exclusively
owned by OPA. The adapter is compatible with tenant-specific Microsoft Entra ID configuration and
does not store passwords, client secrets, certificates, or tokens.

LangGraph persistence is owned by `ConversationCheckpointStore`. Tests and explicit local
development may use memory. Integrated, staging, and production modes use LangGraph's PostgreSQL
checkpointer with a dedicated connection pool and setup lifecycle.

Checkpoint storage uses a database/schema/user boundary separate from analytical data. Its writer
credentials are injected as `CHECKPOINT_DATABASE_URL`, are never sent to the LLM, and must have no
access to the analytics database. The analytics role remains physically read-only. Staging and
production reject local authentication and in-memory checkpoint configuration.

## Consequences

- Threads resume typed analytical context after a backend restart.
- Checkpoint initialization and provider outages fail as sanitized typed errors.
- Enterprise OIDC tenant registration and claims configuration remain deployment responsibilities.
- The v1 checkpointer is intentionally a straightforward PostgreSQL implementation, not a custom
  distributed persistence system.

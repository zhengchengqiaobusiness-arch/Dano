# Context Map

## Contexts

- [Dano](./CONTEXT.md) — defines the enterprise assistant product language.
- [Deployment](./deploy/CONTEXT.md) — defines container deployment and runtime layout language.

## System-Wide Decisions

- [ADRs](./docs/adr/) — decisions that affect multiple contexts or repo-wide operation.

## Relationships

- **Dano → Deployment**: Dano runtime behavior is packaged and operated through the Deployment context.
- **Deployment → Dano**: Deployment preserves Dano runtime state and injects server-side configuration without exposing secrets to the browser.

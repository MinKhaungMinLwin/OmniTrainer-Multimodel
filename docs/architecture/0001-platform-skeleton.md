# ADR 0001: Platform skeleton

Status: accepted

## Context

Omni Model needs transactional business features, asynchronous automation, web/mobile clients, and a future low-latency voice plane. The existing repository is a standalone moderation proof of concept.

## Decision

- Use a monorepo with React web, Expo mobile, a modular FastAPI service, shared generated contracts, and a Redis worker.
- Use PostgreSQL as the transactional system of record, Redis for transient work, and S3-compatible object storage.
- Keep transactional domains in a modular monolith until scale or ownership requires separation.
- Keep the legacy moderation package operational during migration.
- Isolate the future realtime voice/media plane from the transactional API.

## Consequences

The customer vertical slice validates API contracts and tenant isolation early. Deployments remain simple, while domain modules retain boundaries that can be extracted later.

# ADR 0002: Operations domain commands

Status: accepted

## Context

Jobs, appointments, and invoices have lifecycle rules that cannot be protected
if clients directly mutate status fields. Consequential retries must also be
safe, tenant scoped, and observable.

## Decision

- Expose explicit `schedule`, `complete`, `draft invoice`, `issue`, and `void`
  commands instead of generic status updates.
- Require an `Idempotency-Key` on every operations write and retain a
  tenant-scoped command receipt.
- Require the caller's last-seen aggregate version and return `409 Conflict`
  when it is stale. Lock the aggregate while applying each transition.
- Store appointment instants in UTC while retaining the source display
  timezone.
- Write audit and outbox events in the same database transaction as each
  business change.
- Store money as integer minor units and snapshot invoice line amounts.

## Consequences

Clients must reload after conflicts and cannot skip lifecycle states. Domain
events can be published asynchronously without risking a committed business
change that has no corresponding event.

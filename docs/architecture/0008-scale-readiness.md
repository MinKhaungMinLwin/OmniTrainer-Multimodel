# ADR 0008: Scale readiness and controlled releases

## Status

Accepted for M6 on 2026-09-16.

## Decision

The live media plane applies a hard session admission limit, maximum frame
size, bounded drop-oldest media buffers, and stale-frame expiry. Every provider
stage has a deadline, circuit breaker, and bounded fallback attempt. The
gateway exports provider health, queue, load-shed, timeout, fallback, and stage
latency metrics. Call state and sequenced events remain the durable recovery
checkpoint; the call-data reconciler continues to repair downstream facts.

Every release must pass a versioned, segmented scorecard covering task success,
grounding, false actions, transfer safety, interruption handling, first-audio
latency, and cost per successful outcome. CI rejects absolute threshold failures
and regressions from the approved baseline. Deployment uses immutable images
with SBOM/provenance, an explicit canary, health verification, manual production
promotion, and automatic rollback on failure.

Terraform owns managed PostgreSQL, Redis, encrypted/versioned object storage,
durable queues with dead-letter handling, image repositories, secrets, logs,
and the compute cluster for staging and production. Kubernetes owns runtime
workloads, resource bounds, autoscaling, disruption budgets, network policy,
and migration jobs. Secrets are supplied by workload identity and are never
stored in manifests.

## Recovery objectives

- PostgreSQL and operational records: RPO 15 minutes, RTO 60 minutes.
- Call recordings/raw evidence: RPO 24 hours, RTO 4 hours.
- Redis-derived work: no independent RPO; rebuild from PostgreSQL outbox and
  reconciliation checkpoints within the 60-minute service RTO.

Restore exercises run at least quarterly and after material storage or schema
changes. A release is not promoted when a recovery drill or migration rollback
assessment is overdue.

## Consequences

Local development stays deterministic and provider-neutral. Production still
requires cloud account identity, DNS/certificates, carrier credentials, and
provider-specific adapters; those are deployment inputs rather than source
code defaults.

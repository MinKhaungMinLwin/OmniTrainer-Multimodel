# ADR 0005: Durable automation runtime and reversible rollout

## Status

Accepted — 2026-09-16

## Context

Omni Model must react to transactional business events, schedules, inbound
messages, and call events without hiding why work happened or coupling durable
execution to an HTTP request. Automations can create records and queue customer
messages, so tenant isolation, review, deduplication, and emergency controls are
part of the execution model rather than UI conventions.

## Decision

- Store an automation as a stable definition plus immutable version snapshots.
  Every run references the exact snapshot used for evaluation and replay.
- Use PostgreSQL for trigger, run, approval, event, retry, dead-letter, and
  compensation state. Redis is a delivery mechanism, not the source of truth.
- Bridge existing transactional outbox events into tenant-scoped trigger events.
  Unique trigger and run constraints make dispatcher recovery safe.
- Support domain-event, interval schedule, inbound-message, call, and manual
  sources through one trigger envelope.
- Evaluate structured conditions before steps. AI and extraction steps produce
  derived values; actions use an explicit allowlist.
- Require shadow mode for installed templates. Shadow runs record planned
  actions without side effects. Promotion to production is explicit.
- Pause production writes according to a versioned approval rule. A reviewer
  can approve the proposal, edit arguments without adding new operation types,
  or reject it.
- Apply per-definition hourly limits, bounded retries, exponential retry
  timestamps, durable dead letters, replay, bulk pause, and an irreversible
  kill switch.
- Record the matched trigger, condition explanations, step results, and changed
  resource identifiers in ordered run events. Audit and outbox records carry
  the automation run identifier.
- Compensation is intentionally conservative: queued messages are cancelled,
  draft invoices are voided, draft jobs are cancelled, and automation-created
  notes are removed. Effects that have progressed beyond those safe states are
  reported as non-reversible.

## Template library

The initial library includes lead intake, appointment reminders, missed-call
follow-up, job completion summaries, invoice drafts, overdue-invoice follow-up,
and review escalation. Templates remain editable after installation and each
save creates a new immutable version.

## Consequences

The API can execute a trigger immediately for responsive review flows, while the
worker independently recovers transactional outbox events, scheduled triggers,
and due retries. External delivery adapters still consume queued outbound
messages; vendor-specific email/SMS delivery is deliberately outside this ADR.

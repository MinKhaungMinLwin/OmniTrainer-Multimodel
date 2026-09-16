# ADR 0003: Operations MVP storage and payment records

- Status: Accepted
- Date: 2026-09-16

## Context

The Operations MVP needs job evidence, printable invoices, and payment status
without coupling the domain model to a single cloud vendor or external payment
processor.

## Decision

- Store attachment metadata in PostgreSQL and bytes behind an object-storage
  adapter. Tests use the local filesystem; the container platform uses MinIO
  through the S3 API.
- Keep invoice amounts as integer minor units. Payments are append-only records
  with a tenant-unique optional external reference, while the invoice keeps
  derived paid amount and payment status for operational reads.
- Require idempotency keys for payment commands and reject duplicate external
  references. A paid invoice cannot be voided.
- Generate invoice PDFs from the persisted invoice snapshot in the API.
- Emit audit and transactional-outbox records for attachment, invoice, and
  payment changes.

## Consequences

Object bytes can move to managed S3 without changing API contracts. Payment
records remain an operational ledger rather than a full accounting system;
processor settlement, refunds, taxes, and accounting synchronization remain
future integrations.

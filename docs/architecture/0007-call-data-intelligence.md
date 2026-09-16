# ADR 0007: Call-data pipeline and conversation intelligence

- Status: accepted
- Date: 2026-09-16

## Decision

Build a replayable call-data pipeline on the existing transactional outbox and
Redis worker. A completed, failed, abandoned, or transferred call publishes one
`voice.call.ended` event. The dispatcher creates an idempotent `call.process`
job. An authorized operator can invoke the same processor for backfill or
recovery.

The first governed mart lives in PostgreSQL so development and pilot tenants
have one reproducible source of truth. Its raw, cleaned, and modeled contracts
are deliberately warehouse-shaped and can be exported without changing API or
metric definitions.

## Data layers

1. Raw provider callbacks are verified before persistence, content-addressed in
   object storage, and indexed by provider event ID, hash, retention date, and
   legal-hold state. Production object storage must enable encryption at rest.
2. Final speaker turns become a redacted transcript revision. Email addresses
   and phone numbers are excluded from search. Corrections append a revision
   linked to its source; raw segments are never overwritten.
3. Each transcript revision produces versioned intelligence: topic, intent,
   sentiment trajectory, objections, compliance flags, action items, summary,
   outcome, structured fields, confidence, and explicit provenance.
4. One call fact supplies governed operational measures for answer,
   containment, transfer, abandonment, consent, interruption, silence, tools,
   latency, duration, transcript completeness, and estimated cost.

Pipeline runs preserve attempts and ordered checkpoints for transcript
finalization, redaction, extraction, search indexing, fact modeling, and
reconciliation. Reprocessing the same revision is idempotent; a corrected
revision produces a new intelligence version.

## Quality and review

Low-confidence intelligence and all compliance flags enter `pending_review`.
Authorized reviewers may accept, correct, or reject the result, and every
decision is audited. Search and dashboards always enforce tenant scope. The UI
shows confidence, extractor and transcript versions, checkpoints, freshness,
and reconciliation state alongside aggregate metrics.

## Reconciliation

The processor first verifies event-sequence continuity and required end state.
Provider snapshots can then compare terminal status and duration with an
explicit tolerance. Discrepancies remain visible and do not silently rewrite
the internal record. Provider billing will replace the deterministic cost
estimate when a carrier supplies invoice detail.

## Consequences

This implementation provides an operational analytics mart, not a separate
cloud warehouse. External warehouse replication, column-level encryption keys,
advanced diarization, multilingual models, and semantic/vector indexing remain
adapter concerns for scale readiness. The durable versioned contracts and
replay path prevent those upgrades from changing raw evidence or public APIs.

# ADR 0004: Durable provider-neutral AI copilot runtime

- Status: Accepted
- Date: 2026-09-16

## Context

The copilot must stream useful work while keeping business writes reviewable,
tenant isolated, resumable, and independent of a particular model vendor.

## Decision

- Persist conversations, messages, runs, ordered events, tool invocations,
  approval decisions, and feedback in PostgreSQL. SSE is a replayable view over
  those events and resumes from an event sequence.
- Route model planning through a typed provider interface. The gateway applies
  PII tokenization, timeouts, retries, fallback selection, version metadata,
  rate limits, and token/cost accounting. CI and local development use a
  deterministic provider.
- Register tools with versioned JSON input schemas, role policy, risk level,
  deadline, stable idempotency key, and structured results. Read tools can run
  immediately. Write and external tools remain dry-run proposals until a human
  approves or edits them.
- Keep retrieved text outside system and tool policy. Knowledge chunks are
  tenant and role filtered, marked as untrusted, returned with citations, and
  deleted through database cascade.
- Preserve classification/extraction schema versions, confidence, source
  offsets, abstention state, original fields, and human corrections.
- Queue approved outbound messages through the transactional outbox; delivery
  belongs to the automation milestone.
- When explicitly configured, use Gemini schema-constrained planning while
  retaining the same tool allow-list, typed validation, and approval policy.
  Authenticated, size-bounded Assistant audio endpoints send browser recordings
  to Gemini STT and wrap Gemini TTS PCM as 24 kHz WAV for browser playback. The
  credential remains server-side.

## Consequences

Paused runs survive process restarts and every decision can be replayed. A
hosted model adapter can replace the local planner without changing tools,
policy, storage, streaming, or clients. Provider-specific prompt quality and
semantic vector retrieval remain independently deployable improvements behind
the same contracts.

# ADR 0006: Realtime voice pilot

- Status: accepted
- Date: 2026-09-16

## Decision

Ship one internal, inbound after-hours intake flow behind a tenant-level pilot
configuration. Run media handling in a separate FastAPI service while retaining
business policy, durable call state, review decisions, and audit history in the
platform database.

The flow is deliberately narrow: disclose AI use and recording, obtain consent,
collect a caller name and reason, perform an allowlisted customer lookup, confirm
the request, and create only a pending human review. The voice agent cannot
create a customer or job. Approval by an authorized operator creates a draft
callback job through the control-plane API. Emergency language, declined
consent, DTMF zero, repeated silence, unavailable tools, and operator requests
transfer to a human.

## Media and provider boundary

Telephony callbacks use timestamped HMAC signatures and idempotent provider call
IDs. Media URLs contain a signed, expiring token scoped to one call. The gateway
offers a WebRTC signaling/media-bridge contract and a bidirectional WebSocket
protocol for start, audio, transcript, speech-start, playback completion, DTMF,
silence, voicemail, and hangup frames.

STT, TTS, and realtime model capabilities are protocols with deterministic local
implementations. PCM16 frames can be normalized to the STT target rate; hosted
provider adapters may consume PCMU, PCMA, or Opus natively. Adapters are warmed
at process startup and can be replaced independently. Carrier SIP/RTP
termination remains the responsibility of the selected telephony adapter or
managed media bridge.

## Turn-taking and safety

Every call uses a persisted state machine and monotonically increasing event
sequence. A final caller transcript advances at most one state. Speech-start
during playback cancels the active generation, emits the caller's heard-response
boundary, and instructs the gateway to flush queued audio. Each STT, model, TTS,
and speech-end-to-first-audio duration is recorded separately.

Business tools are allowlisted per flow. Read-only customer lookup can execute
during a call; callback creation always enters human review. No payment,
scheduling, invoice, outbound-message, or other high-risk tool is exposed.

## Data lifecycle

Call lifecycle events are immutable, versioned, tenant-scoped, and ordered.
Transcript segments reference their source event sequence and media offsets.
Recordings are stored only after consent and carry a configured retention date
and legal-hold flag. The control plane restricts recording access to authorized
roles. Provider payload reconciliation, lifecycle deletion, encryption key
policy, and warehouse delivery are expanded in the reliability and call-data
milestones.

## Operational boundary

The default pilot is internal, region-allowlisted, time-bounded, and concurrency
limited. Local simulation covers consent, accents, noisy frames, interruption,
DTMF, silence, voicemail, transfer, hangup, duplicate/stale webhooks, codec
negotiation, recording alignment, and latency measurement. Before external
traffic, operators must configure a real carrier and speech adapters, validate
the transfer destination, perform jurisdiction-specific disclosure/recording
legal review, and complete live internal call certification.

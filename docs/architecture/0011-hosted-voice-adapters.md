# ADR 0011: Hosted voice adapters (Twilio and Gemini)

- Status: accepted
- Date: 2026-10-24

## Context

ADR 0006 shipped the voice pilot with deterministic local adapters only. The
local STT reads a `transcript_hint` from each frame, and the local TTS returns
UTF-8 text bytes. Neither can serve a real phone call. Before any external
traffic, the gateway needs a carrier connection and hosted speech.

## Decision

**Telephony: Twilio Programmable Voice with bidirectional Media Streams.**

- `POST /v1/telephony/twilio/voice/{tenant_id}` admits the call. It applies the
  existing pilot policy (enabled flag, region, hours, concurrency) and answers
  with `<Connect><Stream>`. The signed, expiring media token travels as a
  `<Parameter>`, because Twilio removes query strings from Stream URLs.
- When admission is denied, TwiML apologises and dials the flow's transfer
  number. If no transfer number is configured, it hangs up. A denied caller is
  never left in silence.
- Every webhook must carry a valid `X-Twilio-Signature`, computed over
  `OMNI_VOICE_PUBLIC_BASE_URL` plus the request path. Without
  `OMNI_TWILIO_AUTH_TOKEN`, the Twilio routes fail closed with 503.
- Each Twilio webhook body is stored as immutable raw evidence, keyed by an
  idempotent provider event ID.
- The bridge translates messages in both directions:

  | Twilio message | Gateway frame |
  |---|---|
  | `media` | `audio` |
  | `dtmf` | `dtmf` |
  | `mark` (echoed) | `playback_complete` |
  | `stop` | `hangup` |

  Barge-in is sent to Twilio as `clear`.
- Transfers and hang-ups wait until Twilio echoes the mark that follows the
  final line, so the caller hears the whole handoff or goodbye first. A call is
  transferred or hung up through the REST API at most once.
- `POST /v1/telephony/twilio/status` reconciles terminal statuses: completed,
  busy, failed, no-answer and canceled.

**STT: Gemini, one endpointed utterance at a time.** Gemini audio understanding
is request/response, so the gateway endpoints speech itself:

- It decodes G.711 μ-law to PCM16, then runs an energy-based segmenter.
- The segmenter keeps 200 ms of pre-roll before onset and needs
  `OMNI_VOICE_VAD_MIN_SPEECH_MS` of speech to count as onset. It ends an
  utterance after `OMNI_VOICE_VAD_END_SILENCE_MS` of silence, or when the
  utterance reaches `OMNI_VOICE_VAD_MAX_UTTERANCE_MS`.
- Each utterance is sent to Gemini as WAV, under
  `OMNI_VOICE_HOSTED_STT_TIMEOUT_MS` and the existing circuit breaker.
- Speech onset fires `speech_start`, which drives barge-in.
- Gemini returns no confidence score, so transcripts carry a nominal 9000 bps.

**TTS: Gemini.** Gemini returns 24 kHz PCM. The gateway converts it to the
call's codec and sample rate and sends it in 20 ms frames. Each media session
selects its own TTS through a context variable, so concurrent calls never share
codec state. Simulations keep the deterministic adapter.

**Model: unchanged and scripted.** The disclosure, consent, confirmation and
transfer lines are fixed text that legal review applies to. A generative model
must not paraphrase them. The `RealtimeModel` protocol is kept for a future
flow that has an approved generative step.

The G.711 codec and resampler are pure Python, because `audioop` was removed in
Python 3.13. The encoder matches the Sun G.711 reference that `audioop` used
for every 16-bit input.

## Failure behavior

| Failure | Behavior |
|---|---|
| STT error, timeout, or open circuit | Treated like silence. The caller is re-prompted, and a second failure transfers them to a person (`repeated_silence`). |
| Empty or noise-only transcription | Recorded as `speech.unrecognized`; no state change. |
| TTS error | The line stays in the transcript and a `provider.fallback.activated` event is recorded. The caller hears silence, and the silence path escalates. |
| REST call control fails | Recorded as `twilio.call_control.failed` and counted in `twilio_call_control_failures_total`. |

## Consequences

- **Latency misses the 800 ms first-audio target.** End-of-speech detection
  alone waits `OMNI_VOICE_VAD_END_SILENCE_MS` (700 ms by default), and the
  batch STT and TTS requests add their own round trips. The pilot records each
  stage separately. A streaming speech provider would have to replace these
  adapters before the SLO can be met with hosted speech.
- **Barge-in is lost while a turn is being processed.** Media is buffered while
  a hosted request is in flight, and the buffer drops its oldest frames when
  full.
- **The VAD is energy-based.** Loud background noise can trigger false onsets.
  Tune `OMNI_VOICE_VAD_THRESHOLD` per line during certification.
- **Legal review is still required.** ADR 0006 already requires jurisdiction
  review of recording and consent before external calls. That requirement is
  unchanged.

Setup steps are in the [Twilio voice runbook](../runbooks/twilio-voice.md).

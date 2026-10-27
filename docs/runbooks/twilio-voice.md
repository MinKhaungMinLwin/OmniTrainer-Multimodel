# Twilio voice runbook

This runbook connects a Twilio phone number to the voice gateway, with Gemini
speech. The design and failure behavior are in
[ADR 0011](../architecture/0011-hosted-voice-adapters.md).

## Before you start

You need:

- the voice gateway reachable over public HTTPS and WSS (for example
  `https://voice.example.com`);
- a Twilio account and a voice-capable phone number;
- a Gemini API key, or Vertex AI credentials with `OMNI_AI_PROVIDER=vertex`;
- the voice pilot enabled for the tenant, with a `transfer_number` (Voice
  workspace, or `PUT /api/v1/voice/config`); and
- jurisdiction-specific legal review of the disclosure and recording consent.

## Configure the gateway

Set these variables on the voice service:

```bash
OMNI_VOICE_PUBLIC_BASE_URL="https://voice.example.com"  # exactly what Twilio calls
OMNI_TWILIO_ACCOUNT_SID="AC..."
OMNI_TWILIO_AUTH_TOKEN="..."                             # keep in a secret store
OMNI_VOICE_STT_PROVIDER="gemini"
OMNI_VOICE_TTS_PROVIDER="gemini"
GEMINI_API_KEY="..."
```

`OMNI_VOICE_PUBLIC_BASE_URL` must match the scheme, host and any path prefix
that Twilio uses. Signatures are computed over that URL, so a mismatch rejects
every webhook with 401.

After restarting the service, check `GET /health/providers`. It should report
`gemini-stt` and `"twilio": "configured"`.

## Configure the Twilio number

In the Twilio console, open **Phone Numbers → Manage → Active numbers** and
select the number. Then set:

- **A call comes in:** Webhook, `HTTP POST`,
  `https://voice.example.com/v1/telephony/twilio/voice/<tenant-id>?region=<region>`.
  - `region` must be in the flow's `allowed_regions`. If you leave it out, the
    gateway uses Twilio's lowercased `FromCountry`, for example `us`.
  - To use a flow other than `after_hours_intake`, add `&flow_key=<key>`.
- **Call status changes:**
  `https://voice.example.com/v1/telephony/twilio/status`.

## Certify with internal calls

1. Call the number from an allowlisted phone. You should hear the disclosure.
2. Say "yes", your name, and a reason, then confirm. The call should end after
   the goodbye. The Voice workspace should show a pending callback review with
   `gemini-stt` transcript segments.
3. Call again and talk over the disclosure. Playback should stop, and the event
   history should contain `playback.interrupted`.
4. Say an emergency keyword. You should hear the handoff line, and the call
   should be dialed through to the transfer number.
5. Press 0. The call should transfer.
6. Stay silent twice. The call should transfer with reason `repeated_silence`.
7. Disable the flow and call. You should hear the unavailable message, and the
   call should dial the transfer number.

If callers get cut off mid-sentence or noise triggers turns, tune these
settings for the line:

- `OMNI_VOICE_VAD_THRESHOLD` (RMS, default 500);
- `OMNI_VOICE_VAD_END_SILENCE_MS` (default 700); and
- `OMNI_VOICE_VAD_MIN_SPEECH_MS` (default 200).

## Troubleshooting

| Symptom | Check |
|---|---|
| Twilio reports 401 | Make sure `OMNI_VOICE_PUBLIC_BASE_URL` matches the configured webhook URL, and that the auth token is current. |
| Twilio reports 503 | `OMNI_TWILIO_AUTH_TOKEN` is not set. |
| The caller hears "assistant is unavailable" | Admission was denied. Check `twilio_admission_denied_<code>_total` on `/metrics`; the code is `flow_disabled`, `region_denied`, `outside_hours`, or `capacity`. |
| The stream closes with 4401 | The media token is invalid or expired. Tokens last one hour from admission. |
| The caller is re-prompted and then transferred | STT is failing. Look for `provider.fallback.activated` events and `stt_failures_total`, then follow the [voice reliability runbook](voice-reliability.md). |
| The call never transfers or ends | Look for a `twilio.call_control.failed` event, and check the REST credentials. |

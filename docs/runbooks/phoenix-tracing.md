# Phoenix tracing runbook

Omni uses OpenTelemetry with OpenInference semantic conventions and an authenticated Arize Phoenix collector.
PostgreSQL remains the durable business audit log; Phoenix is the diagnostic view for model, tool, retrieval,
approval, audio, and voice-turn latency.

## Staging deployment

1. Create a DNS-only `A` record for `traces.<domain>` pointing to the EC2 Elastic IP.
2. Set `TRACES_DOMAIN`, `PHOENIX_SECRET`, `PHOENIX_ADMIN_SECRET`, and
   `PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD` in `.env.staging`. All secrets must be independent.
3. Keep `OMNI_TRACING_CAPTURE_CONTENT=false`. This hides model prompts, outputs, transcript text, images,
   and customer field values while retaining tool names, field names, status, timing, token counts, and errors.
4. Run `./scripts/deploy_ec2_staging.sh`.
5. Open `https://$TRACES_DOMAIN`, sign in as `admin@localhost`, and change the initial password immediately.

The Phoenix collector is internal at `http://phoenix:6006/v1/traces`; only Caddy publishes its authenticated UI.
The API and voice services use the admin bearer secret for ingestion. Phoenix data persists in the
`staging-phoenix` Docker volume mounted at the non-root image's writable home directory. Phoenix's optional agent assistant, server-side shell/GitHub tools, MCP server,
code mode, and product telemetry are disabled in this tracing-only deployment.

## Trace model

- `copilot.agent.run` is the root agent span. Its session ID is the Omni conversation ID.
- Google GenAI calls are auto-instrumented as child LLM spans.
- `tool.<name>` spans contain risk, execution status, and input field names but never field values.
- `knowledge.search` is a retriever span with query word count and result count.
- `copilot.approval.decision` records approve, edit, or reject decisions without review notes or arguments.
- `audio.transcribe` and `audio.synthesize` record byte/character counts, model, voice, and latency—not content.
- `voice.turn` groups turns by call ID without exporting caller or transcript text.

The Assistant header exposes the first eight characters of the trace ID and links to Phoenix. Search that trace ID
in the Phoenix project when diagnosing a run.

## Operations

Check service health and recent exporter errors:

```bash
docker compose --env-file .env.staging -f compose.staging.yaml ps phoenix api voice
docker compose --env-file .env.staging -f compose.staging.yaml logs --tail=100 phoenix api voice
curl --fail https://$TRACES_DOMAIN/healthz
```

For a low-traffic private demo, trace every request with `OMNI_TRACING_SAMPLE_RATIO=1.0`. Before higher traffic,
place an OpenTelemetry Collector between Omni and Phoenix and use tail sampling so failed and write-action traces
are retained while routine successful reads are sampled.

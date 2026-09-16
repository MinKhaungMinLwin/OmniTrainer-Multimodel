# Voice reliability runbook

## SLOs and alerts

- First audio p95 below 800 ms; call admission availability at least 99.9%.
- Page on load shedding. Warn on media overflow, provider fallback spikes, or
  latency breach for the durations defined in `deploy/monitoring/alerts.yml`.

## Provider degradation

1. Check `/health/providers`, `/v1/reliability`, and `/metrics` on the voice
   gateway. Correlate the circuit state, timeouts, fallback count, and queue
   high-water mark.
2. If a single provider is failing, keep its circuit open and route through the
   configured fallback. If neither path is healthy, use DTMF/human transfer or
   the reviewed callback flow; do not silently continue a consequential flow.
3. If buffers overflow, scale voice replicas, reduce admission capacity, and
   verify CPU/network saturation. Never increase buffers without checking the
   end-to-end latency budget.
4. Reconcile provider call records and replay the call-data outbox after the
   incident. Confirm transcript/event sequence continuity before closing it.

## Fault exercise

Run `uv run pytest tests/test_scale_readiness.py tests/test_voice_pilot.py`.
Exercise provider timeout, open circuit, stale/oversized media, concurrent
session admission, interruption, disconnect, and duplicate webhook cases in
staging before promotion.

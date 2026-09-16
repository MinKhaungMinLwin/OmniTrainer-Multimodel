# Omni Model — Product and Platform Upgrade Plan

## 1. Objective

Evolve the current multimodal-moderation proof of concept into a production-ready business automation platform for jobs, customers, invoices, scheduling, AI-assisted work, and real-time voice calls.

The first production release should let a business:

1. manage customers, jobs, appointments, and invoices from web and mobile;
2. use an AI assistant that can safely search records and propose or execute approved actions;
3. run an inbound or outbound voice-agent pilot;
4. review every AI action and call through transcripts, events, structured outcomes, and quality metrics; and
5. deploy, observe, and scale the platform through repeatable infrastructure and CI/CD.

## 2. Current-State Assessment

The repository is currently a Python proof of concept containing:

- a Gradio training chat interface;
- FastAPI endpoints for text, image, audio, and video moderation;
- Gemini/Pydantic AI agents with structured moderation output;
- Phoenix/OpenTelemetry tracing; and
- unit tests and modality-specific eval scripts.

It does not yet contain React or React Native applications, persistent business data, multi-tenant authentication, workflow orchestration, streaming APIs, telephony, a call-event pipeline, a warehouse, production infrastructure, or CI/CD. The upgrade is therefore a platform build that preserves reusable moderation/eval concepts while replacing the demo shell incrementally.

Before feature work, restore a reproducible project manifest and environment template: the README references `pyproject.toml`, `uv.lock`, and `env.example`, but those files are not currently present in the repository.

## 3. Target Architecture

Start with a modular monolith plus asynchronous workers. Split services only when traffic, ownership, or reliability requirements justify it.

```text
React Web / React Native
          |
 API gateway + auth + tenant context
          |
 FastAPI application
  |-- customers / jobs / scheduling / invoices
  |-- conversations / reviews / automations
  |-- AI gateway and tool registry
          |
 PostgreSQL ---- Redis/cache
          |
 Outbox -> queue/event bus -> durable workers/workflows
                              |-- notifications and automations
                              |-- indexing and extraction
                              |-- call post-processing
                              |-- warehouse loading

Telephony provider <-> media gateway <-> realtime voice runtime
                                        |-- streaming STT/LLM/TTS
                                        |-- business tools
                                        |-- call event stream

Object storage: recordings and attachments
Warehouse: modeled call/product data -> BI and conversation intelligence
Observability: logs, metrics, traces, evals, alerts, cost and audit events
```

Recommended boundaries:

- **System of record:** PostgreSQL for transactional data; object storage for media and documents.
- **Async execution:** a durable workflow engine for long-running automations and call post-processing; a queue/event bus for fan-out and buffering.
- **AI gateway:** model/provider abstraction, prompt and tool versioning, policy checks, usage accounting, tracing, and fallbacks.
- **Realtime media plane:** isolated from the normal HTTP API so live-call latency and availability are not affected by dashboard traffic.
- **Analytics plane:** immutable events copied to the warehouse; dashboards never query the live voice path.

Every record and event should carry `tenant_id`, a stable entity ID, `occurred_at`, `schema_version`, and a correlation/trace ID. Every agent run should also carry model, prompt, tool, policy, and knowledge-base versions.

## 4. Delivery Principles

- Build one vertical slice—customer to scheduled job to completed invoice—before broadening each module.
- Default agent tools to read-only. Require policy checks, idempotency keys, and approval for consequential writes.
- Treat transcripts, tool calls, human edits, and approvals as audit records, not temporary UI state.
- Keep raw call events immutable and derive corrected/enriched records separately.
- Define service-level objectives and eval gates before enabling autonomous or realtime behavior.
- Release behind tenant-scoped feature flags with canary cohorts and explicit rollback paths.

## 5. Step-by-Step Delivery Plan

### Step 0 — Product Definition and Success Measures (1–2 weeks)

1. Select the first customer segment and its primary job lifecycle.
2. Map roles and permissions: owner, dispatcher, technician/agent, accountant, reviewer, and administrator.
3. Write the canonical workflows for lead/customer intake, job creation, scheduling, completion, invoice approval, payment status, AI review, and call escalation.
4. Classify actions by risk:
   - read-only;
   - reversible writes;
   - externally visible or financial actions; and
   - prohibited without a human.
5. Define launch measures: task completion, automation success, approval rate, call containment, transfer rate, p95 latency, cost per resolved interaction, and critical-error rate.

**Exit gate:** signed-off workflow maps, role matrix, initial SLOs, and a prioritized MVP scope.

### Step 1 — Stabilize the Existing Repository (1 week)

1. Add a reproducible Python project manifest, lockfile, environment template, lint/type/test commands, and supported runtime versions.
2. Remove tracked bytecode/cache artifacts and add an appropriate `.gitignore`.
3. Separate unit tests from tests that require external model credentials.
4. Establish an initial CI check for formatting, linting, typing, unit tests, and secret scanning.
5. Preserve current moderation endpoints and evals as a legacy compatibility module.
6. Record baseline moderation quality, latency, error rate, and model cost.

**Exit gate:** a clean checkout installs and passes deterministic CI without external credentials; model evals run as an explicit job.

### Step 2 — Create the Platform Skeleton (2–3 weeks)

1. Adopt a monorepo structure such as `apps/web`, `apps/mobile`, `services/api`, `services/worker`, `services/voice`, `packages/contracts`, and `infra`.
2. Build the React web shell and React Native/Expo mobile shell with shared design tokens and generated API types.
3. Modularize FastAPI by domain rather than adding more routes to the current demo file.
4. Add PostgreSQL migrations, local containers, object storage, Redis, and development seed data.
5. Implement identity, tenant isolation, RBAC, audit logging, secrets handling, request IDs, and standardized errors.
6. Publish an OpenAPI contract and generate typed web/mobile clients in CI.

**Exit gate:** a user can sign in, switch only among authorized tenants, and load a protected empty application on web and mobile.

### Step 3 — Build the Core Business Model and APIs (3–4 weeks)

1. Define entities and lifecycle rules for customers, contacts, locations, jobs, job status history, appointments, technicians/resources, invoices, line items, payments/status, notes, and attachments.
2. Define UTC storage and tenant/location timezone behavior before scheduling work.
3. Implement CRUD plus domain commands such as `schedule_job`, `complete_job`, `issue_invoice`, and `void_invoice`; avoid exposing arbitrary status mutation.
4. Add optimistic concurrency, idempotency keys, validation, authorization, and append-only audit events.
5. Use a transactional outbox so database changes and emitted domain events cannot diverge.
6. Add contract, authorization, migration, and domain-invariant tests.

**Exit gate:** APIs support the complete customer → job → appointment → completion → invoice vertical slice with an auditable event trail.

### Step 4 — Deliver Web and Mobile Product Flows (4–6 weeks)

1. Build customer list/detail, search, contact/location history, and activity timeline.
2. Build job list/detail, creation wizard, status board, notes, media, and assignment.
3. Build daily/weekly calendar, unassigned queue, technician availability, conflict detection, timezone-safe rescheduling, and mobile “my schedule.”
4. Build invoice draft, line-item editing, review, issue/void states, PDF preview, and payment-status display.
5. Add optimistic UI only for reversible actions; show pending/failed server state for consequential actions.
6. Meet accessibility, responsive-layout, offline/read-only degradation, empty/error/loading state, and localization requirements.
7. Add component tests and browser/mobile end-to-end tests for the vertical slice.

**Exit gate:** role-based web and mobile users complete the primary workflow without the AI assistant.

### Step 5 — Build the AI Chat Experience and Streaming Protocol (3–4 weeks)

1. Define a provider-neutral conversation schema for user/assistant messages, citations, attachments, tool calls, tool results, errors, approval requests, feedback, and final status.
2. Define an ordered streaming event protocol: run started, text delta, tool proposed, approval requested, tool running, tool result, corrected result, completed, failed, and cancelled.
3. Implement resumable server-sent events for normal chat; retain WebSocket/WebRTC paths for bidirectional realtime use.
4. Render tool activity as structured cards—not raw JSON—with inputs, status, result, source links, retry, and audit details.
5. Add stop/regenerate, reconnect/resume, partial failure, rate-limit, and long-running task states.
6. Add human review UI: approve, reject, edit proposed arguments, provide a reason, and resume the same run.
7. Capture thumbs up/down, categorized feedback, free text, and the exact run/version context.

**Exit gate:** web and mobile can stream a multi-step agent run, survive reconnection, render tool results, and pause/resume for review.

### Step 6 — Production AI Workflow Foundation (4–6 weeks)

1. Create an AI gateway for model routing, structured outputs, retries, timeouts, fallbacks, redaction, token/cost accounting, and trace propagation.
2. Build a versioned tool registry with JSON schemas, tenant-scoped authorization, deadlines, idempotency, dry-run support, and audit logs.
3. Implement initial read tools: find customer, fetch job history, check availability, retrieve invoice, and search policy/knowledge.
4. Add write tools gradually: draft job, propose schedule, add note, draft invoice, and send a message. Place policy and approval gates before execution.
5. Use durable workflows for runs that pause for approval, retry external systems, or last beyond an HTTP request.
6. Build RAG ingestion: source authorization, parsing, chunking, metadata, indexing, freshness/delete propagation, hybrid retrieval, reranking, citations, and access filtering.
7. Build versioned classification/extraction services with typed schemas, confidence, provenance, abstention thresholds, and human correction queues.
8. Defend against prompt injection and data leakage by separating untrusted retrieved content from system/tool policy.

**Exit gate:** a traced agent can answer cited questions and safely propose or execute authorized business actions with replayable audit history.

### Step 7 — Launch Business Automations (3–5 weeks)

1. Implement trigger/action workflows from domain events, schedules, inbound messages, and calls.
2. Ship a small template library: lead intake, appointment reminder, missed-call follow-up, job completion summary, invoice draft, overdue-invoice follow-up, and review escalation.
3. Add an automation builder with trigger, conditions, AI/extraction step, action, approval rule, test mode, versioning, and activation controls.
4. Add deduplication, rate limiting, retries, dead-letter handling, compensation, run history, replay, and bulk pause/kill controls.
5. Show why each automation ran and which records/messages it changed.

**Exit gate:** designated tenants run selected templates in shadow mode and then production with measurable success and reversible rollout.

### Step 8 — Build a Realtime Voice-Agent Pilot (5–8 weeks)

1. Choose one constrained call flow, such as after-hours intake or appointment confirmation; define disclosure, consent, recording, retention, and emergency/escalation rules with legal review.
2. Introduce provider adapters for telephony/SIP, streaming STT, TTS, and the realtime model so vendors can be changed independently.
3. Build webhook verification, call admission, session state, media streaming, codec/resampling, and a WebRTC/SIP media gateway.
4. Implement incremental STT, endpointing/voice-activity detection, turn-taking, streaming model output, streaming TTS, and audio playout.
5. Implement barge-in: detect user speech, cancel model/TTS output, flush queued audio, preserve the heard-response boundary, and resume from corrected state.
6. Expose only the minimal business tools required by the chosen call flow and reuse approval/escalation policy.
7. Add warm connections, regional routing, prompt/context caching, short first utterances, and stage-by-stage latency budgets.
8. Test with simulation, noisy audio, accents, silence, DTMF, voicemail, transfers, hangups, and real internal calls before a tenant pilot.

**Initial latency targets:** continuously measure speech-end-to-first-audio and each STT/model/TTS segment; set final SLOs from pilot data rather than hiding latency in a single average.

**Exit gate:** the pilot flow completes or transfers safely, with recordings/transcripts aligned to events and no unsupervised high-risk action.

### Step 9 — Harden Voice Reliability (3–5 weeks, then ongoing)

1. Use bounded audio/event buffers, flow control, queue-depth metrics, stale-frame dropping, and load shedding to prevent unbounded backpressure.
2. Define degradation modes: alternate STT/TTS/model, reduced toolset, cached prompts, DTMF fallback, human transfer, callback creation, and polite termination.
3. Add per-stage timeouts, circuit breakers, retry budgets, regional/provider health checks, and session checkpoints.
4. Ensure every call can be reconciled after disconnect from provider records and durable event logs.
5. Run fault injection for provider latency, dropped frames, duplicate webhooks, queue saturation, model timeout, worker loss, and partial regional failure.
6. Create operator runbooks and alerts based on user impact, not only component uptime.

**Exit gate:** load and chaos tests demonstrate bounded resource use, no silent call loss, tested failover, and graceful caller-facing degradation.

### Step 10 — Build the Call-Data Pipeline (3–5 weeks)

1. Publish versioned immutable events for call lifecycle, media segments, speaker turns, transcript revisions, agent output, tool calls, transfers, consent, feedback, and outcomes.
2. Persist raw provider payloads and recordings in encrypted object storage with tenant-specific retention and legal-hold rules.
3. Build streaming consumers for normalization, diarization/alignment, transcript finalization, PII redaction, structured extraction, and search indexing.
4. Use event IDs, sequence numbers, idempotent consumers, replay, dead-letter queues, and schema compatibility checks.
5. Load raw, cleaned, and modeled layers into the warehouse; reconcile warehouse call counts/durations against the telephony provider.
6. Support corrected transcripts and extractor versions without overwriting raw evidence.

**Exit gate:** every completed, failed, or transferred call is queryable end to end, and daily reconciliation meets the agreed completeness target.

### Step 11 — Deliver Analytics and Conversation Intelligence (3–5 weeks)

1. Define governed dimensions and facts for tenant, customer, agent, call, conversation, job, invoice, model run, tool use, and cost.
2. Publish metric definitions for volume, answer rate, containment, transfer, abandonment, duration, resolution, appointment conversion, automation success, latency, quality, and cost.
3. Build dashboards for operations, quality, product funnel, model performance, and spend with tenant and role-level access controls.
4. Add conversation-intelligence features: topic/reason, intent, sentiment trajectory, objection, compliance flag, action item, outcome, summary, and searchable transcript snippets.
5. Display extraction confidence and provenance; route low-confidence or high-risk findings to review.
6. Add data freshness, completeness, and model-quality indicators to dashboards.

**Exit gate:** dashboards reconcile to source systems, enforce tenant access, and link aggregate metrics back to reviewable calls and traces.

### Step 12 — Establish Evals, Monitoring, and Release Gates (start in Step 5; mature here)

1. Expand the current modality evals into versioned datasets for chat tasks, tool selection/arguments, RAG relevance/groundedness/citations, extraction, safety, voice turn-taking, interruption, and business outcome.
2. Create a privacy-safe regression set from reviewed production failures and representative synthetic calls.
3. Run offline evals on every prompt/model/tool/retriever change; compare against the production baseline and prevent material regressions.
4. Run shadow/canary releases and online sampling with calibrated human review.
5. Monitor task success, false action rate, approval/edit/rejection rates, unsupported claims, retrieval misses, transfer rate, word/semantic error measures, interruption success, dead air, stage latency, error rate, and cost per successful outcome.
6. Segment by tenant, workflow, language/accent, provider, model/prompt version, and time; alert on drift using minimum sample sizes and confidence bounds.
7. Maintain scorecards, experiment records, rollback criteria, and incident-to-eval feedback loops.

**Exit gate:** no AI or voice version reaches broad production without reproducible eval evidence, canary observation, cost projection, and rollback readiness.

### Step 13 — Production Cloud Infrastructure and CI/CD (begin in Step 2; harden continuously)

1. Provision network, compute, managed database, cache, object storage, queue/workflow system, warehouse integration, secrets, DNS, certificates, and observability through infrastructure as code.
2. Separate development, staging, and production accounts/projects and data; prefer short-lived workload identity over static credentials.
3. Containerize services, add health/readiness checks, autoscaling, disruption budgets, regional strategy, and resource limits.
4. Build CI for lint/type/unit/contract/integration/eval/security/IaC checks and artifact provenance.
5. Build CD with immutable artifacts, migration safety checks, feature flags, canary deployment, automated verification, and one-command rollback.
6. Add database backup/restore tests, object-storage lifecycle rules, queue replay procedures, disaster-recovery exercises, and RPO/RTO targets.
7. Monitor infrastructure and third-party spend; enforce tenant/model quotas and anomaly alerts.

**Exit gate:** staging is reproducible from code, production releases are canaried and reversible, and recovery procedures have been exercised.

### Step 14 — Controlled Rollout and Scale (2–4 weeks per cohort)

1. Run employee dogfood, then design-partner tenants, then small production cohorts.
2. Start AI writes in suggestion mode; graduate individual tools to auto-execution only after tool-specific error and override thresholds are met.
3. Start voice with limited hours, call reasons, concurrency, and explicit human fallback.
4. Review feedback, evals, incidents, support load, latency, and unit economics weekly.
5. Promote capabilities independently through feature flags; maintain a kill switch for each automation, tool, model route, and voice flow.

**Exit gate:** the cohort meets product, safety, reliability, and cost SLOs for an agreed observation window before expansion.

## 6. Suggested Release Milestones

Assuming a cross-functional team can work on product, platform, AI, data, and voice in parallel after the foundation is stable:

| Milestone | Indicative window | Demonstrable outcome |
|---|---:|---|
| M0: Foundation | Weeks 1–6 | Reproducible repo, CI, auth/tenancy, database, web/mobile shells |
| M1: Operations MVP | Weeks 7–14 | Customer → job → schedule → invoice flow on web/mobile |
| M2: AI Copilot | Weeks 12–20 | Streaming chat, RAG, read tools, reviewed write proposals |
| M3: Automations | Weeks 18–24 | Durable automation templates with approvals and run history |
| M4: Voice Pilot | Weeks 20–32 | One constrained live-call workflow with human fallback |
| M5: Intelligence | Weeks 26–36 | Reconciled call pipeline, dashboards, conversation intelligence |
| M6: Scale Readiness | Weeks 32–40 | Reliability tests, mature eval gates, canary CD, recovery exercises |

Implementation status: **M1 Operations MVP completed on 2026-09-16.** The next
delivery milestone, **M2 AI Copilot, was completed on 2026-09-16** with durable
streaming runs, cited knowledge retrieval, tenant-scoped tools, reviewed write
proposals, feedback, and extraction correction. **M3 Automations was completed
on 2026-09-16** with seven versioned templates, transactional event and schedule
dispatch, shadow/production rollout, approvals, retry/dead-letter controls,
replay, compensation, kill switches, and explainable run history. The next
milestone, **M4 Voice Pilot, was completed on 2026-09-16** with a constrained
after-hours intake flow, signed telephony/media admission, streaming turn
handling, barge-in, consent and emergency transfer policy, aligned recordings
and transcripts, and reviewed callback creation on web and mobile. External
carrier certification remains a deployment activity requiring provider
credentials. **M5 Intelligence was completed on 2026-09-16** with immutable raw
provider evidence, replayable transcript cleaning and PII redaction, corrected
transcript versions, structured conversation intelligence, human quality
review, reconciled call facts, governed metrics, search, and web/mobile
dashboards. **M6 Scale Readiness was completed on 2026-09-16** with bounded
voice-media admission and backpressure, provider deadlines/circuit breakers and
fallback, reliability metrics and alerts, versioned quality/safety/cost/latency
release gates, cloud infrastructure as code, resource-aware runtime manifests,
canary delivery with rollback, and checksummed recovery exercises. Cloud,
carrier, DNS, certificate, and production identity values remain deployment
inputs rather than repository secrets.

These are planning ranges, not commitments. Re-estimate after Steps 0–2 based on team size, provider choices, compliance scope, and whether payments/accounting integrations are included.

## 7. Workstreams and Ownership

- **Product/design:** workflows, role behavior, web/mobile UX, review operations, rollout criteria.
- **Application:** business APIs, React, React Native, access control, transactional integrity.
- **AI:** model gateway, tools, RAG, extraction, prompts, safeguards, evals.
- **Voice:** telephony/media plane, STT/TTS, turn-taking, reliability, call operations.
- **Data:** event contracts, call pipeline, warehouse models, BI, data quality.
- **Platform/SRE/security:** IaC, CI/CD, observability, incident response, privacy, recovery.

For a smaller team, retain these as ownership hats but reduce scope rather than attempting every workstream simultaneously. The safest initial cut is the operations MVP plus AI copilot, followed by one voice workflow.

## 8. Definition of Done for Every Capability

A feature is complete only when it includes:

- tenant-aware authorization and audit records;
- API/schema versioning and migration strategy;
- loading, empty, error, retry, cancellation, and degraded states;
- unit, contract, integration, and appropriate end-to-end tests;
- logs, metrics, traces, dashboards, alerts, and an owner;
- privacy/retention classification and redaction where needed;
- performance and cost budgets;
- feature flag, rollout plan, rollback/kill path, and runbook; and
- user documentation and support notes.

AI features additionally require a versioned eval dataset, offline comparison, online monitoring, human escalation behavior, and stored feedback. Voice features additionally require call simulation, failover testing, provider reconciliation, consent handling, and a tested transfer/degradation path.

## 9. Major Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Building all modules at once | Ship the vertical slice first and gate later work on observed use |
| Agent makes an incorrect consequential change | Typed tools, least privilege, dry run, approval, idempotency, audit, rollback |
| Cross-tenant data exposure through RAG/tools | Enforce tenant filters in the data layer and test adversarial access cases |
| Voice latency creates poor turn-taking | Separate media plane, streaming stages, barge-in, regional routing, stage budgets |
| Provider or model outage disrupts calls | Adapters, circuit breakers, alternate routes, human transfer, callback fallback |
| Transcript/warehouse records are incomplete | Immutable sequenced events, replayable consumers, and provider reconciliation |
| Dashboard metrics lose trust | Governed definitions, lineage, freshness indicators, and source reconciliation |
| Model drift or silent cost growth | Segmented monitoring, eval gates, quotas, cost-per-outcome alerts, canaries |
| Compliance/privacy failure | Consent and retention policy, encryption, redaction, access logs, legal review |
| Premature microservices increase delivery cost | Modular monolith first; split the voice media plane and workers only where needed |

## 10. First 30-Day Backlog

1. Confirm the MVP user, call flow, roles, regions, and compliance constraints.
2. Restore project packaging, lockfile, environment template, `.gitignore`, and deterministic CI.
3. Approve the monorepo layout and architecture decision records.
4. Define tenant/auth strategy and the first customer/job/schedule/invoice schemas.
5. Define domain-event, call-event, conversation-message, and streaming-event contracts.
6. Provision local PostgreSQL, Redis, object storage, and queue/workflow dependencies.
7. Scaffold the React web, React Native mobile, modular FastAPI API, and worker.
8. Implement authentication, tenant context, migrations, audit log, outbox, and one customer API/UI slice.
9. Build the first deterministic test fixture and baseline the existing moderation evals.
10. Run vendor spikes for telephony, STT/TTS/realtime model, workflow engine, and warehouse; record decisions against latency, reliability, compliance, portability, and cost criteria.

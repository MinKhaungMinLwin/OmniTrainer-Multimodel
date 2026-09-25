# ADR 0010: Governed environmental workspace

## Status

Accepted.

## Context

Environmental laboratory data often arrives as spreadsheets and requires
manual validation, comparison, citation, and report assembly. The portfolio
needs to demonstrate this workflow without presenting fictional criteria as
regulatory advice or allowing an AI draft to become an issued report silently.

## Decision

The platform stores environmental projects, immutable uploaded workbooks, and
versioned report drafts within the authenticated tenant boundary. Uploads are
limited to XLSX and 20 MB, stored in the configured S3-compatible object store,
and identified by SHA-256 digest. Validation and unit conversion are
deterministic and shared with the environmental MCP server.

Retrieval is limited to the authorized workspace documents and returns stable
source URIs. Report generation combines the persisted validation result with
those citations and always starts in `draft`. A reviewer must record an
explicit approve or reject decision and rationale. Draft DOCX/XLSX exports are
allowed for review, but carry a prominent warning. Creation, upload, drafting,
review, and export actions produce tenant-scoped audit events.

All bundled criteria and workbooks are synthetic. They must not be used for a
real client, health, regulatory, remediation, or site-classification decision.

## Consequences

- The demonstration is replayable and usable without a model-provider call.
- Evidence, decisions, and exports remain traceable.
- Unsupported searches abstain instead of inventing a source.
- A production deployment still requires organization-specific schemas,
  jurisdiction-controlled standards, retention policies, malware scanning,
  qualified reviewer roles, and client-approved data residency controls.

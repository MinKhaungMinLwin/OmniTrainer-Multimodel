# ADR 0009: Environmental project MCP boundary

- Status: Accepted
- Date: 2026-09-25

## Context

Environmental practitioners need to search technical references, inspect
laboratory workbooks, and draft rigid reports without allowing an LLM to invent
criteria, cross client boundaries, or issue professional conclusions. The
portfolio also needs to demonstrate Claude and Model Context Protocol skills
without representing synthetic data as a production environmental system.

## Decision

- Add a Python MCP v2 server with resources, prompts, and structured tools.
- Constrain all workbook paths to a selected project directory. Reject absolute
  paths, traversal, non-XLSX inputs, and unknown projects.
- Preserve sample identifiers, analytes, values, units, reporting limits, and
  qualifiers. Permit only explicitly tested unit conversions.
- Store demonstration criteria separately from model prompts and return a
  source URI with each comparison. The bundled values are fictional.
- Search only authorized Markdown references and explicitly abstain when no
  document matches.
- Return report content as `draft_requires_human_review` with
  `can_publish=false`. The MCP server has no publish, submit, or approval tool.
- Add Claude as another provider behind the existing gateway. Claude proposes
  the same typed tools; role checks, validation, approval, audit, and execution
  remain application responsibilities.
- Test tools through an in-memory MCP client so protocol discovery, JSON
  schemas, and structured output remain part of CI.

## Consequences

The workflow demonstrates the safety boundary and protocol integration with no
external model calls in CI. It is not regulatory software and does not yet
parse laboratory certificates, PDF tables, jurisdiction-specific criteria, or
produce DOCX/XLSX deliverables. Those capabilities require practitioner-defined
schemas, representative evaluation data, and a documented review procedure
before any pilot.

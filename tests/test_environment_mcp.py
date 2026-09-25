from pathlib import Path

import pytest
from mcp import Client

from services.environment_mcp.domain import (
    draft_screening_report,
    project_file,
    search_documents,
    validate_lab_workbook,
)
from services.environment_mcp.server import mcp


WORKSPACE = Path(__file__).parents[1] / "data" / "environment"


def test_demo_workbook_is_validated_and_screened():
    result = validate_lab_workbook("demo-site", "laboratory-results.xlsx", root=WORKSPACE)

    assert result.valid is True
    assert result.row_count == 4
    assert result.summary == {
        "above_criterion": 1,
        "at_or_below_criterion": 2,
        "no_criterion": 1,
    }
    assert result.screening[0].source_uri == "environment://documents/demo-screening-framework.md"


def test_project_workbook_cannot_escape_project_directory():
    with pytest.raises(ValueError, match="inside the selected project"):
        project_file("demo-site", "../other-project/results.xlsx", root=WORKSPACE)


def test_document_search_cites_sources_and_abstains():
    found = search_documents("How should a laboratory screening exceedance be reviewed?", root=WORKSPACE)
    missing = search_documents("volcanic seismology", root=WORKSPACE)

    assert found["abstained"] is False
    assert found["matches"][0]["source_uri"].startswith("environment://documents/")
    assert missing == {"query": "volcanic seismology", "matches": [], "abstained": True}


def test_report_is_always_a_non_publishable_review_draft():
    result = draft_screening_report(
        "demo-site",
        "laboratory-results.xlsx",
        "Summarize screening observations and review requirements",
        root=WORKSPACE,
    )

    assert result["status"] == "draft_requires_human_review"
    assert result["can_publish"] is False
    assert "qualified environmental practitioner" in result["report_markdown"]
    assert "GW-01: PFOS" in result["report_markdown"]


@pytest.mark.asyncio
async def test_mcp_server_exposes_and_executes_structured_tools(monkeypatch):
    monkeypatch.setenv("OMNI_ENVIRONMENT_WORKSPACE", str(WORKSPACE))
    async with Client(mcp) as client:
        tools = await client.list_tools()
        result = await client.call_tool(
            "validate_laboratory_workbook",
            {"project_id": "demo-site", "lab_file": "laboratory-results.xlsx"},
        )

    assert {tool.name for tool in tools.tools} == {
        "search_environmental_documents",
        "validate_laboratory_workbook",
        "draft_environmental_screening_report",
    }
    assert result.structured_content["valid"] is True
    assert result.structured_content["summary"]["above_criterion"] == 1

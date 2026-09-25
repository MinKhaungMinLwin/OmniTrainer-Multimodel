from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EnvironmentalProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    code: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    jurisdiction: str = Field(default="Unspecified", min_length=2, max_length=120)


class EnvironmentalProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    code: str
    jurisdiction: str
    status: str
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime


class EnvironmentalWorkbookRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    filename: str
    content_type: str
    size_bytes: int
    content_hash: str
    status: str
    validation: dict
    created_at: datetime


class EnvironmentalReportCreate(BaseModel):
    workbook_id: str
    question: str = Field(min_length=3, max_length=2000)


class EnvironmentalReportReview(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=3, max_length=2000)
    corrected_markdown: str | None = Field(default=None, max_length=100_000)

    @field_validator("corrected_markdown")
    @classmethod
    def nonempty_correction(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


class EnvironmentalReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    workbook_id: str
    question: str
    report_markdown: str
    sources: list
    status: str
    version: int
    reviewed_by_user_id: str | None
    review_reason: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class EnvironmentalSearchResult(BaseModel):
    query: str
    matches: list[dict]
    abstained: bool

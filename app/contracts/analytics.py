from typing import Any

from pydantic import BaseModel, Field


class AnalyticsRequest(BaseModel):
    question: str = Field(min_length=1)
    thread_id: str | None = None


class ChartSpec(BaseModel):
    chart_type: str
    title: str
    x: str
    y: str
    series: str | None = None


class Provenance(BaseModel):
    request_id: str
    source: str
    generated_sql: str | None
    validated_sql: str | None
    result_fields: list[str]
    row_count: int


class AnalyticsResponse(BaseModel):
    request_id: str
    answer: str
    rows: list[dict[str, Any]]
    provenance: Provenance
    chart: ChartSpec | None = None

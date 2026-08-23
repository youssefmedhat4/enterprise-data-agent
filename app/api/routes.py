from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends

from app.agent.graph import build_graph
from app.config import Settings, get_settings
from app.contracts.analytics import AnalyticsRequest, AnalyticsResponse, ChartSpec, Provenance
from app.data.factory import build_database_gateway
from app.data.gateway import DatabaseGateway
from app.llm.factory import build_llm_gateway
from app.llm.gateway import LLMGateway
from app.security.sql_validation import SQLValidator

router = APIRouter()


def get_database_gateway(settings: Annotated[Settings, Depends(get_settings)]) -> DatabaseGateway:
    return build_database_gateway(settings)


def get_llm_gateway(settings: Annotated[Settings, Depends(get_settings)]) -> LLMGateway:
    return build_llm_gateway(settings)


def get_sql_validator(settings: Annotated[Settings, Depends(get_settings)]) -> SQLValidator:
    return SQLValidator(max_rows=settings.query_row_limit)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/analytics/query")
async def query_analytics(
    request: AnalyticsRequest,
    db_gateway: Annotated[DatabaseGateway, Depends(get_database_gateway)],
    llm_gateway: Annotated[LLMGateway, Depends(get_llm_gateway)],
    sql_validator: Annotated[SQLValidator, Depends(get_sql_validator)],
) -> AnalyticsResponse:
    request_id = str(uuid4())
    graph = build_graph(
        db_gateway=db_gateway,
        llm_gateway=llm_gateway,
        sql_validator=sql_validator,
    )
    try:
        result = await graph.ainvoke(
            {
                "request_id": request_id,
                "trace_id": request_id,
                "thread_id": request.thread_id,
                "question": request.question,
            }
        )
    finally:
        await db_gateway.close()
    return AnalyticsResponse(
        request_id=request_id,
        answer=result["final_answer"],
        rows=result["query_result"],
        provenance=Provenance.model_validate(result["provenance"]),
        chart=ChartSpec.model_validate(result["chart_spec"]) if result.get("chart_spec") else None,
    )

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.config import get_settings
from app.data.factory import build_database_gateway
from app.metrics.cube import CubeMetricGateway, HTTPCubeClient
from app.metrics.factory import build_wren_metric_gateway
from app.metrics.gateway import (
    MetricGateway,
    MetricGatewayError,
    MetricProviderUnavailableError,
    MetricQuery,
    MetricQueryValidationError,
)

DEFAULT_CASES_PATH = Path(__file__).parents[2] / "evals" / "metrics_cases.json"


class MetricEvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    description: str
    query: MetricQuery
    expected_rows: tuple[dict[str, Any], ...] = ()
    expected_row_count: int | None = Field(default=None, ge=0)
    expected_subset_of: tuple[dict[str, Any], ...] = ()
    expected_error: Literal["invalid_metric_query"] | None = None
    numeric_tolerance: float = Field(default=0.001, ge=0)


class MetricCaseOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    passed: bool
    reason: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: float
    retrieval_latency_ms: float | None = None
    execution_latency_ms: float | None = None
    generated_sql: str | None = None


class MetricEvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    case_count: int
    passed: int
    failed: int
    correctness: float
    p50_latency_ms: float
    p95_latency_ms: float
    outcomes: list[MetricCaseOutcome]


def load_metric_cases(path: Path = DEFAULT_CASES_PATH) -> list[MetricEvaluationCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return TypeAdapter(list[MetricEvaluationCase]).validate_python(payload)


async def evaluate_metric_gateway(
    provider: str,
    gateway: MetricGateway,
    cases: list[MetricEvaluationCase],
) -> MetricEvaluationReport:
    outcomes: list[MetricCaseOutcome] = []
    for index, case in enumerate(cases, start=1):
        started_at = perf_counter()
        try:
            result = await gateway.query_metric(case.query)
        except MetricProviderUnavailableError:
            raise
        except MetricQueryValidationError:
            latency_ms = (perf_counter() - started_at) * 1000
            passed = case.expected_error == "invalid_metric_query"
            outcome = MetricCaseOutcome(
                case_id=case.id,
                passed=passed,
                reason="expected_invalid_metric_query" if passed else "unexpected_validation_error",
                latency_ms=round(latency_ms, 3),
            )
        except MetricGatewayError as exc:
            latency_ms = (perf_counter() - started_at) * 1000
            outcome = MetricCaseOutcome(
                case_id=case.id,
                passed=False,
                reason=f"provider_error:{type(exc).__name__}",
                latency_ms=round(latency_ms, 3),
            )
        else:
            latency_ms = (perf_counter() - started_at) * 1000
            if case.expected_error is not None:
                passed, reason = False, "expected_error_but_query_succeeded"
            else:
                passed, reason = compare_metric_rows(case, list(result.rows))
            outcome = MetricCaseOutcome(
                case_id=case.id,
                passed=passed,
                reason=reason,
                rows=[_json_row(row) for row in result.rows],
                latency_ms=round(latency_ms, 3),
                retrieval_latency_ms=result.provenance.metric_retrieval_latency_ms,
                execution_latency_ms=result.provenance.metric_execution_latency_ms,
                generated_sql=result.provenance.generated_sql,
            )
        outcomes.append(outcome)
        status = "passed" if outcome.passed else outcome.reason
        print(f"[{index}/{len(cases)}] {case.id} - {status}")

    latencies = [outcome.latency_ms for outcome in outcomes]
    passed_count = sum(outcome.passed for outcome in outcomes)
    return MetricEvaluationReport(
        provider=provider,
        case_count=len(outcomes),
        passed=passed_count,
        failed=len(outcomes) - passed_count,
        correctness=passed_count / len(outcomes) if outcomes else 0,
        p50_latency_ms=round(statistics.median(latencies), 3) if latencies else 0,
        p95_latency_ms=round(_percentile(latencies, 0.95), 3) if latencies else 0,
        outcomes=outcomes,
    )


def compare_metric_rows(
    case: MetricEvaluationCase,
    actual_rows: list[dict[str, Any]],
) -> tuple[bool, str]:
    if case.expected_row_count is not None and len(actual_rows) != case.expected_row_count:
        return False, f"row_count_mismatch:{len(actual_rows)}"
    if case.expected_subset_of:
        for row in actual_rows:
            if not any(
                _rows_equal(row, expected, case.numeric_tolerance)
                for expected in case.expected_subset_of
            ):
                return False, "row_not_in_expected_subset"
        return True, "semantically_equivalent_subset"
    if len(actual_rows) != len(case.expected_rows):
        return False, f"row_count_mismatch:{len(actual_rows)}"
    unmatched = list(case.expected_rows)
    for row in actual_rows:
        match = next(
            (
                index
                for index, expected in enumerate(unmatched)
                if _rows_equal(row, expected, case.numeric_tolerance)
            ),
            None,
        )
        if match is None:
            return False, "expected_row_not_found"
        unmatched.pop(match)
    return True, "semantically_equivalent"


def _rows_equal(actual: dict[str, Any], expected: dict[str, Any], tolerance: float) -> bool:
    if set(actual) != set(expected):
        return False
    return all(_values_equal(actual[key], expected[key], tolerance) for key in expected)


def _values_equal(actual: Any, expected: Any, tolerance: float) -> bool:
    actual_number = _decimal(actual)
    expected_number = _decimal(expected)
    if actual_number is not None and expected_number is not None:
        return abs(actual_number - expected_number) <= Decimal(str(tolerance))
    return bool(_normalized_value(actual) == _normalized_value(expected))


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _normalized_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value.casefold()
    return value


def _json_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, date | datetime) else value
        for key, value in row.items()
    }


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * quantile)))
    return ordered[index]


async def _run(provider: Literal["wren", "cube"], output: Path) -> None:
    settings = get_settings()
    if provider == "wren" and settings.database_provider != "postgres":
        raise SystemExit("Wren metric evaluation requires DATABASE_PROVIDER=postgres.")
    database = build_database_gateway(settings) if provider == "wren" else None
    # `--provider` is explicit and authoritative here regardless of the
    # configured `METRIC_PROVIDER` default, so both providers can always be
    # benchmarked side by side without editing `.env`.
    gateway: MetricGateway
    if provider == "wren":
        assert database is not None
        gateway = build_wren_metric_gateway(settings, database=database)
    else:
        token = (
            settings.cube_api_token.get_secret_value()
            if settings.cube_api_token is not None
            else None
        )
        gateway = CubeMetricGateway(
            HTTPCubeClient(
                settings.cube_api_url,
                timeout_seconds=settings.cube_timeout_seconds,
                api_token=token,
            )
        )
    try:
        if not await gateway.health_check():
            raise SystemExit(f"Configured metric provider '{provider}' is unavailable.")
        report = await evaluate_metric_gateway(provider, gateway, load_metric_cases())
    finally:
        await gateway.close()
        if database is not None:
            await database.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(f"{report.passed}/{report.case_count} passed; report={output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the deterministic governed metric suite.")
    parser.add_argument("--provider", required=True, choices=("wren", "cube"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(_run(args.provider, args.output))


if __name__ == "__main__":
    main()

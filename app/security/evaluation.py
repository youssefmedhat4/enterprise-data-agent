from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from statistics import median

from pydantic import BaseModel, ConfigDict, TypeAdapter

from app.data.schema_metadata import synthetic_enterprise_metadata
from app.security.sql_validation import SQLValidationCode, SQLValidator


class SQLValidationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    category: str
    sql: str
    expected_valid: bool
    expected_error_code: SQLValidationCode | None = None
    expected_repairable: bool = False


def load_validation_cases(path: Path) -> tuple[SQLValidationCase, ...]:
    return tuple(
        TypeAdapter(list[SQLValidationCase]).validate_json(path.read_text(encoding="utf-8"))
    )


def evaluate_validation_cases(path: Path) -> dict[str, object]:
    cases = load_validation_cases(path)
    validator = SQLValidator()
    outcomes: list[dict[str, object]] = []
    parse_latencies: list[float] = []
    schema_latencies: list[float] = []
    for case in cases:
        result = validator.validate(case.sql, allowed_schema=synthetic_enterprise_metadata())
        passed = (
            result.is_valid == case.expected_valid
            and result.error_code == case.expected_error_code
            and result.repairable == case.expected_repairable
        )
        parse_latencies.append(result.parse_latency_ms)
        schema_latencies.append(result.schema_validation_latency_ms)
        outcomes.append(
            {
                "case_id": case.id,
                "category": case.category,
                "passed": passed,
                "actual_valid": result.is_valid,
                "actual_error_code": result.error_code,
                "actual_repairable": result.repairable,
                "parse_latency_ms": result.parse_latency_ms,
                "schema_validation_latency_ms": result.schema_validation_latency_ms,
            }
        )
    category_counts = Counter(case.category for case in cases)
    return {
        "case_count": len(cases),
        "passed": sum(bool(item["passed"]) for item in outcomes),
        "failed": sum(not bool(item["passed"]) for item in outcomes),
        "category_counts": dict(sorted(category_counts.items())),
        "latency_ms": {
            "parse_average": round(sum(parse_latencies) / len(parse_latencies), 4),
            "parse_median": round(median(parse_latencies), 4),
            "schema_average": round(sum(schema_latencies) / len(schema_latencies), 4),
            "schema_median": round(median(schema_latencies), 4),
        },
        "failed_case_ids": [str(item["case_id"]) for item in outcomes if not item["passed"]],
        "outcomes": outcomes,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate schema-aware SQL validation.")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/sql_validation_cases.json"),
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    report = evaluate_validation_cases(args.dataset)
    rendered = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

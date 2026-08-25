import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.data.gateway import DatabaseResultTooLargeError


def bounded_rows(
    records: Sequence[Mapping[str, Any]],
    *,
    max_result_bytes: int,
) -> tuple[list[dict[str, Any]], int, bool]:
    rows: list[dict[str, Any]] = []
    result_bytes = 2
    for record in records:
        row = dict(record)
        row_bytes = len(
            json.dumps(row, default=str, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        separator_bytes = 1 if rows else 0
        if result_bytes + row_bytes + separator_bytes > max_result_bytes:
            if not rows:
                raise DatabaseResultTooLargeError(
                    "A single database result row exceeds DB_MAX_RESULT_BYTES."
                )
            return rows, result_bytes, True
        rows.append(row)
        result_bytes += row_bytes + separator_bytes
    return rows, result_bytes, False

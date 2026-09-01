"""Running quality checks against the datasource they describe.

Every check goes through the same execution path an analytical query does: the
datasource's own gateway from `DataSourceRuntimeProvider`, the read-only role,
and -- for a custom statement -- SQLGlot and schema authorization. There is no
privileged side channel for health checks, because a side channel is exactly
where an unreviewed statement would eventually run.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.knowledge.quality import (
    AssertionType,
    QualityAssertion,
    QualityCheckResult,
    QualityStatus,
    QualityStore,
    build_check_sql,
    interpret,
)
from app.security.sql_validation import SQLValidator

logger = logging.getLogger(__name__)


class QualityRunner:
    def __init__(
        self,
        *,
        store: QualityStore,
        gateway: Any,
        validator: SQLValidator,
        authorized_tables: list[Any] | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._validator = validator
        self._authorized_tables = authorized_tables or []

    async def run_all(self, data_source_id: UUID) -> list[QualityCheckResult]:
        assertions = await self._store.assertions(data_source_id, enabled_only=True)
        results = [await self.run_one(assertion) for assertion in assertions]
        for result in results:
            await self._store.record(result)
        return results

    async def run_one(self, assertion: QualityAssertion) -> QualityCheckResult:
        try:
            sql, parameters = build_check_sql(assertion)
            if assertion.assertion_type is AssertionType.CUSTOM_SAFE_SQL:
                # A reviewer wrote this one, so it is treated exactly like a
                # generated statement: parsed, checked against the authorized
                # schema, and refused if it is not a read-only select.
                validation = self._validator.validate(
                    sql, allowed_schema=self._authorized_tables
                )
                if not validation.is_valid or validation.validated_sql is None:
                    code = (
                        validation.error_code.value
                        if validation.error_code
                        else "invalid"
                    )
                    return QualityCheckResult(
                        assertion_id=assertion.id,
                        data_source_id=assertion.data_source_id,
                        status=QualityStatus.UNKNOWN,
                        detail=f"The statement did not pass validation ({code}).",
                    )
                sql = validation.validated_sql
            result = await self._gateway.execute_readonly(sql, parameters)
        except Exception as exc:
            # Never the provider's message: it can carry a statement, a schema,
            # or a connection detail.
            logger.info(
                "quality check failed: assertion=%s reason=%s",
                assertion.id,
                type(exc).__name__,
            )
            return QualityCheckResult(
                assertion_id=assertion.id,
                data_source_id=assertion.data_source_id,
                status=QualityStatus.UNKNOWN,
                detail=f"The check could not run ({type(exc).__name__}).",
            )
        return interpret(assertion, _observed(result))


def _observed(result: Any) -> float | None:
    """The single number a check produces, whatever the column is called."""
    rows = getattr(result, "rows", None) or []
    if not rows:
        return None
    first = rows[0]
    value = first.get("observed") if "observed" in first else next(iter(first.values()))
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

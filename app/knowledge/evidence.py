"""Validated SQL kept as evidence for promoting a query example.

An approved query example claims that a particular SQL shape answers a
particular question well. Only a run that actually happened can support that
claim, and the model that wrote the SQL cannot be asked to recall it: prose
about what SQL was used is not evidence of anything.

Question memory is not the place for it. It records why a question is worth
learning from -- route, fingerprint, whether the run succeeded, validated and
grounded -- and deliberately holds no SQL, no rows and no answer text, so that
remembering what people ask can never become a record of what their data looks
like. Widening it to carry every statement the system generates would trade
that for one feature.

So evidence lives here instead: one row per recurring cluster, written only by
a run that succeeded, validated and grounded, replaced when a better run comes
along. Bounded by how many distinct question shapes a datasource has, not by
how much it is used.

What is stored is a statement, never a result. No rows, no totals, no answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ExecutionEvidence:
    """One validated statement that answered one recurring question."""

    data_source_id: UUID
    cluster_id: UUID
    question_text: str
    validated_sql: str
    schema_fingerprint: str | None = None
    recorded_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ExecutionEvidenceStore(Protocol):
    """Storage a recorder and a candidate generator need."""

    async def record(self, evidence: ExecutionEvidence) -> ExecutionEvidence: ...

    async def for_cluster(
        self, data_source_id: UUID, cluster_id: UUID
    ) -> ExecutionEvidence | None: ...


class InMemoryExecutionEvidenceStore(ExecutionEvidenceStore):
    """Development storage. Datasource-scoped like the persistent one."""

    def __init__(self) -> None:
        self._by_cluster: dict[tuple[UUID, UUID], ExecutionEvidence] = {}

    async def record(self, evidence: ExecutionEvidence) -> ExecutionEvidence:
        self._by_cluster[(evidence.data_source_id, evidence.cluster_id)] = evidence
        return evidence

    async def for_cluster(
        self, data_source_id: UUID, cluster_id: UUID
    ) -> ExecutionEvidence | None:
        return self._by_cluster.get((data_source_id, cluster_id))


def qualifies_as_evidence(
    *,
    route: str,
    validated_sql: str | None,
    succeeded: bool,
    validated: bool,
    grounded: bool,
) -> bool:
    """Whether this run may support a future query example.

    Every condition is about the run, not about the question. A statement that
    failed, that was repaired but never succeeded, that answered from the
    governed metric layer rather than from SQL, or whose answer grounding
    rejected, is not evidence that this shape answers anything well.
    """
    return bool(
        route == "adhoc_analytics"
        and validated_sql
        and validated_sql.strip()
        and succeeded
        and validated
        and grounded
    )

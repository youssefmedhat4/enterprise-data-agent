"""PostgreSQL-backed knowledge candidate storage.

Candidate state has to outlive a process. A rejection recorded in memory would
be forgotten on restart and the next matching event would re-propose exactly
what a reviewer just declined, which makes review meaningless.

The structured proposal is stored as JSON in `proposal_payload` and re-validated
through the same Pydantic contracts on read, so a hand-edited row cannot inject
a shape the application would not otherwise accept.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.knowledge.candidates import (
    BusinessRuleProposal,
    CandidateStatus,
    CandidateType,
    KnowledgeCandidate,
    MetricProposal,
    QueryExampleProposal,
)

logger = logging.getLogger(__name__)

_COLUMNS = """
    id, data_source_id, candidate_type, display_name, description, rationale,
    proposal_payload, structural_fingerprint, cluster_id, evidence_count,
    successful_evidence_count, evidence_sql, evidence_schema_fingerprint,
    status, rejection_reason, version, created_at, reviewed_at, reviewed_by,
    promoted_to_type, promoted_to_id
"""

_UPSERT = """
    INSERT INTO knowledge.knowledge_candidates
        (id, data_source_id, candidate_type, display_name, description,
         proposal_payload, structural_fingerprint, cluster_id, evidence_count,
         successful_evidence_count, evidence_sql, evidence_schema_fingerprint,
         status, rejection_reason, version,
         created_at, reviewed_at, reviewed_by, promoted_to_type, promoted_to_id)
    VALUES
        (%(id)s, %(data_source_id)s, %(candidate_type)s, %(display_name)s,
         %(description)s, %(payload)s, %(fingerprint)s, %(cluster_id)s,
         %(evidence_count)s, %(successful_evidence_count)s, %(evidence_sql)s,
         %(evidence_fingerprint)s, %(status)s,
         %(rejection_reason)s, %(version)s, %(created_at)s, %(reviewed_at)s,
         %(reviewed_by)s, %(promoted_to_type)s, %(promoted_to_id)s)
    ON CONFLICT (data_source_id, candidate_type, structural_fingerprint)
    DO UPDATE SET
        display_name = EXCLUDED.display_name,
        description = EXCLUDED.description,
        proposal_payload = EXCLUDED.proposal_payload,
        evidence_count = EXCLUDED.evidence_count,
        successful_evidence_count = EXCLUDED.successful_evidence_count,
        evidence_sql = EXCLUDED.evidence_sql,
        evidence_schema_fingerprint = EXCLUDED.evidence_schema_fingerprint,
        status = EXCLUDED.status,
        rejection_reason = EXCLUDED.rejection_reason,
        version = EXCLUDED.version,
        reviewed_at = EXCLUDED.reviewed_at,
        reviewed_by = EXCLUDED.reviewed_by,
        promoted_to_type = EXCLUDED.promoted_to_type,
        promoted_to_id = EXCLUDED.promoted_to_id,
        updated_at = now()
"""


class PostgresCandidateStore:
    """Datasource-scoped candidate storage in the knowledge database."""

    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    async def upsert(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                _UPSERT,
                {
                    "id": candidate.id,
                    "data_source_id": candidate.data_source_id,
                    "candidate_type": candidate.candidate_type.value,
                    "display_name": candidate.display_name,
                    "description": candidate.description,
                    "payload": Jsonb(
                        json.loads(candidate.proposal.model_dump_json())
                    ),
                    "fingerprint": candidate.structural_fingerprint,
                    "cluster_id": candidate.cluster_id,
                    "evidence_count": candidate.evidence_count,
                    "successful_evidence_count": candidate.successful_evidence_count,
                    "evidence_sql": candidate.evidence_sql,
                    "evidence_fingerprint": candidate.evidence_schema_fingerprint,
                    "status": candidate.status.value,
                    "rejection_reason": candidate.rejection_reason,
                    "version": candidate.version,
                    "created_at": candidate.created_at,
                    "reviewed_at": candidate.reviewed_at,
                    "reviewed_by": candidate.reviewed_by,
                    "promoted_to_type": candidate.promoted_to_type,
                    "promoted_to_id": candidate.promoted_to_id,
                },
            )
        stored = await self.get(
            candidate.data_source_id,
            candidate.candidate_type,
            candidate.structural_fingerprint,
        )
        return stored if stored is not None else candidate

    async def get(
        self,
        data_source_id: UUID,
        candidate_type: CandidateType,
        structural_fingerprint: str,
    ) -> KnowledgeCandidate | None:
        rows = await self._select(
            " AND candidate_type = %(candidate_type)s"
            " AND structural_fingerprint = %(fingerprint)s",
            {
                "data_source_id": data_source_id,
                "candidate_type": candidate_type.value,
                "fingerprint": structural_fingerprint,
            },
        )
        return rows[0] if rows else None

    async def by_id(
        self, data_source_id: UUID, candidate_id: UUID
    ) -> KnowledgeCandidate | None:
        rows = await self._select(
            " AND id = %(candidate_id)s",
            {"data_source_id": data_source_id, "candidate_id": candidate_id},
        )
        return rows[0] if rows else None

    async def list(
        self,
        data_source_id: UUID,
        *,
        status: CandidateStatus | None = None,
    ) -> list[KnowledgeCandidate]:
        clause = "" if status is None else " AND status = %(status)s"
        params: dict[str, Any] = {"data_source_id": data_source_id}
        if status is not None:
            params["status"] = status.value
        return list(await self._select(clause + " ORDER BY created_at DESC", params))

    async def _select(
        self, clause: str, params: dict[str, Any]
    ) -> Sequence[KnowledgeCandidate]:
        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                f"SELECT {_COLUMNS} FROM knowledge.knowledge_candidates"
                " WHERE data_source_id = %(data_source_id)s" + clause,
                params,
            )
            rows = cast("list[dict[str, Any]]", await cursor.fetchall())
        return [candidate for row in rows if (candidate := _to_candidate(row))]


def _to_candidate(row: dict[str, Any]) -> KnowledgeCandidate | None:
    """Rebuild a candidate, re-validating its stored proposal.

    A payload that no longer matches the contract is skipped rather than raising:
    one unreadable row must not make the whole review queue unusable, and the
    row stays on disk for inspection.
    """
    candidate_type = CandidateType(row["candidate_type"])
    payload = row["proposal_payload"] or {}
    proposal: MetricProposal | QueryExampleProposal | BusinessRuleProposal
    try:
        if candidate_type is CandidateType.METRIC:
            proposal = MetricProposal.model_validate(payload)
        elif candidate_type is CandidateType.QUERY_EXAMPLE:
            proposal = QueryExampleProposal.model_validate(payload)
        else:
            proposal = BusinessRuleProposal.model_validate(payload)
    except Exception as exc:
        logger.warning(
            "skipping unreadable candidate: id=%s reason=%s",
            row["id"],
            type(exc).__name__,
        )
        return None
    return KnowledgeCandidate(
        id=row["id"],
        data_source_id=row["data_source_id"],
        candidate_type=candidate_type,
        display_name=row["display_name"],
        description=row["description"],
        structural_fingerprint=row["structural_fingerprint"],
        proposal=proposal,
        cluster_id=row["cluster_id"],
        evidence_count=row["evidence_count"],
        successful_evidence_count=row["successful_evidence_count"],
        evidence_sql=row["evidence_sql"],
        evidence_schema_fingerprint=row["evidence_schema_fingerprint"],
        status=CandidateStatus(row["status"]),
        rejection_reason=row["rejection_reason"],
        version=row["version"],
        created_at=row["created_at"],
        reviewed_at=row["reviewed_at"],
        reviewed_by=row["reviewed_by"],
        promoted_to_type=row["promoted_to_type"],
        promoted_to_id=row["promoted_to_id"],
    )

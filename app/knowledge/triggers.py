"""Automatic enqueueing of candidate generation.

The bridge between "a cluster just crossed its threshold" and "a proposal
exists", without either an autonomous loop or a model call on the request path.

Recording an event enqueues *a job* when the cluster is newly eligible. That is
a single INSERT guarded by a partial unique index, so it costs nothing
meaningful per request and cannot enqueue twice. Generation itself runs later,
when a worker claims the job.

Eligibility is deterministic: configured thresholds over counts the database
already maintains. No model is consulted to decide whether to consult a model.
"""

from __future__ import annotations

import logging
from uuid import UUID

from app.config import Settings
from app.knowledge.candidates import CandidateType
from app.knowledge.jobs import PostgresGenerationJobQueue
from app.knowledge.memory import QuestionCluster

logger = logging.getLogger(__name__)


class CandidateTrigger:
    """Decides whether a cluster warrants queuing generation."""

    def __init__(
        self,
        *,
        settings: Settings,
        jobs: PostgresGenerationJobQueue,
        candidates: object,
    ) -> None:
        self._settings = settings
        self._jobs = jobs
        self._candidates = candidates

    async def consider(
        self, *, data_source_id: UUID, cluster: QuestionCluster
    ) -> bool:
        """Queue generation for `cluster` if it has just become eligible.

        Returns whether a job was created. Never raises into the caller: this
        runs after a request already succeeded, and failing to queue learning
        work must not turn a good answer into an error.
        """
        try:
            if not self._is_eligible(cluster):
                return False
            if await self._already_represented(data_source_id, cluster):
                return False
            job = await self._jobs.enqueue(
                data_source_id=data_source_id, cluster_id=cluster.id
            )
            if job is not None:
                logger.info(
                    "generation queued: data_source=%s cluster=%s job=%s",
                    data_source_id,
                    cluster.id,
                    job.id,
                )
            return job is not None
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "candidate trigger failed: data_source=%s reason=%s",
                data_source_id,
                type(exc).__name__,
            )
            return False

    def _is_eligible(self, cluster: QuestionCluster) -> bool:
        return cluster.is_eligible_for_proposal(
            min_occurrences=self._settings.question_cluster_min_occurrences,
            min_successful=self._settings.question_cluster_min_successful,
        )

    async def _already_represented(
        self, data_source_id: UUID, cluster: QuestionCluster
    ) -> bool:
        """Whether this pattern already has a candidate of any status.

        A REJECTED candidate counts. Re-proposing what a reviewer declined from
        the same evidence would make review meaningless, so suppression is part
        of eligibility rather than something generation discovers later.
        """
        for candidate_type in CandidateType:
            existing = await self._candidates.get(  # type: ignore[attr-defined]
                data_source_id, candidate_type, cluster.structural_fingerprint
            )
            if existing is not None:
                return True
        return False

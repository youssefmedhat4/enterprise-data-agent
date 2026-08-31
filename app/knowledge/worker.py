"""Background worker for knowledge generation jobs.

Closes the last manual gap in the learning loop: a cluster that crosses its
threshold already enqueues a job, and this drains that queue so a proposal
appears without anyone invoking anything.

Deliberately an asyncio task rather than a queue service. The workload is one
job per cluster crossing a threshold — rare, small, and already serialised by
the database — so a broker would add an operational dependency far larger than
the need.

What this does **not** do is certify anything. It generates PROPOSED candidates
and stops. Approval stays human: an automatic path from "asked often" to
"certified metric" would let a recurring misunderstanding become governed truth
with nobody having agreed to it.

Multi-worker safety is the database's, not this loop's: `claim_next` uses
`FOR UPDATE SKIP LOCKED`, so running several instances is safe by construction
rather than by coordination here.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from app.config import Settings
from app.knowledge.candidates import CandidateGenerator
from app.knowledge.jobs import GenerationJob, PostgresGenerationJobQueue
from app.knowledge.memory import QuestionCluster
from app.llm.gateway import LLMGatewayError, LLMRateLimitError

logger = logging.getLogger(__name__)

#: How many jobs one wake-up may drain before yielding. Bounded so a backlog
#: cannot monopolise the loop or the model quota in a single tick.
MAX_JOBS_PER_TICK = 3


class KnowledgeJobWorker:
    """Polls for generation jobs and turns eligible clusters into proposals."""

    def __init__(
        self,
        *,
        settings: Settings,
        jobs: PostgresGenerationJobQueue,
        generator: CandidateGenerator,
        memory: object,
        poll_seconds: float | None = None,
    ) -> None:
        self._settings = settings
        self._jobs = jobs
        self._generator = generator
        self._memory = memory
        self._poll_seconds = poll_seconds or settings.knowledge_worker_poll_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="knowledge-job-worker")
        logger.info(
            "knowledge worker started: poll_seconds=%s", self._poll_seconds
        )

    async def stop(self) -> None:
        """Ask the loop to finish, then wait briefly for it.

        A job already claimed is left RUNNING rather than force-failed; its
        attempt is recorded, and another worker may retry it within the bounded
        attempt limit. Marking it failed on shutdown would spend an attempt on
        an operator action rather than on a real problem.
        """
        if self._task is None:
            return
        self._stopping.set()
        task, self._task = self._task, None
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except (TimeoutError, asyncio.CancelledError):
            pass
        except Exception as exc:  # pragma: no cover - shutdown best effort
            logger.warning("knowledge worker stop: %s", type(exc).__name__)
        logger.info("knowledge worker stopped")

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.drain_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                # The loop must outlive one bad tick; a crash here would
                # silently stop all future learning.
                logger.warning(
                    "knowledge worker tick failed: %s", type(exc).__name__
                )
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._poll_seconds
                )
            except TimeoutError:
                continue

    async def drain_once(self) -> int:
        """Process up to `MAX_JOBS_PER_TICK` claimable jobs. Returns how many ran.

        Exposed so a test can drive one tick deterministically instead of
        waiting on wall-clock polling.
        """
        processed = 0
        for _ in range(MAX_JOBS_PER_TICK):
            job = await self._jobs.claim_next()
            if job is None:
                break
            await self._process(job)
            processed += 1
        return processed

    async def _process(self, job: GenerationJob) -> None:
        cluster = await self._cluster_for(job)
        if cluster is None:
            # The cluster is gone. Nothing to propose and nothing to retry.
            await self._jobs.complete(job.id)
            return
        try:
            candidate = await self._generator.propose_for_cluster(
                data_source_id=job.data_source_id,
                cluster=cluster,
                example_questions=await self._examples_for(job, cluster),
            )
        except LLMRateLimitError:
            # Transient and expected when quota is exhausted. Release for a
            # bounded retry rather than burning the job.
            await self._jobs.release_for_retry(
                job.id, error_code="llm_rate_limited"
            )
            logger.info("generation deferred: job=%s reason=rate_limited", job.id)
            return
        except LLMGatewayError as exc:
            await self._jobs.release_for_retry(
                job.id, error_code=_error_code(exc)
            )
            logger.info(
                "generation deferred: job=%s reason=%s", job.id, _error_code(exc)
            )
            return
        except Exception as exc:  # pragma: no cover - defensive
            await self._jobs.fail(job.id, error_code="generation_failed")
            logger.warning(
                "generation failed: job=%s reason=%s", job.id, type(exc).__name__
            )
            return

        await self._jobs.complete(job.id)
        logger.info(
            "generation complete: job=%s proposed=%s",
            job.id,
            candidate is not None,
        )

    async def _cluster_for(self, job: GenerationJob) -> QuestionCluster | None:
        clusters = await self._memory.clusters(job.data_source_id)  # type: ignore[attr-defined]
        return next(
            (cluster for cluster in clusters if cluster.id == job.cluster_id), None
        )

    async def _examples_for(
        self, job: GenerationJob, cluster: QuestionCluster
    ) -> list[str]:
        events = await self._memory.events_for_cluster(  # type: ignore[attr-defined]
            job.data_source_id, cluster.id, limit=5
        )
        return [event.question_text for event in events]


def _error_code(exc: Exception) -> str:
    """A short token for the jobs table. Never a provider message."""
    return type(exc).__name__.removesuffix("Error").lower() or "llm_unavailable"


async def build_worker(
    settings: Settings,
    runtime: object,
    *,
    data_source_id: UUID | None = None,
) -> KnowledgeJobWorker | None:
    """A worker for this process, or None when it should not run.

    Returns None unless the knowledge layer is persistent and the worker is
    enabled: an in-memory job queue would neither survive a restart nor
    coordinate between workers, so running a loop against one would create the
    appearance of automation without the guarantees.
    """
    del data_source_id
    jobs = getattr(runtime, "jobs", None)
    if not settings.knowledge_worker_enabled or jobs is None:
        return None
    from app.knowledge.candidates import CandidateGenerator
    from app.llm.factory import build_llm_gateway
    from app.llm.profiles import DEFAULT_MODEL_PROFILE

    generator = CandidateGenerator(
        llm=build_llm_gateway(settings, model_profile=DEFAULT_MODEL_PROFILE),
        store=runtime.candidates,  # type: ignore[attr-defined]
        registry=runtime.registry,  # type: ignore[attr-defined]
        # Without this a query example can be proposed and never approved,
        # because approval needs the statement a run actually validated.
        evidence=getattr(runtime, "evidence", None),
    )
    return KnowledgeJobWorker(
        settings=settings,
        jobs=jobs,
        generator=generator,
        memory=runtime.memory,  # type: ignore[attr-defined]
    )

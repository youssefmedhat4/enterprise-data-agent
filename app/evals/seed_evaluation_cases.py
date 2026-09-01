"""Seed a local instance with the reference questions.

A development and operations tool, run by hand:

    python -m app.evals.seed_evaluation_cases <data-source-id> [--legacy]

Nothing here is consulted while answering a question. These values are the ones
independently verified against each fixture, written down once so a developer
does not have to retype them; a real deployment builds its evaluation set from
its own answers, confirmed by the people who know what they should be.

Seeding is idempotent by case name.
"""

from __future__ import annotations

import argparse
import asyncio
import selectors
import sys
from dataclasses import replace as dataclass_replace
from decimal import Decimal
from uuid import UUID

from app.config import get_settings
from app.knowledge.evaluation import (
    EvaluationCase,
    ExpectationKind,
)
from app.knowledge.runtime import build_knowledge_runtime

#: Verified against the demo warehouse through the governed metric path.
DEFAULT_CASES: tuple[tuple[str, str, str], ...] = (
    (
        "Annual payroll commitment",
        "How much are we committed to paying employees annually?",
        "1565000",
    ),
)

#: Verified against the Legacy ERP fixture by independent reference SQL. Each
#: one has a wrong answer a plausible query produces, which is what makes it
#: worth keeping: 15,595,000 for payroll, 60 for headcount, 1,283,400 for
#: revenue, 1,439,250 for cost.
LEGACY_CASES: tuple[tuple[str, str, str], ...] = (
    ("Active headcount", "How many active employees do we have?", "42"),
    ("Current annual payroll", "What is our current annual payroll?", "6345000"),
    ("Invoiced revenue", "What is total invoiced revenue?", "839700"),
    ("Posted project cost", "What are total project costs?", "1042500"),
)


async def seed(data_source_id: UUID, *, legacy: bool) -> int:
    settings = get_settings()
    runtime = await build_knowledge_runtime(settings)
    try:
        store = runtime.evaluations
        if store is None:
            print("This deployment has no persistent evaluation storage.")
            return 1
        existing = {
            case.name: case
            for case in await store.cases(data_source_id, include_archived=True)
        }
        written = 0
        for name, question, expected in LEGACY_CASES if legacy else DEFAULT_CASES:
            previous = existing.get(name)
            case = EvaluationCase(
                data_source_id=data_source_id,
                name=name,
                question=question,
                expectation=ExpectationKind.SCALAR,
                expected={"value": expected},
                tolerance=Decimal(0),
                created_by="seed",
            )
            if previous is not None:
                # Re-seeding updates the case a reviewer may already have run
                # against, rather than creating a second one with the same name.
                case = dataclass_replace(case, id=previous.id)
            await store.upsert_case(case)
            written += 1
            print(f"  {name}: expects {expected}")
        print(f"{written} evaluation case(s) written for {data_source_id}.")
        return 0
    finally:
        await runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_source_id", type=UUID)
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Seed the Legacy ERP reference questions instead of the demo ones.",
    )
    args = parser.parse_args()
    if sys.platform == "win32":
        with asyncio.Runner(
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        ) as runner:
            return runner.run(seed(args.data_source_id, legacy=args.legacy))
    return asyncio.run(seed(args.data_source_id, legacy=args.legacy))


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())

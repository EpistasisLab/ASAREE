"""Promote every completed cell's Score-stage metrics into metric_values.

Runs services.metric_promotion.promote_experiment_score_metrics for one
experiment. Needs agentic_core configured first (its own DB engine is set up
by asaree.app's lifespan when the server runs; a bare script has to do the
same thing by hand -- see agentic_core.config.configure's own docstring on
why this must happen before anything else in agentic_core runs).

Usage (inside the asaree-app container, so DATABASE_URL/etc. are the real
ones -- see compose.yml):

    docker exec asaree-app python scripts/promote_score_metrics.py <experiment_id>
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from agentic_core.config import configure

import asaree.models.dataset  # noqa: F401 -- registers registered_datasets for research_experiments' FK
import asaree.models.experiment  # noqa: F401 -- registers research_experiments for factorial_cell_results' FK
from asaree.config import get_settings
from asaree.models.database import get_session
from asaree.services.metric_promotion import promote_experiment_score_metrics


async def main(experiment_id: uuid.UUID) -> int:
    configure(get_settings())
    async with get_session() as db:
        results = await promote_experiment_score_metrics(db, experiment_id=experiment_id)

    promoted = [r for r in results if r.promoted]
    skipped = [r for r in results if not r.promoted]
    for r in promoted:
        print(f"promoted: {r.cell_label}")
    # "no ProtocolRun for this cell yet" covers every not-yet-run cell in the
    # design -- routine, not worth a line per cell; every other skip reason
    # (failed run, no test_metrics, etc.) is printed since it's actionable.
    for r in skipped:
        if r.reason != "no ProtocolRun for this cell yet":
            print(f"skipped: {r.cell_label}: {r.reason}")
    print(f"\n{len(promoted)} promoted, {len(skipped)} skipped, {len(results)} total cells")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <experiment_id>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(uuid.UUID(sys.argv[1]))))

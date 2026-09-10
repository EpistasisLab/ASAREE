"""Clear the stuck ``AgentAction`` output contract off protocol-owned agents.

An earlier conversation design injected a per-run "action contract" into the
canvas node's config, which ``_sync_durable_agent`` then wrote onto the durable
Motoro agent row ``protocol-{protocol_id}-{node_id}``. ``update_agent`` reads
``None`` as "leave unchanged" and a normal agent node passes
``output_contract=None``, so that contract is never cleared: every later
ordinary run of that node pays for a junk extraction LLM call forever.

The current design writes no such thing (see the rule on ``_sync_durable_agent``
in ``services/protocol_execution.py``), so this is a one-off repair of rows
contaminated before the fix. It is a direct UPDATE because ``update_agent``
offers no way to set a field back to NULL.

Only ``output_contract`` is stuck. The system prompt was contaminated the same
way but is passed non-``None`` on every run, so it self-heals the next time the
node runs.

Usage (inside the asaree-app container, so DATABASE_URL is the real one).
``scripts/`` is baked into the image rather than bind-mounted, so a copy is
needed until the image is rebuilt:

    docker cp scripts/repair_contaminated_agents.py asaree-app:/app/scripts/
    docker exec asaree-app python scripts/repair_contaminated_agents.py          # dry run
    docker exec asaree-app python scripts/repair_contaminated_agents.py --apply
"""

from __future__ import annotations

import asyncio
import sys

import motoro.models.run  # noqa: F401 -- registers AgentRun, which Agent's relationship() names
from motoro.config import configure
from motoro.models.agent import Agent
from motoro.models.database import system_session
from sqlalchemy import select, update

from asaree.config import get_settings

# The contract the old design injected. Matched by name rather than by whole
# value so a row whose schema drifted is still repaired, and so a legitimate
# user-authored contract on a protocol agent is left alone.
CONTAMINANT = "AgentAction"


async def main(*, apply: bool) -> int:
    configure(get_settings())
    async with system_session(reason="one-off repair: clear injected AgentAction contract") as db:
        rows = (
            await db.execute(
                select(Agent.id, Agent.name, Agent.output_contract)
                .where(Agent.name.like("protocol-%"))
                .where(Agent.output_contract.is_not(None))
            )
        ).all()
        contaminated = [
            (agent_id, name)
            for agent_id, name, contract in rows
            if isinstance(contract, dict) and contract.get("name") == CONTAMINANT
        ]
        for _, name in contaminated:
            print(f"contaminated: {name}")
        if not contaminated:
            print("nothing to repair")
            return 0
        if not apply:
            print(f"\n{len(contaminated)} agent(s) would be cleared; re-run with --apply")
            return 0
        await db.execute(
            update(Agent).where(Agent.id.in_([agent_id for agent_id, _ in contaminated])).values(output_contract=None)
        )
    print(f"\ncleared output_contract on {len(contaminated)} agent(s)")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args not in ([], ["--apply"]):
        print(f"usage: {sys.argv[0]} [--apply]", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(apply=args == ["--apply"])))

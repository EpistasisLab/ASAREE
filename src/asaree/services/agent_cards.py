"""The AgentCard: what one Agent node looks like to another.

An AgentCard answers "who is this agent and what is it for", in a form another
agent can read and act on. A2A defines it as the capability-discovery document;
here it plays the same role, minus the network -- see
``local_files/agent-communication/01-agent-card.md`` and ``06-a2a-alignment.md``.

**It is derived, never stored.** There is no card table, no card column and no
card cache. A card is a pure projection of an Agent node plus its connectors,
computed at run start by the same pass that already computes ``model_config`` /
``tool_config`` / ``skill_config`` / ``pattern_config`` from the graph. That is
precisely what makes it dynamic: wiring a Skill node takes effect on the next run
today with nothing to invalidate, and the card inherits that for free. If you
find yourself adding a place to persist one, the staleness problem comes back.

This module owns the card's *shape* only. The graph reading that feeds it lives
in :mod:`asaree.services.protocol_execution`, next to every other ``_resolve_*``
-- which is also what keeps the import one-directional.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentSkill:
    """One thing this agent can do. A2A's ``AgentCard.skills`` entry.

    ``id`` is a slug of the name rather than the registered skill's UUID: it
    only has to be unique within one card, and the reader is a language model
    for which a UUID is noise. Nothing dereferences it -- a peer cannot load
    another agent's skill.
    """

    id: str
    name: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "description": self.description}


@dataclass(frozen=True)
class AgentCard:
    """A2A-shaped capability descriptor for one Agent node.

    ``agent_id`` is the **canvas node id** -- never the Motoro ``Agent.id``, and
    never the label. The node id is what graph connectivity authorizes against,
    what the transcript stores, and what stays stable across re-syncs of the
    durable Motoro agent; the Motoro agent's UUID is an implementation detail of
    one execution and must not leak into the card.

    Deliberately absent: tools and MCP servers (a peer that knows which servers
    another agent can reach will ask it to proxy a call -- out of scope, and a
    permission hole), the system prompt (user-authored, long, and mostly
    meaningless to another agent), and endpoint/auth/security schemes (A2A
    carries those because its peers are remote and untrusted; in-process peers
    in one graph owned by one user have no endpoint to publish).
    """

    agent_id: str
    name: str
    description: str
    skills: list[AgentSkill] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """The wire shape Motoro's ``available_agents`` consumes.

        Plain JSON-able dicts rather than the dataclass itself, because this
        crosses into core -- which must not learn an ASAREE type -- and is
        snapshotted into ``RunContext`` for resume.
        """
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "skills": [s.to_dict() for s in self.skills],
            "metadata": dict(self.metadata),
        }


def _skill_slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.strip().lower()).strip("-") or "skill"


def build_agent_card(
    *,
    node_id: str,
    label: str | None,
    description: str,
    goal: str,
    skills: list[dict[str, Any]],
    model: str | None,
    metadata: dict[str, Any] | None = None,
) -> AgentCard:
    """Assemble a card from values the caller has already resolved.

    Pure on purpose: every input here is something ``_run_agent_node`` reads out
    of the graph anyway, so the card cannot disagree with the agent that
    actually runs.

    *description* falls back to *goal* and then to a generated default, because
    the description is the single highest-leverage field for making a peer's
    model choose well -- it becomes the consultation function's description in
    ``motoro.engine.agent_channel``. An empty one is worse than a generic one.
    """
    name = (label or "").strip() or node_id
    text = (description or "").strip() or (goal or "").strip() or f"An agent named {name}."
    card_metadata: dict[str, Any] = dict(metadata or {})
    if model:
        card_metadata["model"] = model
    return AgentCard(
        agent_id=node_id,
        name=name,
        description=text,
        skills=[
            AgentSkill(
                id=_skill_slug(str(s.get("name") or "")),
                name=str(s.get("name") or ""),
                description=str(s.get("description") or ""),
            )
            for s in skills
            if s.get("name")
        ],
        metadata=card_metadata,
    )


__all__ = ["AgentCard", "AgentSkill", "build_agent_card"]

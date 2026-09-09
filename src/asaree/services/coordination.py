"""Reading an experiment's declared coordination strategy off its design_spec.

Its own module, rather than living beside the validation in
``services.protocol_execution``, because ``services.design_generation`` needs
the same fact -- a strategy change supersedes a design revision, since cells
scored under two different execution semantics don't belong in one results
table -- and ``protocol_execution`` already imports ``design_generation``, so
reading it from there would be a cycle.

Deliberately just the one shared fact. Which strategies exist, what each
requires of a canvas, and how a run executes all stay with the executor that
enforces them.
"""

from __future__ import annotations

from typing import Any

#: What an absent declaration means. Every experiment created before
#: ``coordination_strategy`` existed has no entry in its design_spec, and its
#: cells were run by the plain pipeline walk -- which is what "sequential"
#: names. So absent and explicitly-sequential must always compare equal, or a
#: regenerate on an untouched legacy experiment would look like a change.
DEFAULT_COORDINATION_STRATEGY = "sequential"


def coordination_strategy_slug(design_spec: dict[str, Any] | None) -> str:
    slug = ((design_spec or {}).get("coordination_strategy") or {}).get("slug")
    return str(slug or DEFAULT_COORDINATION_STRATEGY)

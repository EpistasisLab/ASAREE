"""The reference syntax a prompt uses to name what it receives.

A pure string layer: parsing and substitution, no graph and no run state. Graph
semantics -- does the referenced node exist, does it run before this one, what
did it output -- live in :mod:`asaree.services.protocol_execution`, which is
where the graph lives. Keeping the split means this module can be tested
exhaustively against text alone, and the interesting graph rules are not buried
under regex plumbing.

Why references exist at all: on the current prompt contract nothing reaches an
agent's prompt implicitly. An edge grants *availability* -- the upstream output
is retained and in scope -- and a reference in the prompt grants *use*. The
platform therefore never inserts text an experimenter did not ask for, which is
the property a controlled treatment needs (see
``local_files/agent-handoff/02-variable-references.md``).

The forms, all of them:

``{{node:<id>}}``
    That node's ``output_text``, fenced -- followed by its extracted fields, if
    an Output Parser is wired to it.
``{{node:<id>.<field>}}``
    One extracted field, bare and unfenced: ``4300``, not a quoted JSON
    fragment. Only legal when the referenced node has an Output Parser
    declaring that field, which is checked at design time -- a typo'd field
    name is a wiring mistake, not an empty resolution.
``{{previous}}``
    Every direct main-edge predecessor's output, fenced. Survives rewiring,
    which a hardcoded id does not, so it is the right default for "just give me
    the last step."
``{{audience}}``, ``{{upstream_instructions}}``
    Platform-composed sentences (who receives this agent's output; how to treat
    a referenced output). Opt-in tokens rather than automatic text, because on
    an experiment platform prose nobody chose is a confound in the treatment.

Any of them accepts a ``|raw`` suffix to skip the fence: ``{{previous|raw}}``.
Delimiter *neutralization* inside the payload is not optional either way -- that
is what stops a referenced output forging its way out of Motoro's outer
``<<<USER_DATA>>>`` fence.

Node ids are stored, labels are only ever displayed. Rename a node and a stored
reference still resolves; store the label instead and a rename breaks every
prompt pointing at it. So ``{{ Data Profiler }}`` is display form, produced by
the inspector's picker, and never what sits in ``config.prompt``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

#: The bare tokens, and the prefix the node form uses. ``node:`` is spelled out
#: rather than left implicit (``{{<id>}}``) so an id can never be confused with
#: a token name, and so an unrecognized ``{{...}}`` is unambiguously not a
#: reference.
NODE_PREFIX = "node:"
PREVIOUS = "previous"
AUDIENCE = "audience"
UPSTREAM_INSTRUCTIONS = "upstream_instructions"

_BARE_TOKENS = (PREVIOUS, AUDIENCE, UPSTREAM_INSTRUCTIONS)

#: Ids as the canvas mints them (``node-msza682j-w2vslmwn``) and as older graphs
#: and tests spell them (``dndnode_3``, ``a``).
_ID = r"[A-Za-z0-9_-]+"

#: An Output Parser field name. Narrower than ``_ID`` on purpose: it becomes an
#: attribute on the Pydantic model Motoro builds from the contract, so it has to
#: be an identifier. Excluding ``-`` also keeps the split unambiguous -- ``.``
#: is the only thing that can separate an id from a field.
_FIELD = r"[A-Za-z_][A-Za-z0-9_]*"

#: Whitespace-tolerant and case-insensitive on the token name. Both matter for
#: the notebook/SDK path, where prompts are hand-authored with no picker: a
#: mis-cased ``{{Previous}}`` silently surviving as literal text is exactly the
#: quiet failure this design exists to avoid.
_REFERENCE_RE = re.compile(
    r"\{\{\s*(?P<target>"
    + NODE_PREFIX
    + _ID
    + r"(?:\."
    + _FIELD
    + r")?"
    + r"|"
    + "|".join(_BARE_TOKENS)
    + r")\s*(?:\|\s*(?P<modifier>raw)\s*)?\}\}",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PromptReference:
    """One occurrence of one reference in a prompt."""

    token: str
    """The matched text exactly as authored, so substitution can replace it
    without re-deriving the spelling it was written with."""

    kind: str
    """``"node"`` or one of the bare token names."""

    node_id: str
    """The referenced node, or ``""`` for every kind except ``"node"``."""

    raw: bool
    """``|raw`` was present: substitute without the fence. Neutralization of
    delimiters inside the payload still applies."""

    field: str = ""
    """The Output Parser field named after the id, or ``""`` for a whole-node
    reference. Kind stays ``"node"`` either way -- a field reference is still a
    reference to that node, so everything asking "which senders does this
    prompt name" keeps working without knowing fields exist."""


def _reference_from(match: re.Match[str]) -> PromptReference:
    target = match.group("target")
    lowered = target.lower()
    if lowered.startswith(NODE_PREFIX):
        # Only the *token name* is case-insensitive. The id keeps its authored
        # case, because node ids are case-sensitive identifiers and lowercasing
        # one would turn a valid reference into a missing node. Same for the
        # field, which is an attribute name on the extracted model.
        rest = target[len(NODE_PREFIX) :]
        node_id, _, field = rest.partition(".")
        return PromptReference(match.group(0), "node", node_id, bool(match.group("modifier")), field)
    return PromptReference(match.group(0), lowered, "", bool(match.group("modifier")))


def iter_references(text: str) -> Iterator[PromptReference]:
    """Every reference occurrence, in document order, duplicates included."""
    for match in _REFERENCE_RE.finditer(text or ""):
        yield _reference_from(match)


def has_references(text: str) -> bool:
    return _REFERENCE_RE.search(text or "") is not None


def referenced_node_ids(text: str) -> list[str]:
    """The distinct ids the text points at, in first-appearance order.

    Order is stable so a validation error lists them the way the prompt reads,
    and distinct so referencing one node twice does not report it twice.
    """
    seen: list[str] = []
    for ref in iter_references(text):
        if ref.kind == "node" and ref.node_id not in seen:
            seen.append(ref.node_id)
    return seen


def referenced_node_fields(text: str) -> dict[str, list[str]]:
    """``{node_id: [field, ...]}`` for the field references only, distinct and
    in first-appearance order.

    A node referenced only as a whole does not appear here -- the caller asking
    this question wants the fields to check against a declared contract, and an
    empty list would read as "declares no fields" rather than "asked for none".
    """
    fields: dict[str, list[str]] = {}
    for ref in iter_references(text):
        if ref.kind == "node" and ref.field:
            named = fields.setdefault(ref.node_id, [])
            if ref.field not in named:
                named.append(ref.field)
    return fields


def uses(text: str, kind: str) -> bool:
    """Whether *text* contains at least one reference of *kind* -- the question
    the audience and framing tokens are asked, where the id is irrelevant."""
    return any(ref.kind == kind for ref in iter_references(text))


def substitute(text: str, render: Callable[[PromptReference], str]) -> str:
    """Replace every reference with what *render* returns for it.

    *render* is handed the parsed reference and returns finished text; it is
    where fencing and graph lookup happen, so this function stays free of both.
    Returning ``""`` is how "this resolved to nothing" is expressed -- an empty
    resolution is recorded by the caller, never turned into an error here, since
    an agent that correctly produced nothing is a legitimate result and failing
    would discard valid experimental data.

    Anything else in double braces is left exactly as written. That is
    deliberate: a prompt telling an agent to emit a Handlebars or Jinja template
    must survive this pass unharmed, so only the documented forms above are
    recognized and no attempt is made to guess at near-misses.
    """
    return _REFERENCE_RE.sub(lambda m: render(_reference_from(m)), text or "")


def serialize_node_reference(node_id: str, *, field: str = "", raw: bool = False) -> str:
    """The stored form for a node reference. The picker's output, and the one
    place the spelling is defined -- callers must not build it by hand."""
    suffix = f".{field}" if field else ""
    return f"{{{{{NODE_PREFIX}{node_id}{suffix}{'|raw' if raw else ''}}}}}"

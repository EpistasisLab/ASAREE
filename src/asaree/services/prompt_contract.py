"""Which version of the agent prompt format an experiment's runs use.

The prompt is an *input* to a run, as much as the model name or the temperature
is, and until this existed it was the only input not pinned by anything. A
design revision pins the factors; a protocol revision pins the graph; the text
ASAREE wraps around a node's own prompt was whatever the code happened to say
on the day the run executed. So an obviously-correct improvement to that text --
naming the upstream agent instead of printing its raw canvas node id, say --
would silently change the numbers of every already-published experiment that
was rerun afterwards. That is not a refactor, it's a data-integrity bug.

So the format is versioned and the version is stored on the experiment:

* **Absent means v1**, and v1 is frozen. Every experiment that existed before
  this module -- the published spinal pipeline included -- is v1, permanently,
  and ``tests/test_spinal_compat.py`` asserts its prompt byte-for-byte.
* **v2** is stamped on newly created experiments and is where improvements go.

The version is resolved once per run and threaded down, rather than re-read
from ``design_spec`` at each place that builds a prompt, so a mid-run edit to
the experiment cannot produce a run that used two formats.

Lives in its own module (rather than in ``protocol_execution``, which owns the
builders) so that the API layer can stamp the default on creation without
importing the executor.
"""

from __future__ import annotations

from typing import Any

#: What an experiment with no recorded version is. Never change this: it is the
#: contract every pre-versioning experiment was run under.
DEFAULT_PROMPT_CONTRACT_VERSION = 1

#: What a newly created experiment gets. Bump when a new version ships.
LATEST_PROMPT_CONTRACT_VERSION = 2


def prompt_contract_version(design_spec: dict[str, Any] | None) -> int:
    """The prompt format version *design_spec* declares, defaulting to v1.

    Anything unparseable resolves to v1 rather than raising or to the latest:
    a corrupted value must not silently reformat a published experiment's
    prompts, and refusing to run at all would be a worse failure than running
    the format the experiment has always used.
    """
    raw = (design_spec or {}).get("prompt_contract_version")
    try:
        version = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_PROMPT_CONTRACT_VERSION
    if version < DEFAULT_PROMPT_CONTRACT_VERSION:
        return DEFAULT_PROMPT_CONTRACT_VERSION
    return version

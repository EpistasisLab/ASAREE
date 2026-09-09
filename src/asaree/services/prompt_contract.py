"""Which version of the agent prompt format an experiment's runs use.

The prompt is an *input* to a run, as much as the model name or the temperature
is, and until this existed it was the only input not pinned by anything. A
design revision pins the factors; a protocol revision pins the graph; the text
ASAREE wraps around a node's own prompt was whatever the code happened to say
on the day the run executed. So an obviously-correct improvement to that text --
naming the upstream agent instead of printing its raw canvas node id, say --
would silently change the numbers of every already-published experiment that
was rerun afterwards. That is not a refactor, it's a data-integrity bug.

**But only published results need that protection, and there is exactly one.**
The handoff format is still being designed; freezing every intermediate shape
would make each improvement pay for a guarantee nobody is relying on. So there
are two contracts, not a version ladder:

* :data:`LEGACY_PROMPT_CONTRACT` -- frozen forever, and **absence means this**.
  Every experiment predating this module is legacy, the published spinal
  pipeline included, and ``tests/test_spinal_compat.py`` asserts its assembled
  prompt byte-for-byte. This is the one that exists to keep a paper
  reproducible; it has no other job and takes no improvements.
* :data:`CURRENT_PROMPT_CONTRACT` -- everything else. It is where the handoff
  design happens, and it is **expected to change** while the feature is being
  built. Its golden is a change-detector so a diff shows up in review, not a
  reproducibility promise.

The stored value stays an integer (``design_spec["prompt_contract_version"]``)
because real rows already carry it and the frontend reads it.

**At release, freeze the current contract into a real numbered version and
restore the ladder.** The dispatch structure below is deliberately left intact
so that is a small change rather than a rewrite -- the discipline is postponed
because there are no users yet, not discarded.

The contract is resolved once per run and threaded down, rather than re-read
from ``design_spec`` at each place that builds a prompt, so a mid-run edit to
the experiment cannot produce a run that used two formats.

Lives in its own module (rather than in ``protocol_execution``, which owns the
builders) so that the API layer can stamp the default on creation without
importing the executor.
"""

from __future__ import annotations

from typing import Any

#: The frozen contract, and what an experiment with no recorded version is.
#: Never change this or the format it selects: it is what every pre-versioning
#: experiment -- the published spinal pipeline included -- was run under.
LEGACY_PROMPT_CONTRACT = 1

#: What a newly created experiment gets: the format currently being designed.
#: Expected to evolve in place while the handoff feature is built; give it a
#: number of its own only when something published depends on it.
CURRENT_PROMPT_CONTRACT = 2


def prompt_contract_version(design_spec: dict[str, Any] | None) -> int:
    """The prompt format *design_spec* declares, defaulting to the legacy one.

    Anything unparseable resolves to legacy rather than raising or to the
    current format: a corrupted value must not silently reformat a published
    experiment's prompts, and refusing to run at all would be a worse failure
    than running the format the experiment has always used.
    """
    raw = (design_spec or {}).get("prompt_contract_version")
    try:
        version = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return LEGACY_PROMPT_CONTRACT
    if version < LEGACY_PROMPT_CONTRACT:
        return LEGACY_PROMPT_CONTRACT
    return version

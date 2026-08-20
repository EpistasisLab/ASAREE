#!/usr/bin/env python3
"""Import myocardial-use-case.json (and its dataset) into a running ASAREE.

    ASAREE_BASE_URL=http://localhost:8000 ASAREE_API_KEY=... \
        uv run --with ./sdk python publications/bioinformatics/import_use_case.py

See README.md in this directory for the full walkthrough. Idempotent: run it
again and it reuses whatever already exists under the same names rather than
creating a second copy.

What it does, in order (the order matters -- an experiment has to exist before
a protocol can attach to it, and the dataset has to be registered before the
graph can point at it):

1. Register `myocardial_infarction` from mi_ZSN.csv + dict_ZSN.json.
2. Split it 70/30, stratified on the target, seed 42.
3. Create the experiment and attach the dataset.
4. Create the protocol from the file's `graph`, rewriting the dataset and MCP
   server UUIDs baked into it to this deployment's own (see `_localize`).
5. Apply the file's `design_spec` and materialize its cells.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from asaree_client import AsareeClient
from asaree_client.exceptions import AsareeNotFoundError

HERE = Path(__file__).resolve().parent

USE_CASE_FILE = HERE / "myocardial-use-case.json"
DATA_FILE = HERE / "mi_ZSN.csv"
DICTIONARY_FILE = HERE / "dict_ZSN.json"

DATASET_NAME = "myocardial_infarction"
TARGET_COLUMN = "mi_ZSN"
TEST_SIZE = 0.3
SPLIT_SEED = 42


def _localize(graph: dict[str, Any], *, dataset_id: str, server_ids: dict[str, str]) -> list[str]:
    """Repoint the graph's baked-in UUIDs at this deployment's own rows.

    Execution itself resolves by NAME, not UUID -- `protocol_execution` reads
    `server_name` off an mcp_tool node and `dataset_name` off the dataset node
    -- so a stale `server_id`/`dataset_id` doesn't stop a run. But the protocol
    canvas reads them to show which server/dataset a node is bound to, so an
    export from someone else's install renders as unresolved until they're
    rewritten. Cheap to fix here; confusing to leave.

    Returns the names of any servers the graph wants that aren't registered on
    this deployment -- empty on a normal install, since all six asaree-sklearn-*
    servers and asaree-workspace now ship with ASAREE itself.
    """
    missing: list[str] = []
    for node in graph.get("nodes", []):
        config = node.get("data", {}).get("config", {})
        if node.get("type") == "dataset":
            config["dataset_id"] = dataset_id
        elif node.get("type") == "mcp_tool":
            name = config.get("server_name")
            if name in server_ids:
                config["server_id"] = server_ids[name]
            elif name is not None and name not in missing:
                missing.append(name)
    return missing


def main() -> int:
    for path in (USE_CASE_FILE, DATA_FILE, DICTIONARY_FILE):
        if not path.is_file():
            print(f"ERROR: {path} is missing.", file=sys.stderr)
            return 2
    if not os.environ.get("ASAREE_BASE_URL") or not os.environ.get("ASAREE_API_KEY"):
        print("ERROR: export ASAREE_BASE_URL and ASAREE_API_KEY first.", file=sys.stderr)
        print("       See sdk/README.md's 'Auth bootstrap' to issue a token.", file=sys.stderr)
        return 2

    use_case = json.loads(USE_CASE_FILE.read_text())
    graph = use_case["graph"]

    with AsareeClient() as client:
        # --- 1. the dataset -------------------------------------------------
        try:
            dataset = client.datasets.get_by_name(DATASET_NAME)
            print(f"dataset      reusing {DATASET_NAME} ({dataset.id})")
        except AsareeNotFoundError:
            dataset = client.datasets.create(
                DATASET_NAME,
                str(DATA_FILE),
                target_column=TARGET_COLUMN,
                description=(
                    "UCI Myocardial Infarction Complications (ZSN): 1700 admissions x 111 raw "
                    "features, predicting chronic heart failure as a post-MI complication. "
                    "https://archive.ics.uci.edu/dataset/579/myocardial+infarction+complications"
                ),
                # Opaque to ASAREE, which never parses it -- it's what
                # asaree-sklearn-eda's get_data_dictionary serves back to an
                # agent that asks what a column means. This dataset needs it:
                # the column names are short Russian-derived codes (nr11,
                # zab_leg_01, S_AD_KBRIG), not descriptive English.
                dictionary_json=DICTIONARY_FILE.read_text(),
            )
            print(f"dataset      registered {DATASET_NAME} ({dataset.id})")

        # --- 2. the split ---------------------------------------------------
        # Stratified on the target (23.2% positive, so an unstratified split
        # would leave the two halves at materially different base rates).
        # Re-splitting is safe: it overwrites rather than accumulating.
        if dataset.train_path and dataset.test_path:
            print("split        already present, left alone")
        else:
            dataset = client.datasets.quick_split(
                dataset.id, target_column=TARGET_COLUMN, test_size=TEST_SIZE, seed=SPLIT_SEED
            )
            print(f"split        {1 - TEST_SIZE:.0%}/{TEST_SIZE:.0%} stratified, seed {SPLIT_SEED}")

        # --- 3. the experiment ----------------------------------------------
        name = use_case["name"]
        experiment = next((e for e in client.experiments.list() if e.name == name), None)
        if experiment is None:
            experiment = client.experiments.create(name=name, description=use_case.get("description"))
            print(f"experiment   created {name!r} ({experiment.id})")
        else:
            print(f"experiment   reusing {name!r} ({experiment.id})")
        experiment = client.experiments.update(experiment.id, dataset_id=dataset.id)

        # --- 4. the protocol ------------------------------------------------
        servers = {s.name: str(s.id) for s in client.tools.list_servers()}
        missing = _localize(graph, dataset_id=str(dataset.id), server_ids=servers)
        if missing:
            # Not fatal -- the protocol still imports, and a run would still
            # resolve these by name if they showed up later. But every tool
            # call against them fails until they do, so say so loudly.
            print(f"WARNING      MCP servers not registered here: {', '.join(missing)}", file=sys.stderr)

        # Every protocol, not just this experiment's: names are unique per
        # OWNER, so a same-named protocol parked under another experiment would
        # make create() 409 rather than fall through to the update branch.
        protocol = next((p for p in client.protocols.list() if p.name == name), None)
        if protocol is None:
            protocol = client.protocols.create(
                name=name, description=use_case.get("description"), experiment_id=experiment.id, graph=graph
            )
            print(f"protocol     created ({protocol.id})")
        else:
            protocol = client.protocols.update(protocol.id, graph=graph)
            print(f"protocol     graph updated ({protocol.id})")

        # --- 5. the design --------------------------------------------------
        # A full replacement, not a merge -- see experiments.update's docstring.
        experiment = client.experiments.update(experiment.id, design_spec=use_case["design_spec"])
        cells = client.experiments.generate_design(experiment.id)
        print(f"design       {len(cells)} cells")

        print(f"\nDone. Open /experiments/{experiment.id}/protocol")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

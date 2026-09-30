#!/usr/bin/env python3
"""Import a myocardial-*.json use case (and its dataset) into a running ASAREE.

    ASAREE_BASE_URL=http://localhost:8000 ASAREE_API_KEY=... \
        uv run --with ./sdk python publications/BDM/import_use_case.py \
        [myocardial-anthropic-v0.8.0.json]

The API-side equivalent of README.md's GUI walkthrough, for rebuilding the
experiment from scratch repeatedly. It accepts the v0.8.0 provider variants
and defaults to Azure Foundry.
Idempotent: run it again and it updates the existing experiment rather than
creating a second copy.

What it does, in order:

1. Register `myocardial_infarction` from mi_ZSN.csv + dict_ZSN.json.
2. Split it 70/30, stratified on the target, seed 42.
3. Rewrite the graph and measurement-source UUIDs for this deployment.
4. Atomically import the experiment and its canvas (or update an existing one),
   then attach the dataset.
5. Publish an immutable protocol revision.
6. Apply the design and materialize its eight cells and 160 replicates.
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

DEFAULT_USE_CASE_FILE = HERE / "myocardial-azure-foundry-v0.8.0.json"
DATA_FILE = HERE / "mi_ZSN.csv"
DICTIONARY_FILE = HERE / "dict_ZSN.json"

DATASET_NAME = "myocardial_infarction"
TARGET_COLUMN = "mi_ZSN"
TEST_SIZE = 0.3
SPLIT_SEED = 42


def _localize(
    graph: dict[str, Any],
    measurement_plan: dict[str, Any],
    *,
    dataset_id: str,
    server_ids: dict[str, str],
) -> list[str]:
    """Repoint baked-in resource UUIDs at this deployment's own rows.

    Execution itself resolves by NAME, not UUID -- `protocol_execution` reads
    `server_name` off an mcp_tool node and `dataset_name` off the dataset node
    -- so a stale `server_id`/`dataset_id` doesn't stop a run. But the protocol
    canvas reads them to show which server/dataset a node is bound to, so an
    export from someone else's install renders as unresolved until they're
    rewritten. Measurement producers also identify the exact MCP server whose
    call is authoritative, so those references are localized with the graph.

    Returns the names of any servers the graph wants that aren't registered on
    this deployment -- empty on a normal install, since all six asaree-sklearn-*
    servers and asaree-workspace now ship with ASAREE itself.
    """
    missing: list[str] = []
    server_id_by_node: dict[str, str] = {}
    for node in graph.get("nodes", []):
        config = node.get("data", {}).get("config", {})
        if node.get("type") == "dataset":
            config["dataset_id"] = dataset_id
        elif node.get("type") == "mcp_tool":
            name = config.get("server_name")
            if name in server_ids:
                config["server_id"] = server_ids[name]
                server_id_by_node[str(node.get("id"))] = server_ids[name]
            elif name is not None and name not in missing:
                missing.append(name)
    for producer in measurement_plan.get("producers", []):
        config = producer.get("config", {})
        node_id = str(config.get("mcp_node_id", ""))
        if node_id in server_id_by_node:
            config["server_id"] = server_id_by_node[node_id]
    return missing


def main(argv: list[str]) -> int:
    # The three provider variants differ only in their LLM node and the
    # factors bound to it, so any of them imports through this same path.
    use_case_file = Path(argv[0]).resolve() if argv else DEFAULT_USE_CASE_FILE
    for path in (use_case_file, DATA_FILE, DICTIONARY_FILE):
        if not path.is_file():
            print(f"ERROR: {path} is missing.", file=sys.stderr)
            return 2
    if not os.environ.get("ASAREE_BASE_URL") or not os.environ.get("ASAREE_API_KEY"):
        print("ERROR: export ASAREE_BASE_URL and ASAREE_API_KEY first.", file=sys.stderr)
        print("       See sdk/README.md's 'Auth bootstrap' to issue a token.", file=sys.stderr)
        return 2

    use_case = json.loads(use_case_file.read_text())
    graph = use_case["graph"]
    measurement_plan = use_case.get("measurement_plan")
    if not isinstance(measurement_plan, dict):
        print(
            "ERROR: this definition lacks the v0.8.0 measurement plan.",
            file=sys.stderr,
        )
        return 2

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

        # --- 3. deployment-local references ---------------------------------
        servers = {s.name: str(s.id) for s in client.tools.list_servers()}
        missing = _localize(
            graph,
            measurement_plan,
            dataset_id=str(dataset.id),
            server_ids=servers,
        )
        if missing:
            print(f"ERROR: MCP servers not registered here: {', '.join(missing)}", file=sys.stderr)
            return 2

        # --- 4. experiment + canvas -----------------------------------------
        name = use_case["name"]
        experiment = next((e for e in client.experiments.list() if e.name == name), None)
        created = experiment is None
        if experiment is None:
            experiment = client.experiments.import_definition(
                name=name,
                description=use_case.get("description"),
                hypothesis=use_case.get("hypothesis"),
                design_type=use_case.get("design_type", "factorial"),
                task_brief=use_case.get("task_brief"),
                design_spec=use_case["design_spec"],
                measurement_plan=measurement_plan,
                graph=graph,
                published_graph=graph,
                protocol_description=use_case.get("description"),
            )
            print(f"experiment   created {name!r} ({experiment.id})")
        else:
            print(f"experiment   reusing {name!r} ({experiment.id})")
            if experiment.locked_at is not None:
                experiment = client.experiments.unlock(experiment.id)
                print("experiment   unlocked for update")
        protocols = client.protocols.list(experiment_id=experiment.id)
        if len(protocols) != 1:
            print(
                f"ERROR: expected one canvas for experiment {experiment.id}, found {len(protocols)}.",
                file=sys.stderr,
            )
            return 2
        protocol = protocols[0]
        if not created:
            protocol = client.protocols.update(protocol.id, graph=graph)
            print(f"protocol     graph updated ({protocol.id})")

        # Full replacements, not merges. The protocol id asks the server to
        # validate producer wiring against this exact localized graph.
        experiment = client.experiments.update(
            experiment.id,
            description=use_case.get("description"),
            hypothesis=use_case.get("hypothesis"),
            dataset_ids=[dataset.id],
            design_spec=use_case["design_spec"],
            measurement_plan=measurement_plan,
            measurement_validation_protocol_id=protocol.id,
        )

        # --- 5. immutable production revision -------------------------------
        protocol = client.protocols.publish(protocol.id)
        print(f"protocol     published revision {protocol.published_revision}")

        # --- 6. design ------------------------------------------------------
        replicates = client.experiments.generate_design(experiment.id)
        cell_count = len({replicate.cell_id for replicate in replicates})
        print(f"design       {cell_count} cells, {len(replicates)} replicates")

        print(f"\nDone. Open /experiments/{experiment.id}/protocol")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

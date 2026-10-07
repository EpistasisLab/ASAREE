from __future__ import annotations

import copy
import hashlib

import pytest

from asaree.services.dataset_row_csv import read_row_source
from asaree.services.dataset_row_inputs import DatasetRowInputError
from asaree.services.dataset_row_planning import enumerate_row_candidates

DATASET_ID = "11111111-1111-4111-8111-111111111111"


def make_graph() -> dict:
    return {
        "nodes": [
            {
                "id": "dataset",
                "type": "dataset",
                "data": {"config": {"dataset_id": DATASET_ID}},
            },
            {
                "id": "agent",
                "type": "agent",
                "data": {
                    "config": {"system_prompt": "Base prompt"},
                    "factor_bindings": {"config.system_prompt": "Prompt"},
                },
            },
        ],
        "edges": [
            {
                "id": "dataset-agent",
                "source": "dataset",
                "target": "agent",
                "targetHandle": "dataset",
                "data": {"dataset_input": {"mode": "per_row", "columns": ["question"]}},
            }
        ],
    }


def make_source(tmp_path, content: str = "question\nrepeat\nrepeat\nlast\n"):
    path = tmp_path / "original.csv"
    raw = content.encode()
    path.write_bytes(raw)
    return read_row_source(
        dataset_id=DATASET_ID,
        raw_path=str(path),
        raw_sha256=hashlib.sha256(raw).hexdigest(),
    )


def make_parents() -> list[dict]:
    return [
        {
            "replicate_result_id": f"result-{level}-{replicate}",
            "cell_id": f"cell-{level}",
            "cell_label": f"Prompt={level}",
            "replicate_label": f"Prompt={level} / replicate {replicate}",
            "replicate_number": replicate,
            "design_revision_id": "design-revision",
            "factor_values": {"Prompt": f"Prompt for level {level}"},
        }
        for level in range(5)
        for replicate in range(1, 3)
    ]


def test_expands_five_factor_cells_and_two_parents_across_three_original_rows(tmp_path):
    candidates = enumerate_row_candidates(
        parents=make_parents(),
        source=make_source(tmp_path),
        graph=make_graph(),
        design_spec={"factors": [{"name": "Prompt", "levels": [str(i) for i in range(5)]}]},
        protocol_revision_id="published-revision",
    )

    assert len(candidates) == 30
    assert len({candidate["cell_id"] for candidate in candidates}) == 5
    assert len({candidate["replicate_result_id"] for candidate in candidates}) == 10
    assert [candidate["dataset_row"]["row_index"] for candidate in candidates[:3]] == [0, 1, 2]
    assert all(candidate["protocol_revision_id"] == "published-revision" for candidate in candidates)


def test_duplicate_row_values_keep_distinct_original_positions(tmp_path):
    source = make_source(tmp_path)
    candidates = enumerate_row_candidates(
        parents=make_parents()[:1],
        source=source,
        graph=make_graph(),
        design_spec=None,
        protocol_revision_id="published-revision",
    )

    assert source.rows[0] == source.rows[1]
    assert [candidate["dataset_row"]["row_index"] for candidate in candidates[:2]] == [0, 1]


def test_explicit_row_selection_keeps_original_index(tmp_path):
    candidates = enumerate_row_candidates(
        parents=make_parents()[:1], source=make_source(tmp_path), graph=make_graph(),
        design_spec=None, protocol_revision_id="published-revision", row_indices=[2],
    )
    assert len(candidates) == 1
    assert candidates[0]["dataset_row"]["row_index"] == 2
    assert candidates[0]["replicate_result_id"] == "result-0-1"


@pytest.mark.parametrize("indices", [[], [-1], [3], [1, 1], [True], [1.5]])
def test_rejects_invalid_source_row_selections(tmp_path, indices):
    with pytest.raises(DatasetRowInputError, match="invalid_row_selection"):
        enumerate_row_candidates(
            parents=make_parents()[:1], source=make_source(tmp_path), graph=make_graph(),
            design_spec=None, protocol_revision_id="published-revision", row_indices=indices,
        )


def test_candidate_order_does_not_depend_on_parent_input_order(tmp_path):
    arguments = {
        "source": make_source(tmp_path),
        "graph": make_graph(),
        "design_spec": None,
        "protocol_revision_id": "published-revision",
    }

    forward = enumerate_row_candidates(parents=make_parents(), **arguments)
    backward = enumerate_row_candidates(parents=list(reversed(make_parents())), **arguments)

    assert forward == backward
    assert [candidate["replicate_result_id"] for candidate in forward[:6]] == [
        "result-0-1", "result-0-1", "result-0-1",
        "result-0-2", "result-0-2", "result-0-2",
    ]


def test_prompt_factor_substitution_is_allowed_and_inputs_are_unchanged(tmp_path):
    parents = make_parents()[:1]
    graph = make_graph()
    parents_before, graph_before = copy.deepcopy(parents), copy.deepcopy(graph)

    candidates = enumerate_row_candidates(
        parents=parents,
        source=make_source(tmp_path),
        graph=graph,
        design_spec=None,
        protocol_revision_id="published-revision",
    )

    assert len(candidates) == 3
    assert parents == parents_before
    assert graph == graph_before


def test_rejects_empty_source_and_driver_mutation(tmp_path):
    empty = make_source(tmp_path, "question\n")
    with pytest.raises(DatasetRowInputError, match="empty_source"):
        enumerate_row_candidates(
            parents=make_parents(), source=empty, graph=make_graph(), design_spec=None,
            protocol_revision_id="published-revision",
        )

    graph = make_graph()
    graph["nodes"][0]["data"]["factor_bindings"] = {"config.enabled": "Driver enabled"}
    with pytest.raises(DatasetRowInputError, match="factorized_driver"):
        enumerate_row_candidates(
            parents=make_parents(), source=make_source(tmp_path), graph=graph, design_spec=None,
            protocol_revision_id="published-revision",
        )

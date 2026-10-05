from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from asaree.services.dataset_row_csv import project_row, read_row_source
from asaree.services.protocol_execution import _build_user_input

DATASET_ID = str(uuid.UUID("12345678-1234-5678-1234-567812345678"))


def _fixture(tmp_path: Path, *, columns: list[str]) -> tuple[dict, dict, dict]:
    raw = (
        "question,reference,other\n"
        "What is 2+2?,same-row-reference,first-row-secret\n"
        "Next?,another-row-secret,second-row-secret\n"
    )
    source_path = tmp_path / "source.csv"
    source_path.write_text(raw, encoding="utf-8")
    source = read_row_source(
        dataset_id=DATASET_ID,
        raw_path=str(source_path),
        raw_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
    )
    view = project_row(source, row_index=0, columns=columns)
    agent = {"id": "agent", "type": "agent", "data": {"label": "Agent", "config": {"prompt": "Authored instructions."}}}
    dataset = {
        "id": "dataset",
        "type": "dataset",
        "data": {
            "label": "Dataset",
            "config": {
                "dataset_id": DATASET_ID,
                "dataset_name": "private-registration-name",
                "description": "FULL DICTIONARY MUST NOT APPEAR",
                "target_column": "reference",
                "split_state": "test",
                "dictionary_available": True,
            },
        },
    }
    graph = {
        "nodes": [agent, dataset],
        "edges": [
            {
                "id": "driver",
                "source": "dataset",
                "target": "agent",
                "targetHandle": "dataset",
                "data": {"dataset_input": {"mode": "per_row", "columns": columns}},
            }
        ],
    }
    return agent, graph, view


def _assemble(agent: dict, graph: dict, view: dict) -> str:
    return _build_user_input(agent, graph, {}, row_input_context=view)


def test_prediction_prompt_contains_only_projected_question_row(tmp_path: Path) -> None:
    agent, graph, view = _fixture(tmp_path, columns=["question"])
    prompt = _assemble(agent, graph, view)
    block = prompt.split("Dataset row input:\n", 1)[1]
    row = json.loads(block)
    assert row == view
    assert "What is 2+2?" in prompt
    assert "same-row-reference" not in prompt
    assert "first-row-secret" not in prompt
    assert "second-row-secret" not in prompt
    assert "FULL DICTIONARY MUST NOT APPEAR" not in prompt
    assert "private-registration-name" not in prompt
    assert "target=reference" not in prompt
    assert "split_state" not in prompt
    assert "source.csv" not in prompt


def test_grading_prompt_contains_reference_for_the_same_projected_row(tmp_path: Path) -> None:
    agent, graph, view = _fixture(tmp_path, columns=["question", "reference"])
    prompt = _assemble(agent, graph, view)
    row = json.loads(prompt.split("Dataset row input:\n", 1)[1])
    assert row["values"] == {"question": "What is 2+2?", "reference": "same-row-reference"}
    assert "another-row-secret" not in prompt


def test_row_json_preserves_empty_unicode_and_requested_column_order(tmp_path: Path) -> None:
    raw = "question,reference,note\n雪,,café\n".encode()
    source_path = tmp_path / "unicode.csv"
    source_path.write_bytes(raw)
    source = read_row_source(
        dataset_id=DATASET_ID, raw_path=str(source_path), raw_sha256=hashlib.sha256(raw).hexdigest()
    )
    view = project_row(source, row_index=0, columns=["note", "reference", "question"])
    agent = {"id": "agent", "type": "agent", "data": {"label": "Agent", "config": {"prompt": "Authored instructions."}}}
    dataset = {
        "id": "dataset",
        "type": "dataset",
        "data": {"label": "Dataset", "config": {"dataset_id": DATASET_ID, "dataset_name": "cohort"}},
    }
    graph = {
        "nodes": [agent, dataset],
        "edges": [
            {
                "id": "driver",
                "source": "dataset",
                "target": "agent",
                "targetHandle": "dataset",
                "data": {"dataset_input": {"mode": "per_row", "columns": view["columns"]}},
            }
        ],
    }
    prompt = _assemble(agent, graph, view)
    encoded = prompt.split("Dataset row input:\n", 1)[1]
    assert "café" in encoded and "雪" in encoded
    row = json.loads(encoded)
    assert row["columns"] == ["note", "reference", "question"]
    assert row["values"] == {"note": "café", "reference": "", "question": "雪"}


def test_agent_without_driver_edge_gets_no_row_values_or_catalog_entry(tmp_path: Path) -> None:
    agent, graph, view = _fixture(tmp_path, columns=["question"])
    graph["edges"] = []
    prompt = _build_user_input(agent, graph, {}, row_input_context=view)
    assert "Dataset row input:" not in prompt
    assert "Available datasets:" not in prompt
    assert "What is 2+2?" not in prompt


def test_whole_context_and_legacy_prompt_remain_compatible() -> None:
    agent = {"id": "agent", "type": "agent", "data": {"label": "Agent", "config": {"prompt": "Authored instructions."}}}
    assert _build_user_input(agent, {"nodes": [agent], "edges": []}, {}) == "Authored instructions."
    dataset = {"id": "dataset", "type": "dataset", "data": {"label": "Dataset", "config": {"dataset_name": "cohort"}}}
    graph = {
        "nodes": [agent, dataset],
        "edges": [{"id": "d", "source": "dataset", "target": "agent", "targetHandle": "dataset"}],
    }
    prompt = _build_user_input(agent, graph, {}, experiment_id=uuid.uuid4(), effective_cell_label="cell")
    assert "Dataset context:" in prompt and "open_workspace()" in prompt
    assert "Available datasets:\n- cohort" in prompt
    assert "Dataset row input:" not in prompt

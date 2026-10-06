from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from asaree.services import dataset_row_workspaces as workspaces
from asaree.services.dataset_row_csv import DatasetRowCsvError, read_row_source


@pytest.fixture
def row_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "workspace-root"
    monkeypatch.setattr(workspaces, "_workspace_root", lambda: root)
    original = tmp_path / "registered.csv"
    original.write_text("question,answer,extra\nfirst,one,x\nsecond,two,y\n", encoding="utf-8")
    data = original.read_bytes()
    dataset_id = str(uuid.uuid4())
    source = read_row_source(
        dataset_id=dataset_id,
        raw_path=str(original),
        raw_sha256=hashlib.sha256(data).hexdigest(),
    )
    return root, original, source


def _binding(dataset_id: str, agent: str, row: int, columns: list[str], target: str | None = None) -> dict:
    return {
        "dataset_id": dataset_id,
        "dataset_name": "Owned registration",
        "agent_node_id": agent,
        "columns": columns,
        "row_index": row,
        "target_column": target,
    }


def test_rows_and_retry_get_fresh_attempt_files_without_inherited_scratch(row_setup) -> None:
    root, _, source = row_setup
    agent = "agent"
    runs = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    first = workspaces.prepare_agent_row_inputs(
        run_id=runs[0], agent_node_id=agent, source=source,
        bindings=[_binding(source.dataset_id, agent, 0, ["question"])],
    )["row_inputs"][0]
    second = workspaces.prepare_agent_row_inputs(
        run_id=runs[1], agent_node_id=agent, source=source,
        bindings=[_binding(source.dataset_id, agent, 1, ["question"])],
    )["row_inputs"][0]
    retry = workspaces.prepare_agent_row_inputs(
        run_id=runs[2], agent_node_id=agent, source=source,
        bindings=[_binding(source.dataset_id, agent, 0, ["question"])],
    )["row_inputs"][0]
    assert len({first["path"], second["path"], retry["path"]}) == 3
    assert all(not (Path(item["path"]).parent / "scratch.py").exists() for item in (first, second, retry))
    assert (root / "_protocol_runs" / str(runs[0]) / "agents").is_dir()


def test_agents_get_separate_directories_and_column_views(row_setup) -> None:
    _, _, source = row_setup
    run_id = uuid.uuid4()
    result_a = workspaces.prepare_agent_row_inputs(
        run_id=run_id, agent_node_id="agent-a", source=source,
        bindings=[_binding(source.dataset_id, "agent-a", 0, ["question"])],
    )["row_inputs"][0]
    result_b = workspaces.prepare_agent_row_inputs(
        run_id=run_id, agent_node_id="agent-b", source=source,
        bindings=[_binding(source.dataset_id, "agent-b", 0, ["answer"])],
    )["row_inputs"][0]
    assert result_a["columns"] == ["question"]
    assert result_b["columns"] == ["answer"]
    assert Path(result_a["path"]).parent != Path(result_b["path"]).parent


def test_repeated_same_run_view_is_idempotent_and_unselected_target_is_valid(row_setup) -> None:
    _, original, source = row_setup
    run_id = uuid.uuid4()
    binding = _binding(source.dataset_id, "node/../agent", 1, ["question", "extra"])
    first = workspaces.prepare_agent_row_inputs(
        run_id=run_id, agent_node_id="node/../agent", source=source, bindings=[binding]
    )
    Path(first["row_inputs"][0]["path"]).parent.joinpath("scratch.txt").write_text("private")
    second = workspaces.prepare_agent_row_inputs(
        run_id=run_id, agent_node_id="node/../agent", source=source, bindings=[binding]
    )
    assert first == second
    assert second["row_inputs"][0]["target_column"] == ""
    assert Path(second["row_inputs"][0]["path"]).is_relative_to(Path(first["row_inputs"][0]["path"]).parents[2])
    assert original.read_text(encoding="utf-8").startswith("question,answer,extra")


def test_empty_agent_edge_and_selected_registration_target(row_setup) -> None:
    _, _, source = row_setup
    empty = workspaces.prepare_agent_row_inputs(
        run_id=uuid.uuid4(), agent_node_id="agent", source=source,
        bindings=[_binding(source.dataset_id, "other", 0, ["question"])],
    )
    assert empty["row_inputs"] == []
    result = workspaces.prepare_agent_row_inputs(
        run_id=uuid.uuid4(), agent_node_id="agent", source=source,
        bindings=[_binding(source.dataset_id, "agent", 0, ["question"], "answer")],
    )["row_inputs"][0]
    assert result["target_column"] == ""
    selected_target = workspaces.prepare_agent_row_inputs(
        run_id=uuid.uuid4(), agent_node_id="agent", source=source,
        bindings=[_binding(source.dataset_id, "agent", 0, ["question", "answer"], "answer")],
    )["row_inputs"][0]
    assert selected_target["target_column"] == "answer"


def test_source_identity_and_corrupted_existing_snapshot_fail_closed(row_setup) -> None:
    _, _, source = row_setup
    run_id = uuid.uuid4()
    binding = _binding(source.dataset_id, "agent", 0, ["question"])
    result = workspaces.prepare_agent_row_inputs(
        run_id=run_id, agent_node_id="agent", source=source, bindings=[binding]
    )["row_inputs"][0]
    Path(result["path"]).write_text("question\ncorrupted\n", encoding="utf-8")
    with pytest.raises(DatasetRowCsvError, match="workspace_conflict"):
        workspaces.prepare_agent_row_inputs(
            run_id=run_id, agent_node_id="agent", source=source, bindings=[binding]
        )
    wrong = _binding(str(uuid.uuid4()), "agent", 0, ["question"])
    with pytest.raises(DatasetRowCsvError, match="source_identity_mismatch"):
        workspaces.prepare_agent_row_inputs(
            run_id=uuid.uuid4(), agent_node_id="agent", source=source, bindings=[wrong]
        )

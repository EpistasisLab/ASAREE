"""Integrated five-cell, three-original-row, two-replicate production acceptance."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import uuid
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from motoro.models import database as core_database
from motoro.models.run import AgentRun, RunStatus
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from asaree.api import protocols as api
from asaree.api.experiments import export_run_results_csv_endpoint, get_experiment_run_results_endpoint
from asaree.models import database
from asaree.models.dataset import RegisteredDataset
from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.models.protocol import Protocol
from asaree.models.protocol_run import ProtocolRun
from asaree.services import dataset_row_workspaces, run_tools
from asaree.services import protocol_execution as execution
from asaree.services.design_generation import generate_design_cells
from asaree.services.experiments import create_experiment, delete_experiment
from asaree.services.factorial_row_results import project_row_attempt
from asaree.services.protocol_revisions import publish_protocol
from asaree.services.protocols import create_protocol, delete_protocol
from asaree.services.users import get_user_by_email


@pytest.mark.asyncio
async def test_five_cells_three_rows_two_replicates_and_one_failed_row_retry(tmp_path, monkeypatch):
    product_engine = create_async_engine(os.environ["ASAREE_PRODUCT_DATABASE_URL"])
    core_engine = create_async_engine(os.environ["DATABASE_URL"])
    sessions = async_sessionmaker(product_engine, expire_on_commit=False)
    core_sessions = async_sessionmaker(core_engine, expire_on_commit=False)
    monkeypatch.setattr(database, "_get_session_factory", lambda: sessions)
    monkeypatch.setattr(core_database, "_get_session_factory", lambda: core_sessions)
    monkeypatch.setattr(execution, "get_session", database.get_session)
    monkeypatch.setattr(dataset_row_workspaces, "_workspace_root", lambda: tmp_path / "datasets")
    monkeypatch.setattr(execution, "WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("ASAREE_DATASET_WORKSPACE_DIR", str(tmp_path / "workspaces"))
    source_path = tmp_path / "original.csv"
    raw = b"question,reference\nQUESTION-1,GOLD-1\nQUESTION-1,GOLD-1\nQUESTION-3,GOLD-3\n"
    source_path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    experiment_id = protocol_id = dataset_id = None
    queued = []
    provider_calls = []
    private_paths = set()
    failure_run = None
    null_run = None
    missing_run = None
    attempted_failure = False

    async def enqueue(run_id):
        async with sessions() as db:
            assert await db.get(ProtocolRun, run_id) is not None, "enqueue follows commit"
        queued.append(run_id)

    async def hydrate():
        return None

    registry = SimpleNamespace(
        get_all_tools=lambda **kwargs: [
            {
                "name": f"{execution.SCRIPT_SERVER_NAME}.run_wired_script",
                "tool_name": "run_wired_script",
                "server": execution.SCRIPT_SERVER_NAME,
                "description": "Inspect authorized row",
                "input_schema": {},
            }
        ]
    )
    monkeypatch.setattr(api, "enqueue_protocol_run", enqueue)
    monkeypatch.setattr(execution, "hydrate_registry", hydrate)
    monkeypatch.setattr(execution, "get_registry", lambda: registry)
    monkeypatch.setattr(run_tools, "get_registry", lambda: registry)

    async def provider(*, run_id, available_tools, **kwargs):
        nonlocal attempted_failure
        from asaree.mcp_servers import script_server

        async with core_sessions() as db:
            agent_run = await db.get(AgentRun, run_id)
            meta = agent_run.run_metadata
            node_id = meta["node_id"]
            ambient = meta["ambient_meta"]
            if ambient.get("dataset_mode") != "per_row":
                assert "Dataset row input:" not in agent_run.input
                agent_run.status = RunStatus.COMPLETED
                agent_run.output = "whole dataset regression"
                await db.commit()
                return
            values = ambient["row_inputs"][0]["values"]
            row_index = ambient["row_inputs"][0]["row_index"]
            protocol_run_id = uuid.UUID(meta["protocol_run_id"])
            input_text = agent_run.input
            assert ambient["dataset_mode"] == "per_row"
            assert values["question"] == ("QUESTION-3" if row_index == 2 else "QUESTION-1")
            if node_id == "prediction":
                assert "GOLD" not in input_text and "reference" not in input_text
                assert "GOLD" not in json.dumps(ambient)
                assert any(tool["tool_name"] == "run_wired_script" for tool in available_tools)
                metadata = {f"motoro.ambient.{key}": value for key, value in ambient.items()}
                metadata["motoro.workspace_id"] = f"_protocol_runs/{protocol_run_id}"
                result = json.loads(
                    script_server.run_wired_script(
                        ctx=SimpleNamespace(request_context=SimpleNamespace(meta=SimpleNamespace(model_extra=metadata)))
                    )
                )
                assert result["exit_code"] == 0, result
                output = json.loads(result["stdout"])
                assert output["rows"] == [{"question": values["question"]}]
                assert "GOLD" not in json.dumps(output)
                path = output["path"]
                manifest = json.loads((Path(path).parent / "row-view.json").read_text())
                assert manifest["values"] == {"question": values["question"]}
                assert manifest["row_index"] == row_index and manifest["raw_sha256"] == digest
                assert "GOLD" not in json.dumps(manifest)
                assert path not in private_paths, "every attempt has a fresh private input"
                private_paths.add(path)
            else:
                assert node_id == "grading"
                assert values["reference"] == ("GOLD-3" if row_index == 2 else "GOLD-1")
                assert values["reference"] in input_text
            provider_calls.append((protocol_run_id, node_id, row_index, input_text))
        if protocol_run_id == failure_run and node_id == "prediction" and not attempted_failure:
            attempted_failure = True
            raise RuntimeError("injected row failure")
        async with core_sessions.begin() as db:
            agent_run = await db.get(AgentRun, run_id)
            agent_run.status = RunStatus.COMPLETED
            agent_run.output = (
                None
                if protocol_run_id == missing_run and node_id == "grading"
                else "prediction"
                if node_id == "prediction"
                else "graded"
            )

    async def steps(run_id):
        async with core_sessions() as db:
            run = await db.get(AgentRun, run_id)
            if run.run_metadata["node_id"] != "prediction":
                return []
            protocol_run_id = uuid.UUID(run.run_metadata["protocol_run_id"])
        return [
            SimpleNamespace(
                iteration=1,
                sequence=1,
                tool_call={
                    "server": execution.SCRIPT_SERVER_NAME,
                    "tool": "run_wired_script",
                    "arguments": {"script": "inspect-row"},
                    "success": True,
                    "result": None if protocol_run_id == null_run else {"opaque": "12"},
                },
            )
        ]

    monkeypatch.setattr(execution, "execute_run", provider)
    monkeypatch.setattr("asaree.services.reported_metrics.get_run_steps", steps)
    try:
        async with sessions.begin() as db:
            user = await get_user_by_email(db, "test@test.com")
            assert user is not None
            dataset = RegisteredDataset(
                id=uuid.uuid4(),
                name=f"acceptance-{uuid.uuid4()}",
                owner_id=user.id,
                raw_path=str(source_path),
                raw_sha256=digest,
                target_column="reference",
            )
            db.add(dataset)
            dataset_id = dataset.id
            design = {
                "factors": [{"name": "Prompt", "levels": [f"LEVEL-{i}" for i in range(5)]}],
                "replicates": 2,
                "metrics": [
                    {"id": "grade", "name": "Grade", "kind": "custom", "valueType": "opaque"},
                    {"id": "script", "name": "Script report", "kind": "custom", "valueType": "opaque"},
                ],
            }
            plan = {
                "metrics": [{"id": "grade", "name": "Grade"}, {"id": "script", "name": "Script report"}],
                "producers": [
                    {
                        "id": "grade-output",
                        "kind": "reported",
                        "producer_id": "asaree.agent_output",
                        "outputs": {"value": "grade"},
                        "config": {"agent_node_id": "grading"},
                    },
                    {
                        "id": "script-output",
                        "kind": "reported",
                        "producer_id": "asaree.python_script",
                        "outputs": {"value": "script"},
                        "config": {"agent_node_id": "prediction", "script_node_id": "script"},
                    },
                ],
                "inputs": [],
            }
            experiment = await create_experiment(
                db, name=f"acceptance-{uuid.uuid4()}", owner_id=user.id, design_spec=design, measurement_plan=plan
            )
            experiment_id = experiment.id
            graph = {
                "nodes": [
                    {
                        "id": "source",
                        "type": "dataset",
                        "data": {"config": {"dataset_id": str(dataset.id), "dataset_name": dataset.name}},
                    },
                    {
                        "id": "prediction",
                        "type": "agent",
                        "data": {
                            "config": {"name": "Prediction", "prompt": "Predict.", "system_prompt": "LEVEL-0"},
                            "factor_bindings": {"config.system_prompt": "Prompt"},
                        },
                    },
                    {"id": "grading", "type": "agent", "data": {"config": {"name": "Grading", "prompt": "Grade."}}},
                    {"id": "model-p", "type": "model_openai", "data": {"config": {}}},
                    {"id": "model-g", "type": "model_openai", "data": {"config": {}}},
                    {
                        "id": "script",
                        "type": "script",
                        "data": {
                            "config": {
                                "name": "inspect-row",
                                "code": "import csv, json\nfrom asaree.script_context import training_input\n"
                                'item=training_input()\n'
                                'print(json.dumps({"path": str(item.path), '
                                '"rows": list(csv.DictReader(item.path.open()))}))\n',
                            }
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "data-p",
                        "source": "source",
                        "target": "prediction",
                        "targetHandle": "dataset",
                        "data": {"dataset_input": {"mode": "per_row", "columns": ["question"]}},
                    },
                    {
                        "id": "data-g",
                        "source": "source",
                        "target": "grading",
                        "targetHandle": "dataset",
                        "data": {"dataset_input": {"mode": "per_row", "columns": ["question", "reference"]}},
                    },
                    {"id": "m-p", "source": "model-p", "target": "prediction", "targetHandle": "model"},
                    {"id": "m-g", "source": "model-g", "target": "grading", "targetHandle": "model"},
                    {"id": "s-p", "source": "script", "target": "prediction", "targetHandle": "tool"},
                    {"id": "p-g", "source": "prediction", "target": "grading"},
                ],
            }
            protocol = await create_protocol(
                db, name=f"acceptance-{uuid.uuid4()}", owner_id=user.id, experiment_id=experiment.id, graph=graph
            )
            protocol_id = protocol.id
            revision = await publish_protocol(db, protocol, owner_id=user.id)
            protocol.published_revision_id = revision.id
            parents = await generate_design_cells(
                db, experiment_id=experiment.id, factors=design["factors"], replicates=2, design_spec=design
            )
            assert len(parents) == 10
            assert (
                await db.scalar(
                    select(func.count()).select_from(FactorialCell).where(FactorialCell.experiment_id == experiment.id)
                )
                == 5
            )
            user_id = user.id
        async with sessions() as db:
            user = await get_user_by_email(db, "test@test.com")
            batch = await api.create_cell_runs_endpoint(protocol_id, user, db)
            assert len(batch.protocol_run_ids) == len(batch.row_result_ids) == 30
            assert batch.consumption_mode == "per_row"
            for run_id in batch.protocol_run_ids:
                pinned = await db.get(ProtocolRun, run_id)
                assert pinned.protocol_revision_id == revision.id
                assert pinned.design_revision_id is not None
                assert pinned.dataset_row["dataset_id"] == str(dataset_id)
                assert pinned.dataset_row["columns"] == ["question", "reference"]
                assert pinned.factor_values["Prompt"] in [f"LEVEL-{i}" for i in range(5)]
            failure_run, null_run, missing_run = batch.protocol_run_ids[:3]
            protocol = await db.get(Protocol, protocol_id)
            edited = deepcopy(protocol.graph)
            edited["nodes"][1]["data"]["config"]["prompt"] = "DRAFT-SENTINEL"
            protocol.graph = edited
            await db.commit()
        for run_id in batch.protocol_run_ids:
            await execution.run_protocol(run_id)
        async with sessions() as db:
            user = await get_user_by_email(db, "test@test.com")
            result = await get_experiment_run_results_endpoint(experiment_id, user, db, protocol_id=protocol_id)
            assert result.row_summary["planned"] == 30
            assert result.row_summary["completed"] == 29 and result.row_summary["failed"] == 1
            assert result.row_summary["missing_reported"] == 1
            assert result.cells == [] and result.replicates == [] and result.primary_metric is None
            assert "DRAFT-SENTINEL" not in json.dumps(provider_calls, default=str)
            rows = result.row_results
            failed = next(row for row in rows if row["status"] == "failed")
            null_row = next(row for row in rows if row["latest_attempt"]["run_id"] == str(null_run))
            observation = next(
                item for item in null_row["measurement"]["observations"] if item["metric_id"] == "script"
            )
            assert observation["status"] == "measured" and observation["value"] is None
            retry = await api.create_cell_runs_endpoint(
                protocol_id, user, db, api.CellRunBatchRequest(retry_row_result_ids=[failed["row_result_id"]])
            )
            assert len(retry.protocol_run_ids) == 1
            assert retry.row_result_ids == [uuid.UUID(failed["row_result_id"])]
        await execution.run_protocol(retry.protocol_run_ids[0])
        async with sessions() as db:
            user = await get_user_by_email(db, "test@test.com")
            result = await get_experiment_run_results_endpoint(experiment_id, user, db, protocol_id=protocol_id)
            assert result.row_summary["completed"] == result.row_summary["planned"] == 30
            assert result.row_summary["missing_reported"] == 1
            assert sum(len(row["attempts"]) for row in result.row_results) == 31
            retried = next(row for row in result.row_results if row["row_result_id"] == failed["row_result_id"])
            assert {item["status"] for item in retried["attempts"]} == {"failed", "completed"}
            assert not await project_row_attempt(
                db, row_result_id=uuid.UUID(failed["row_result_id"]), run_id=failure_run,
                fields={"metric_values": {"stale": 999}},
            )
            await db.commit()
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(FactorialReplicateResult)
                    .where(FactorialReplicateResult.id.in_([parent.id for parent in parents]))
                )
                == 10
            )
            resume = await api.create_cell_runs_endpoint(protocol_id, user, db)
            assert resume.protocol_run_ids == [] and resume.skipped == 30
            csv_response = await export_run_results_csv_endpoint(experiment_id, user, db, protocol_id=protocol_id)
            exported = csv_response.body.decode()
            assert digest in exported and str(revision.id) in exported and "row_index" in exported
            assert "null" in exported and len(list(csv.DictReader(exported.splitlines()))) == 30
            for row in result.row_results:
                assert row["dataset_row"]["raw_sha256"] == digest
                assert row["dataset_row"]["row_index"] in (0, 1, 2)
            snapshots_before = await db.scalar(select(func.count()).select_from(FactorialRowResult))
            selected_test = await api.create_test_run_endpoint(protocol_id, user, db, api.TestRunRequest(row_index=2))
            selected_play = await api.run_single_node_endpoint(
                protocol_id, "prediction", user, db, api.NodePlayRequest(row_index=2)
            )
            assert selected_test.dataset_row["row_index"] == selected_play.dataset_row["row_index"] == 2
            preview = await api.preview_node_prompt_endpoint(
                protocol_id, "prediction", api.PromptPreviewRequest(graph=graph, row_index=2), user, db
            )
            assert preview.dataset_row["row_index"] == 2 and "GOLD" not in preview.text
            assert await db.scalar(select(func.count()).select_from(FactorialRowResult)) == snapshots_before
        await execution.run_protocol(selected_test.id)
        await execution.run_protocol(selected_play.id)
        assert len(private_paths) == 33
        assert source_path.read_bytes() == raw
        async with sessions() as db:
            user = await get_user_by_email(db, "test@test.com")
            protocol = await db.get(Protocol, protocol_id)
            later = await publish_protocol(db, protocol, owner_id=user.id)
            protocol.published_revision_id = later.id
            await db.commit()
            assert later.id != revision.id
            current = await get_experiment_run_results_endpoint(experiment_id, user, db, protocol_id=protocol_id)
            assert current.row_results == []
            history = await get_experiment_run_results_endpoint(
                experiment_id, user, db, protocol_id=protocol_id, protocol_revision_id=revision.id,
            )
            assert len(history.row_results) == 30 and history.row_summary["completed"] == 30
            source_path.write_bytes(raw + b"CORRUPTED,SECRET\n")
            with pytest.raises(HTTPException) as error:
                await api.create_test_run_endpoint(protocol_id, user, db, api.TestRunRequest(row_index=0))
            assert error.value.status_code == 422
            history_after = await get_experiment_run_results_endpoint(
                experiment_id, user, db, protocol_id=protocol_id, protocol_revision_id=revision.id,
            )
            assert len(history_after.row_results) == 30
            source_path.write_bytes(raw)
            whole_graph = deepcopy(graph)
            for edge in whole_graph["edges"]:
                edge.pop("data", None)
            protocol.graph = whole_graph
            whole_revision = await publish_protocol(db, protocol, owner_id=user.id)
            protocol.published_revision_id = whole_revision.id
            await db.commit()
            async def enqueue_whole(run_id):
                queued.append(run_id)
            monkeypatch.setattr(api, "enqueue_protocol_run", enqueue_whole)
            whole = await api.create_protocol_run_endpoint(protocol_id, user, db)
            await db.commit()
            assert whole.dataset_row is None
            assert await db.scalar(select(func.count()).select_from(FactorialRowResult)) == snapshots_before
        await execution.run_protocol(whole.id)
        async with sessions() as db:
            assert (await db.get(ProtocolRun, whole.id)).status == "completed"
    finally:
        if protocol_id is not None:
            from motoro.models.agent import Agent

            async with core_sessions.begin() as db:
                agents = (
                    await db.scalars(
                        select(Agent).where(Agent.owner_id == user_id, Agent.name.like(f"protocol-{protocol_id}-%"))
                    )
                ).all()
                for agent in agents:
                    await db.delete(agent)
            async with sessions.begin() as db:
                await delete_protocol(db, protocol_id)
                await delete_experiment(db, experiment_id)
                dataset = await db.get(RegisteredDataset, dataset_id)
                if dataset is not None:
                    await db.delete(dataset)
        await product_engine.dispose()
        await core_engine.dispose()

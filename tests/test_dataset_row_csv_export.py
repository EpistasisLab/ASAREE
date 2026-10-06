"""PostgreSQL Results-to-CSV export coverage for stable dataset row slots."""

from __future__ import annotations

import csv
import io
import json
import uuid

import pytest
from fastapi import HTTPException

import asaree.api.protocols as protocol_api
from asaree.api.experiments import (
    export_run_results_csv_endpoint,
    get_run_results_schema_endpoint,
)
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.protocol_run import ProtocolRun
from asaree.services.factorial_row_results import claim_row_attempt, list_row_results

pytest_plugins = ("test_dataset_row_results_projection",)


@pytest.mark.asyncio
async def test_row_csv_and_schema_use_selected_results_projection(row_results_setup, monkeypatch) -> None:
    db, ctx = row_results_setup
    # Plan stable slots through the production planner. Each dataset position
    # remains a separate CSV record even where source values are duplicates.
    from asaree.api.protocols import CellRunBatchRequest, create_cell_runs_endpoint

    async def no_enqueue(_run_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(protocol_api, "enqueue_protocol_run", no_enqueue)
    planned = await create_cell_runs_endpoint(ctx["protocol"].id, ctx["user"], db, CellRunBatchRequest())
    slots = await list_row_results(
        db,
        experiment_id=ctx["experiment"].id,
        design_revision_id=ctx["design_revision"].id,
        protocol_revision_id=ctx["publication"].id,
    )
    assert slots
    runs = [await db.get(ProtocolRun, run_id) for run_id in planned.protocol_run_ids]
    run_by_slot = {run.row_result_id: run for run in runs if run is not None and run.row_result_id is not None}
    failed_slot = slots[2]
    failed_run = run_by_slot[failed_slot.id]
    assert failed_run is not None
    failed_replicate = await db.get(FactorialReplicateResult, failed_slot.replicate_result_id)
    assert failed_replicate is not None
    failed_run.status = "failed"
    second_failed_run = run_by_slot[slots[6].id]
    assert second_failed_run is not None
    second_failed_run.status = "failed"
    quoted_value = "quote,\n雪"
    opaque_value = {"nested": [1, None], "text": "é"}
    for slot, value in zip(slots[:5], [None, None, None, quoted_value, opaque_value], strict=True):
        if slot is failed_slot:
            continue
        run = run_by_slot[slot.id]
        assert run is not None
        status = "unavailable" if slot is slots[1] else "measured"
        run.status = "completed"
        run.attempt_result = {"measurement": {"observations": [{
            "metric_id": "reported", "metric_name": "Report", "status": status,
            "value": value, "producer": {"producer_id": "asaree.agent_output", "node_id": "agent"},
        }]}}
        if status == "measured":
            slot.metric_values = {"Report": value}
    retry = await claim_row_attempt(
        db,
        row_result_id=failed_slot.id,
        expected_run_id=failed_run.id,
        create_kwargs={
            "protocol_id": ctx["protocol"].id,
            "owner_id": ctx["user"].id,
            "replicate_label": failed_replicate.replicate_label,
            "factor_values": failed_run.factor_values,
            "replicate_result_id": failed_slot.replicate_result_id,
            "design_revision_id": ctx["design_revision"].id,
            "protocol_revision_id": ctx["publication"].id,
            "row_result_id": failed_slot.id,
            "dataset_row": failed_run.dataset_row,
        },
    )
    assert retry is not None
    await db.flush()

    selectors = {
        "protocol_id": ctx["protocol"].id,
        "design_revision_id": ctx["design_revision"].id,
        "protocol_revision_id": ctx["publication"].id,
    }
    response = await export_run_results_csv_endpoint(ctx["experiment"].id, ctx["user"], db, **selectors)
    parsed = list(csv.DictReader(io.StringIO(response.body.decode("utf-8"))))
    schema = await get_run_results_schema_endpoint(ctx["experiment"].id, ctx["user"], db, **selectors)
    header = next(csv.reader(io.StringIO(response.body.decode("utf-8"))))

    assert len(parsed) == len(slots)
    assert len({row["row_result_id"] for row in parsed}) == len(slots)
    assert {row["row_index"] for row in parsed} == {str(slot.row_index) for slot in slots}
    assert parsed[0]["Report"] == "null"
    assert parsed[0]["Report__status"] == "measured"
    assert '"producer_id":"asaree.agent_output"' in parsed[0]["Report__producer"]
    assert parsed[1]["Report"] == "" and parsed[1]["Report__status"] == "unavailable"
    assert parsed[2]["status"] == "pending"
    assert any(row["status"] == "failed" for row in parsed)
    retry_row = next(row for row in parsed if row["row_result_id"] == str(failed_slot.id))
    assert retry_row["run_id"] == str(retry.id)
    assert json.loads(parsed[3]["Report"]) == quoted_value
    assert json.loads(parsed[4]["Report"]) == opaque_value
    assert schema["consumption_mode"] == "per_row"
    assert schema["row_identity"][-6]["name"] == "row_index"
    assert set(header) == {column["name"] for column in schema["columns"]}

    for endpoint in (export_run_results_csv_endpoint, get_run_results_schema_endpoint):
        with pytest.raises(HTTPException) as foreign:
            await endpoint(
                ctx["experiment"].id,
                ctx["user"],
                db,
                protocol_id=ctx["protocol"].id,
                protocol_revision_id=uuid.uuid4(),
            )
        assert foreign.value.status_code == 404


@pytest.mark.asyncio
async def test_whole_dataset_csv_route_keeps_legacy_layout(row_results_setup) -> None:
    db, ctx = row_results_setup
    ctx["protocol"].graph = {"nodes": [{"id": "agent", "type": "agent", "data": {}}], "edges": []}
    from asaree.services.protocol_revisions import publish_protocol

    await publish_protocol(db, ctx["protocol"], owner_id=ctx["user"].id)
    response = await export_run_results_csv_endpoint(
        ctx["experiment"].id, ctx["user"], db, protocol_id=ctx["protocol"].id
    )
    assert response.body.decode("utf-8").startswith("cell_label,replicate_number,")
    assert "row_result_id" not in response.body.decode("utf-8").splitlines()[0]

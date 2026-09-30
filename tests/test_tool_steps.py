import json

import pytest

from asaree.services import tool_steps
from asaree.services.derived_metrics import (
    collect_derived_observations,
    derived_binding_issue,
    feature_pipeline_values,
    tool_step_values,
)
from asaree.services.measurement_engine import parse_measurement_plan
from asaree.services.tool_steps import (
    canonical_payload_json,
    execute_tool_step,
    sha256_text,
    validate_tool_step,
)

SERVER_ID = "b16f01eb-0000-4000-8000-000000000001"
CODE = "print('model')\n"
ARGUMENTS = {
    "code": {"source": "script_code"},
    "payload_json": {"source": "upstream_payload", "format": "json_string"},
    "workspace_id": {"source": "workspace_id"},
    "random_seed": {"source": "value", "value": 7},
}
HASH_CHECKS = {"code_sha256": "code", "payload_sha256": "payload_json"}


def _graph(*, tool_names=("run_model_script",), with_script=True, arguments=None, hash_checks=None) -> dict:
    nodes = [
        {"id": "gate", "type": "critic_gate", "data": {"config": {}}},
        {
            "id": "mcp",
            "type": "mcp_tool",
            "data": {"config": {"server_id": SERVER_ID, "server_name": "models", "tool_names": list(tool_names)}},
        },
        {
            "id": "step",
            "type": "tool_step",
            "data": {
                "config": {
                    "tool_name": "run_model_script",
                    "arguments": ARGUMENTS if arguments is None else arguments,
                    "hash_checks": HASH_CHECKS if hash_checks is None else hash_checks,
                }
            },
        },
    ]
    edges = [
        {"source": "gate", "target": "step", "targetHandle": None},
        {"source": "mcp", "target": "step", "targetHandle": "tool"},
    ]
    if with_script:
        nodes.append({"id": "script", "type": "script", "data": {"config": {"code": CODE}}})
        edges.append({"source": "script", "target": "step", "targetHandle": "tool"})
    return {"nodes": nodes, "edges": edges}


def _step(graph: dict) -> dict:
    return next(node for node in graph["nodes"] if node["id"] == "step")


def test_validation_requires_the_tool_to_be_enabled_on_the_mcp_node() -> None:
    graph = _graph(tool_names=("other",))
    assert "does not enable" in (validate_tool_step(graph, _step(graph)) or "")
    graph = _graph()
    assert validate_tool_step(graph, _step(graph)) is None


def test_validation_checks_argument_sources_and_hash_checks() -> None:
    graph = _graph(arguments={"x": {"source": "sanitized"}})
    assert "no valid source" in (validate_tool_step(graph, _step(graph)) or "")
    graph = _graph(with_script=False)
    assert "no Script is wired" in (validate_tool_step(graph, _step(graph)) or "")
    graph = _graph(hash_checks={"code_sha256": "missing"})
    assert "not a sent argument" in (validate_tool_step(graph, _step(graph)) or "")
    graph = _graph(arguments={}, hash_checks={}, with_script=False)
    assert validate_tool_step(graph, _step(graph)) is None


def test_validation_names_the_old_config_format() -> None:
    graph = _graph()
    _step(graph)["data"]["config"].update(sanitizer="xgboost_hyperparameters", verify_hashes=True)
    assert "old Tool Step format" in (validate_tool_step(graph, _step(graph)) or "")


def _patch_call(monkeypatch: pytest.MonkeyPatch, respond) -> list[dict]:
    calls: list[dict] = []

    async def call_server_tool(server_id, tool_name, arguments, **_kwargs):
        calls.append({"server_id": str(server_id), "tool_name": tool_name, "arguments": arguments})
        return respond(arguments)

    monkeypatch.setattr(tool_steps.mcp_service, "call_server_tool", call_server_tool)
    return calls


@pytest.mark.asyncio
async def test_tool_step_sends_mapped_arguments_and_records_them_first(monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(arguments):
        return False, json.dumps(
            {
                "code_sha256": sha256_text(arguments["code"]),
                "payload_sha256": sha256_text(arguments["payload_json"]),
                "n_sanitize_notes": 3,
                "test_metrics": {"average_precision": 0.41, "metrics_at_0.5": {"f1": 0.3}},
            }
        )

    calls = _patch_call(monkeypatch, respond)
    recorded: list[dict] = []

    async def record(step):
        assert not calls, "provenance must be recorded before the call"
        recorded.append(step)

    graph = _graph()
    payload = {"search_space": [{"name": "max_depth", "low": 3, "high": 6}]}
    node_run = await execute_tool_step(
        _step(graph),
        graph=graph,
        upstream_runs=[{"status": "completed", "payload": payload}],
        workspace_id="ws-1",
        record_provenance=record,
    )

    assert node_run["status"] == "completed", node_run["error"]
    sent = calls[0]["arguments"]
    assert sent == {
        "code": CODE.strip(),
        "payload_json": canonical_payload_json(payload),
        "workspace_id": "ws-1",
        "random_seed": 7,
    }
    assert recorded[0]["payload"] == payload
    assert recorded[0]["arguments"] == {"workspace_id": "ws-1", "random_seed": 7}
    assert recorded[0]["argument_sha256"]["payload_json"] == sha256_text(sent["payload_json"])
    values = tool_step_values(node_run)
    assert values is not None
    assert values["test_metrics.metrics_at_0.5.f1"] == 0.3
    assert values["n_sanitize_notes"] == 3


@pytest.mark.asyncio
async def test_tool_step_sends_null_when_upstream_has_no_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_call(monkeypatch, lambda arguments: (False, json.dumps({"ok": True})))
    graph = _graph(hash_checks={})
    node_run = await execute_tool_step(
        _step(graph), graph=graph, upstream_runs=[{"output_text": "no json here"}], workspace_id="ws-1"
    )
    assert node_run["status"] == "completed"
    assert calls[0]["arguments"]["payload_json"] == "null"


@pytest.mark.asyncio
async def test_tool_step_fails_when_the_scored_payload_differs(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_call(
        monkeypatch,
        lambda arguments: (
            False,
            json.dumps({"code_sha256": sha256_text(CODE.strip()), "payload_sha256": "tampered"}),
        ),
    )
    graph = _graph()
    node_run = await execute_tool_step(
        _step(graph),
        graph=graph,
        upstream_runs=[{"payload": {"search_space": [], "n_trials": 1}}],
        workspace_id="ws-1",
    )
    assert node_run["status"] == "failed"
    assert "'payload_sha256'" in node_run["error"] and node_run["error"].startswith("Hash check failed")
    assert node_run["tool_step"]["payload"] == {"search_space": [], "n_trials": 1}


def test_feature_pipeline_counts_created_features_against_raw_columns() -> None:
    node_runs = {
        "dc": {"status": "completed", "payload": {"n_cols_out": 10}},
        "fte": {"status": "completed", "payload": {"n_features_out": 14}},
        "fs": {"status": "completed", "payload": {"selected_features": ["num__age", "num__age_x_bmi", "bmi"]}},
    }
    values = feature_pipeline_values(
        node_runs,
        {"dc_node_id": "dc", "fte_node_id": "fte", "fs_node_id": "fs"},
        frozenset({"age", "bmi"}),
    )
    assert values == {
        "n_features_after_dc": 10,
        "n_features_after_fte": 14,
        "n_features_after_fs": 3,
        "n_features_created": 4,
        "n_created_selected": 1,
        "frac_created_selected": pytest.approx(1 / 3),
    }


def _derived_plan(producer_id: str, outputs: dict, config: dict):
    return parse_measurement_plan(
        {
            "metrics": [{"id": metric_id, "name": metric_id} for metric_id in outputs.values()],
            "producers": [
                {
                    "id": "source",
                    "producer_id": producer_id,
                    "kind": "reported",
                    "outputs": outputs,
                    "artifacts": [],
                    "config": config,
                }
            ],
            "inputs": [],
        }
    )


@pytest.mark.asyncio
async def test_tool_step_producer_writes_numbers_and_reports_missing_paths() -> None:
    plan = _derived_plan(
        "asaree.tool_step",
        {"test_metrics.average_precision": "pr_auc", "test_metrics.nan": "nan", "absent": "absent"},
        {"node_id": "step"},
    )
    node_runs = {
        "step": {
            "status": "completed",
            "payload": {"test_metrics": {"average_precision": 0.41, "nan": float("nan")}},
            "tool_step": {},
        }
    }
    observations = await collect_derived_observations(
        plan.producers[0],
        {metric.id: metric for metric in plan.metrics},
        node_runs=node_runs,
        graph=_graph(),
        attempt_id="a1",
    )
    by_id = {observation.metric_id: observation for observation in observations}
    assert by_id["pr_auc"].value == 0.41 and by_id["pr_auc"].status == "measured"
    assert by_id["nan"].status == "unavailable"
    assert by_id["absent"].status == "unavailable"
    assert derived_binding_issue(plan.producers[0], _graph()) is None
    assert derived_binding_issue(plan.producers[0], {"nodes": [], "edges": []})[0] == "tool_step_missing"


def test_graph_validation_rejects_a_misconfigured_tool_step() -> None:
    from asaree.services.protocol_execution import ProtocolValidationError, topological_order

    def with_input(graph: dict) -> dict:
        graph["nodes"][0] = {"id": "gate", "type": "input", "data": {"config": {}}}
        return graph

    ordered = [node["id"] for node in topological_order(with_input(_graph()))]
    assert ordered.index("gate") < ordered.index("step")
    with pytest.raises(ProtocolValidationError, match="does not enable"):
        topological_order(with_input(_graph(tool_names=("other",))))

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
    sanitize_xgboost_payload,
    sha256_text,
    validate_tool_step,
)

SERVER_ID = "b16f01eb-0000-4000-8000-000000000001"
CODE = "print('model')\n"


def _graph(*, tool_names=("run_model_script",), with_script=True) -> dict:
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
                    "sanitizer": "xgboost_hyperparameters",
                    "arguments": {"random_seed": 7},
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


def test_sanitizer_drops_what_the_script_cannot_run() -> None:
    clean, notes = sanitize_xgboost_payload(
        {
            "search_space": [
                {"name": "max_depth", "low": 3, "high": 6},
                {"param": "learning_rate", "low": 0, "high": 0.1},
                {"param": "subsample", "low": 0.6, "high": 0.9},
                {"param": "subsample", "low": 0.7, "high": 0.8},
            ],
            "fixed_params": {"objective": "binary:logistic", "gamma": 1, "subsample": 0.7},
            "n_trials": 500,
        }
    )
    assert clean == {
        "search_space": [{"param": "subsample", "low": 0.6, "high": 0.9}],
        "fixed_params": {"gamma": 1},
        "n_trials": 100,
    }
    assert len(notes) == 6


def test_sanitizer_reduces_trials_when_nothing_is_tuned() -> None:
    clean, notes = sanitize_xgboost_payload({"search_space": [], "n_trials": 20})
    assert clean["n_trials"] == 1
    assert notes


def test_validation_requires_the_tool_to_be_enabled_on_the_mcp_node() -> None:
    graph = _graph(tool_names=("other",))
    step = next(node for node in graph["nodes"] if node["id"] == "step")
    assert "does not enable" in (validate_tool_step(graph, step) or "")
    graph = _graph()
    assert validate_tool_step(graph, next(node for node in graph["nodes"] if node["id"] == "step")) is None


def _patch_call(monkeypatch: pytest.MonkeyPatch, respond) -> list[dict]:
    calls: list[dict] = []

    async def call_server_tool(server_id, tool_name, arguments, **_kwargs):
        calls.append({"server_id": str(server_id), "tool_name": tool_name, "arguments": arguments})
        return respond(arguments)

    monkeypatch.setattr(tool_steps.mcp_service, "call_server_tool", call_server_tool)
    return calls


@pytest.mark.asyncio
async def test_tool_step_sends_the_sanitized_payload_and_records_it_first(monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(arguments):
        return False, json.dumps(
            {
                "code_sha256": sha256_text(arguments["code"].strip()),
                "payload_sha256": sha256_text(arguments["payload_json"]),
                "test_metrics": {"average_precision": 0.41, "metrics_at_0.5": {"f1": 0.3}},
            }
        )

    calls = _patch_call(monkeypatch, respond)
    recorded: list[dict] = []

    async def record(step):
        assert not calls, "provenance must be recorded before the call"
        recorded.append(step)

    graph = _graph()
    upstream = {"status": "completed", "payload": {"search_space": [{"name": "max_depth", "low": 3, "high": 6}]}}
    node_run = await execute_tool_step(
        next(node for node in graph["nodes"] if node["id"] == "step"),
        graph=graph,
        upstream_runs=[upstream],
        workspace_id="ws-1",
        record_provenance=record,
    )

    assert node_run["status"] == "completed", node_run["error"]
    sent = calls[0]["arguments"]
    assert sent["random_seed"] == 7 and sent["workspace_id"] == "ws-1" and sent["code"] == CODE
    assert json.loads(sent["payload_json"]) == {"search_space": [], "fixed_params": {}, "n_trials": 1}
    assert recorded[0]["payload_sha256"] == sha256_text(sent["payload_json"])
    values = tool_step_values(node_run)
    assert values is not None
    assert values["test_metrics.metrics_at_0.5.f1"] == 0.3
    assert values["tool_step.n_sanitize_notes"] == 3


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
        next(node for node in graph["nodes"] if node["id"] == "step"),
        graph=graph,
        upstream_runs=[{"payload": {"search_space": [], "n_trials": 1}}],
        workspace_id=None,
    )
    assert node_run["status"] == "failed"
    assert node_run["error"].startswith("Payload guard failed")
    assert canonical_payload_json(node_run["tool_step"]["payload"]) == canonical_payload_json(
        {"fixed_params": {}, "n_trials": 1, "search_space": []}
    )


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

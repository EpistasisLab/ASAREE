"""Offline checks for the runnable SDK demo; no database or model calls."""

import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from asaree.services.factor_bindings import validate_factor_bindings
from asaree.services.measurement_engine import parse_measurement_plan, validate_measurement_plan_structure
from asaree.services.protocol_execution import sink_node_ids, topological_order, validate_coordination_strategy
from asaree.services.reported_metrics import collect_reported_metrics, validate_reported_measurement_plan
from asaree.services.runtime_metrics import validate_runtime_measurement_plan

DEMO = runpy.run_path(str(Path(__file__).parents[1] / "scripts/create_stats_demo.py"))


def test_demo_scores_known_answers_and_rejects_incomplete_predictions():
    namespace = {}
    exec(DEMO["scoring_source"](), namespace)
    predictions = dict(namespace["ANSWER_KEY"])
    assert namespace["score"](predictions) == {"accuracy": 1, "misclassified_count": 0, "passed": True}
    for ticket in ("T01", "T02", "T03"):
        predictions[ticket] = "account"
    assert namespace["score"](predictions) == {"accuracy": 0.85, "misclassified_count": 3, "passed": True}
    predictions["T04"] = "account"
    assert namespace["score"](predictions)["passed"] is False
    predictions.pop("T01")
    with pytest.raises(ValueError, match="20 expected"):
        namespace["score"](predictions)


def test_demo_graph_bindings_metrics_and_answer_key_isolation():
    definition = DEMO["build_definition"]("openai", "configured-model", 15, "dataset-id", "tickets")
    design, graph = definition["design_spec"], definition["graph"]
    assert design["replicates"] == 15
    assert [len(factor["levels"]) for factor in design["factors"]] == [2, 2]
    validate_factor_bindings(design, graph)
    topological_order(graph)
    validate_coordination_strategy(design, graph=graph)
    assert sink_node_ids(graph) == ["scorer"]
    plan = parse_measurement_plan(definition["measurement_plan"])
    assert validate_measurement_plan_structure(plan).valid
    assert validate_runtime_measurement_plan(definition["measurement_plan"], graph=graph).valid
    assert len(plan.metrics) == 6
    classifier = graph["nodes"][0]
    assert "ANSWER_KEY" not in classifier["data"]["config"]["prompt"]
    assert all(edge["source"] != "dataset" for edge in graph["edges"] if edge["target"] == "classifier")
    assert all(edge["source"] != "script" for edge in graph["edges"] if edge["target"] == "classifier")


def test_demo_dry_run_needs_no_credentials_or_sdk_install(monkeypatch, capsys):
    monkeypatch.delenv("ASAREE_API_KEY", raising=False)
    monkeypatch.delenv("ASAREE_BASE_URL", raising=False)
    monkeypatch.setattr("sys.argv", ["create_stats_demo.py", "--provider", "openai", "--model", "demo", "--dry-run"])
    DEMO["main"]()
    definition = json.loads(capsys.readouterr().out)
    assert len(definition["design_spec"]["metrics"]) == 6
    assert definition["design_spec"]["replicates"] == 15


async def test_demo_custom_fields_are_captured_by_the_real_measurement_path():
    definition = DEMO["build_definition"]("openai", "configured-model", 15, "dataset-id", "tickets")
    plan = parse_measurement_plan(definition["measurement_plan"])
    assert (await validate_reported_measurement_plan(plan, definition["graph"])).valid
    scores = {"accuracy": 0.85, "misclassified_count": 3, "passed": True}
    run = SimpleNamespace(
        id=uuid4(),
        replicate_result_id=None,
        node_runs={"scorer": {"status": "completed", "output_text": json.dumps(scores)}},
    )
    evaluation = await collect_reported_metrics(run, plan, definition["graph"])
    assert {observation.metric_name: observation.value for observation in evaluation.observations} == scores
    assert all(observation.status == "measured" for observation in evaluation.observations)

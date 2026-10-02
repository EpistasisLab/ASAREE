import csv
import io
import json
from pathlib import Path

from asaree.services.csv_export import result_rows_to_csv
from asaree.services.derived_metrics import FEATURE_PIPELINE_OUTPUTS
from asaree.services.measurement_migration import normalize_experiment_measurement_plan
from asaree.services.metrics import (
    design_metrics_from_measurement_plan,
    normalize_metrics,
)
from asaree.services.protocol_execution import topological_order, validate_prompt_references
from asaree.services.runtime_metrics import validate_runtime_measurement_plan

BDM = Path(__file__).parents[1] / "publications" / "BDM"
USE_CASES = sorted(BDM.glob("myocardial-*.json"))


def test_myocardial_use_cases_normalize_reported_metrics_as_named_observations() -> None:
    documents = [json.loads(path.read_text()) for path in USE_CASES]
    expected_metrics = documents[0]["design_spec"]["metrics"]
    expected_plan_metrics = documents[0]["measurement_plan"]["metrics"]

    assert len(USE_CASES) == 3
    for document in documents:
        metrics = document["design_spec"]["metrics"]
        plan = document["measurement_plan"]
        assert metrics == expected_metrics
        assert plan["metrics"] == expected_plan_metrics
        assert normalize_metrics(metrics) == design_metrics_from_measurement_plan(plan)
        normalized_plan = normalize_experiment_measurement_plan(plan, metrics)
        reported = next(
            producer for producer in normalized_plan["producers"] if producer["id"] == "model-evaluation-source"
        )
        reported_metric_ids = set(reported["outputs"].values())
        assert reported["config"]["projections"]["pr_auc"] == {"path": "test_metrics.average_precision"}
        assert all(
            set(metric) <= {"id", "name"}
            for metric in normalized_plan["metrics"]
            if metric["id"] in reported_metric_ids
        )
        assert not any(metric.get("primary") for metric in normalize_metrics(metrics) if metric["kind"] == "custom")

        runtime = next(producer for producer in plan["producers"] if producer["producer_id"] == "asaree.runtime")
        assert set(runtime["outputs"]) == {
            "cost_usd",
            "duration_seconds",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "tool_calls",
            "tool_error_rate",
            "agent_loop_iterations",
            "critic_rejections",
            "critic_approvals",
            "critic_invocations",
            "critic_rejections_partial",
            "critic_rejections_full",
            "revision_rounds",
            "capped_agent_runs",
            "agent_runs",
            "react_runs",
            "react_turns",
            "react_tool_calls",
            "prompt_sha256",
        }
        graph_ids = {node["id"] for node in document["graph"]["nodes"]}
        stages = {
            producer["id"]: producer
            for producer in plan["producers"]
            if producer["producer_id"] == "asaree.node_runtime"
        }
        assert set(stages) == {f"runtime-{stage}" for stage in ("dc", "fte", "fs", "mlm", "critic")}
        assert stages["runtime-critic"]["config"]["node_ids"] == ["gate-dc", "gate-fte", "gate-fs", "gate-mlm"]
        for stage, producer in stages.items():
            assert set(producer["config"]["node_ids"]) <= graph_ids
            assert set(producer["outputs"]) == {"total_tokens", "agent_loop_iterations"}
            assert {"producer_binding_id": stage, "input_key": "facts", "source_key": "attempt.runtime"} in plan[
                "inputs"
            ]
        assert not validate_runtime_measurement_plan(plan, graph=document["graph"]).issues


def test_myocardial_output_parser_types_match_the_prompted_payloads() -> None:
    document = json.loads(USE_CASES[0].read_text())
    parsers = {
        node["id"]: {field["name"]: field["type"] for field in node["data"]["config"]["output_contract"]["fields"]}
        for node in document["graph"]["nodes"]
        if node["type"] == "output_parser"
    }

    assert parsers["output-parser-dc"]["outliers"] == "array"
    assert parsers["output-parser-dc"]["imputation"] == "array"
    assert parsers["output-parser-fte"]["engineering_recipe"] == "array"
    assert parsers["output-parser-fs"]["class_balance_check"] == "string"
    assert "n_engineered_features_selected" not in parsers["output-parser-fs"]
    assert parsers["output-parser-fte"]["encoding_map"] == "array"
    assert parsers["output-parser-mlm"]["search_space"] == "array"
    assert "output-parser-score" not in parsers


def test_myocardial_mlm_receives_only_the_notebooks_fs_brief_fields() -> None:
    for path in USE_CASES:
        graph = json.loads(path.read_text())["graph"]
        validate_prompt_references(graph=graph)
        edge = next(e for e in graph["edges"] if e["source"] == "gate-fs" and e["target"] == "agent-mlm")
        assert edge["data"]["handoff"] == {
            "mode": "selected",
            "fields": [
                {"name": name}
                for name in (
                    "selected_features",
                    "n_features_out",
                    "observed_class_distribution",
                    "class_balance_check",
                    "notes_for_mlm",
                )
            ],
        }
        goal = next(n for n in graph["nodes"] if n["id"] == "agent-mlm")["data"]["config"]["goal"]
        # The edge carries FS's fields; the brief only reaches further back.
        assert "{{node:agent-fs" not in goal
        assert "{{node:agent-fte.engineering_recipe[name]}}" in goal
        assert "{{node:agent-fte.encoding_map[feature, encoding]}}" in goal


def test_myocardial_scoring_is_a_deterministic_tool_step() -> None:
    for path in USE_CASES:
        document = json.loads(path.read_text())
        nodes = {node["id"]: node for node in document["graph"]["nodes"]}
        assert "agent-score" not in nodes
        step = nodes["tool-step-score"]
        assert step["type"] == "tool_step"
        config = step["data"]["config"]
        script = nodes["script-model"]["data"]["config"]["code"]
        compile(script, path.name, "exec")
        assert '"reg_alpha": 0.0' in script
        assert '_fixed.update(hp["fixed_params"])' in script
        assert "sanitizer" not in config
        assert config["arguments"]["selection_metric"] == {"source": "value", "value": "average_precision"}
        assert config["arguments"]["payload_json"] == {"source": "upstream_payload", "format": "json_string"}
        assert config["arguments"]["code"] == {"source": "script_code"}
        assert set(json.loads(config["arguments"]["param_spec_json"]["value"])) == {
            "n_estimators",
            "max_depth",
            "learning_rate",
            "min_child_weight",
            "gamma",
            "subsample",
            "colsample_bytree",
            "reg_lambda",
            "reg_alpha",
        }
        assert config["hash_checks"] == {"code_sha256": "code", "payload_sha256": "payload_json"}
        assert [node["id"] for node in topological_order(document["graph"])][-1] == "tool-step-score"
        assert document["design_spec"]["replicates"] == 10
        assert {
            node["data"]["config"]["max_iterations"] for node in nodes.values() if node["type"] == "pattern_reason_act"
        } == {12}

        producers = {producer["id"]: producer for producer in document["measurement_plan"]["producers"]}
        scoring = producers["model-evaluation-source"]
        assert scoring["producer_id"] == "asaree.tool_step"
        assert scoring["config"]["node_id"] == "tool-step-score"
        projections = scoring["config"]["projections"]
        assert set(projections) == set(scoring["outputs"])
        assert scoring["outputs"]["pr_auc"] == "pr-auc"
        assert projections["pr_auc"] == {"path": "test_metrics.average_precision"}
        assert projections["f1_at_0_5"] == {"path": "test_metrics.metrics_at_0.5.f1"}
        assert projections["n_schema_violations"] == {"path": "n_sanitize_notes"}
        pipeline = producers["feature-pipeline-source"]
        assert pipeline["producer_id"] == "asaree.feature_pipeline"
        assert set(pipeline["outputs"]) == set(FEATURE_PIPELINE_OUTPUTS)
        bound = [metric_id for producer in producers.values() for metric_id in producer["outputs"].values()]
        assert len(bound) == len(set(bound)), "each metric has exactly one producer"


def test_myocardial_results_export_has_analysis_ready_custom_columns() -> None:
    document = json.loads(USE_CASES[0].read_text())
    csv_text = result_rows_to_csv(
        [
            {
                "cell_label": "cell-1",
                "replicate_number": 1,
                "factor_values": {"Critic enabled": True},
                "metric_values": {},
            }
        ],
        design_spec=document["design_spec"],
    )

    header = next(csv.reader(io.StringIO(csv_text)))
    assert {
        "pr_auc",
        "roc_auc",
        "brier_score",
        "n_pred_pos_at_0_5",
        "n_features_after_dc",
        "n_engineered_features",
        "n_features_after_fs",
        "n_engineered_features_selected",
        "n_features_created",
        "frac_created_selected",
        "n_schema_violations",
        "optuna_best_inner_cv_score",
        "hp_n_estimators",
        "code_sha256",
        "payload_sha256",
        "data_sha256",
    } <= set(header)

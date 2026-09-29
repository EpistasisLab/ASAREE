import csv
import io
import json
from pathlib import Path

from asaree.services.csv_export import result_rows_to_csv
from asaree.services.measurement_migration import normalize_experiment_measurement_plan
from asaree.services.metrics import (
    design_metrics_from_measurement_plan,
    normalize_metrics,
)

BDM = Path(__file__).parents[1] / "publications" / "BDM"
USE_CASES = sorted(BDM.glob("myocardial-*-v0.8.0.json"))


def test_myocardial_use_cases_declare_the_same_analysis_ready_measurements() -> None:
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
        assert normalize_experiment_measurement_plan(plan, metrics) == plan

        primary = [metric for metric in metrics if metric["primary"]]
        assert [(metric["name"], metric["direction"]) for metric in primary] == [("pr_auc", "maximize")]
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
        }
        score = next(producer for producer in plan["producers"] if producer["id"] == "model-evaluation-source")
        assert score["config"]["projections"]["pr_auc"] == {"path": "test_metrics.average_precision"}
        assert score["config"]["projections"]["accuracy_at_0_5"] == {
            "path": "/test_metrics/metrics_at_0.5/accuracy"
        }


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
    assert parsers["output-parser-fs"]["n_engineered_features_selected"] == "integer"
    assert parsers["output-parser-mlm"]["search_space"] == "array"
    assert "model_decisions" in parsers["output-parser-score"]
    assert "result" not in parsers["output-parser-score"]


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
        "pct_engineered_features_selected",
        "optuna_best_inner_cv_score",
        "hp_n_estimators",
        "code_sha256",
        "payload_sha256",
        "data_sha256",
    } <= set(header)

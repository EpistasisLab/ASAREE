"""Pure-logic tests for services.metric_promotion -- the flattening/extraction
that turns a run_model_script response into FactorialReplicateResult.metric_values.
No DB fixtures exist anywhere else in this suite (every other test file is
pure/unit-level); promote_cell_score_metrics/promote_experiment_score_metrics
(the two DB-touching orchestration functions) are verified manually against a
real experiment instead, the same way the rest of this codebase's DB-touching
service functions are."""

from __future__ import annotations

from types import SimpleNamespace

from asaree.services import metric_promotion as mp


def _step(tool_call: dict | None) -> SimpleNamespace:
    return SimpleNamespace(tool_call=tool_call)


# --- extract_score_metrics ---------------------------------------------------


def test_extract_score_metrics_flattens_top_level_and_chosen_threshold() -> None:
    # Shape matches a real run_model_script response (see the spinal
    # experiment's own factorial_replicate_results.metric_values).
    tool_result = {
        "test_metrics": {
            "roc_auc": 0.7555,
            "average_precision": 0.5361,
            "brier_score": 0.1645,
            "metrics_at_0.5": {"f1": 0.411, "accuracy": 0.7689, "balanced_accuracy": 0.621},
            "metrics_at_chosen_threshold": {
                "f1": 0.5453,
                "accuracy": 0.6592,
                "balanced_accuracy": 0.6873,
                "threshold": 0.2773,
            },
        },
        "code_sha256": "abc123",
        "payload_sha256": "def456",
    }
    assert mp.extract_score_metrics(tool_result) == {
        "average_precision": 0.5361,
        "roc_auc": 0.7555,
        "f1": 0.5453,
        "balanced_accuracy": 0.6873,
        "accuracy": 0.6592,
    }


def test_extract_score_metrics_ignores_metrics_at_0_5_never_uses_it() -> None:
    # Regression test: f1/balanced_accuracy/accuracy must come from
    # metrics_at_chosen_threshold (the train-only-selected operating point),
    # never metrics_at_0.5 -- using a test-tuned or fixed 0.5 threshold here
    # would violate the stats brief's own threshold-discipline rule.
    tool_result = {
        "test_metrics": {
            "roc_auc": 0.7,
            "average_precision": 0.5,
            "metrics_at_0.5": {"f1": 0.999, "accuracy": 0.999, "balanced_accuracy": 0.999},
            "metrics_at_chosen_threshold": {"f1": 0.4, "accuracy": 0.6, "balanced_accuracy": 0.65},
        }
    }
    metrics = mp.extract_score_metrics(tool_result)
    assert metrics["f1"] == 0.4
    assert metrics["accuracy"] == 0.6
    assert metrics["balanced_accuracy"] == 0.65


def test_extract_score_metrics_none_when_no_test_metrics() -> None:
    # An uninitialized-workspace (or rejected-payload) response has only
    # "error"/"code_sha256" -- never test_metrics.
    tool_result = {"error": "workspace: workspace '...' not initialized.", "code_sha256": "abc123"}
    assert mp.extract_score_metrics(tool_result) is None


def test_extract_score_metrics_partial_when_chosen_threshold_missing() -> None:
    # Top-level metrics still extracted even if metrics_at_chosen_threshold
    # is absent for some reason -- partial data beats none.
    tool_result = {"test_metrics": {"roc_auc": 0.8, "average_precision": 0.6}}
    assert mp.extract_score_metrics(tool_result) == {"average_precision": 0.6, "roc_auc": 0.8}


def test_extract_score_metrics_none_when_test_metrics_not_a_dict() -> None:
    assert mp.extract_score_metrics({"test_metrics": "not a dict"}) is None


# --- find_score_tool_result ---------------------------------------------------


def test_find_score_tool_result_returns_last_successful_call() -> None:
    steps = [
        _step(None),  # a sense/reason step with no tool_call at all
        _step({"tool": "run_model_script", "success": False, "result": '{"error": "transient"}'}),
        _step({"tool": "run_model_script", "success": True, "result": '{"test_metrics": {"roc_auc": 0.1}}'}),
        _step({"tool": "run_model_script", "success": True, "result": '{"test_metrics": {"roc_auc": 0.9}}'}),
    ]
    result = mp.find_score_tool_result(steps)
    assert result == {"test_metrics": {"roc_auc": 0.9}}


def test_find_score_tool_result_ignores_other_tools() -> None:
    steps = [
        _step({"tool": "open_workspace", "success": True, "result": '{"workspace_id": "x"}'}),
    ]
    assert mp.find_score_tool_result(steps) is None


def test_find_score_tool_result_none_when_never_successful() -> None:
    steps = [
        _step({"tool": "run_model_script", "success": False, "result": '{"error": "not initialized"}'}),
    ]
    assert mp.find_score_tool_result(steps) is None


def test_find_score_tool_result_skips_unparseable_result() -> None:
    steps = [
        _step({"tool": "run_model_script", "success": True, "result": "not json"}),
    ]
    assert mp.find_score_tool_result(steps) is None

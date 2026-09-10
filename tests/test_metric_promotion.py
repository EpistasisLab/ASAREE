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


# --- align_to_declared_metrics -----------------------------------------------


def _declaring(*names: str) -> dict:
    return {"metrics": [{"name": name} for name in names]}


def test_a_declared_metric_claims_the_promoted_key_it_only_differs_from_by_case() -> None:
    aligned = mp.align_to_declared_metrics({"accuracy": 0.81, "roc_auc": 0.9}, _declaring("Accuracy"))
    assert aligned == {"Accuracy": 0.81, "roc_auc": 0.9}


def test_a_different_name_is_not_guessed_at() -> None:
    # "AUC" is plainly the experimenter's word for roc_auc, and this function
    # still leaves it alone: only case is reconciled, because anything wider
    # would be inventing a mapping they never wrote down.
    assert mp.align_to_declared_metrics({"roc_auc": 0.9}, _declaring("AUC")) == {"roc_auc": 0.9}


def test_an_exactly_matching_key_is_never_displaced_by_a_cased_twin() -> None:
    metrics = {"accuracy": 0.81, "Accuracy": 0.42}
    assert mp.align_to_declared_metrics(metrics, _declaring("Accuracy")) == metrics


def test_nothing_declared_leaves_the_promoted_keys_exactly_as_reported() -> None:
    metrics = {"accuracy": 0.81}
    assert mp.align_to_declared_metrics(metrics, None) == metrics
    assert mp.align_to_declared_metrics(metrics, {}) == metrics
    assert mp.align_to_declared_metrics(metrics, {"metrics": "not a list"}) == metrics


def test_a_nameless_declaration_is_skipped_rather_than_crashing() -> None:
    spec = {"metrics": [{"direction": "maximize"}, "not a dict", {"name": "  "}, {"name": " Accuracy "}]}
    assert mp.align_to_declared_metrics({"accuracy": 0.81}, spec) == {"Accuracy": 0.81}


def test_a_catalog_key_states_the_mapping_a_casefold_could_never_reach() -> None:
    # Picking the "ROC AUC" catalog entry is the experimenter writing the
    # mapping down, which is exactly what test_a_different_name_is_not_guessed_at
    # says a bare name cannot do.
    spec = {"metrics": [{"name": "ROC AUC", "catalogKey": "roc_auc"}]}
    assert mp.align_to_declared_metrics({"roc_auc": 0.9}, spec) == {"ROC AUC": 0.9}


def test_a_catalog_key_wins_over_another_metrics_cased_name() -> None:
    spec = {
        "metrics": [
            {"name": "F1", "catalogKey": "average_precision"},  # deliberately crossed
            {"name": "f1"},
        ]
    }
    assert mp.align_to_declared_metrics({"average_precision": 0.4}, spec) == {"F1": 0.4}


def test_an_unrecognized_catalog_key_falls_back_to_the_name() -> None:
    spec = {"metrics": [{"name": "Accuracy", "catalogKey": "not_a_real_key"}]}
    assert mp.align_to_declared_metrics({"accuracy": 0.81}, spec) == {"Accuracy": 0.81}


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

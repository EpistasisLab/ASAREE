import json

import numpy as np
import pandas as pd
import pytest
from asaree_sklearn_core.hyperparams import parse_param_spec, sanitize_hyperparameter_payload
from asaree_sklearn_model import server

SPEC = {
    "max_depth": {"low": 3, "high": 10},
    "learning_rate": {"low": 1e-3, "high": 0.3},
    "gamma": {"low": 0, "high": 5},
    "subsample": {"low": 0.5, "high": 1.0},
}
SCRIPT = """
chosen_threshold = 0.5
def predict_proba(X):
    return np.full(len(X), 0.5)
result = {"hp_seen": hp}
"""


def test_sanitizer_drops_what_the_script_cannot_run() -> None:
    clean, notes = sanitize_hyperparameter_payload(
        {
            "search_space": [
                {"name": "max_depth", "low": 3, "high": 6},
                {"param": "learning_rate", "low": 0, "high": 0.1},
                {"param": "subsample", "low": 0.6, "high": 0.9},
                {"param": "subsample", "low": 0.7, "high": 0.8},
            ],
            "fixed_params": {"objective": "binary:logistic", "gamma": 1, "subsample": 0.7},
            "n_trials": 500,
        },
        SPEC,
    )
    assert clean == {
        "search_space": [{"param": "subsample", "low": 0.6, "high": 0.9}],
        "fixed_params": {"gamma": 1},
        "n_trials": 100,
    }
    assert len(notes) == 6


def test_sanitizer_reduces_trials_when_nothing_is_tuned() -> None:
    clean, notes = sanitize_hyperparameter_payload({"search_space": [], "n_trials": 20}, SPEC)
    assert clean["n_trials"] == 1
    assert notes


def test_param_spec_must_have_numeric_bounds() -> None:
    with pytest.raises(ValueError, match="numeric 'low' and 'high'"):
        parse_param_spec(json.dumps({"max_depth": {"low": "3"}}))


@pytest.fixture
def workspace(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    rng = np.random.default_rng(0)
    frames = {}
    for split, n in (("train", 40), ("test", 20)):
        frame = pd.DataFrame({"a": rng.normal(size=n), "y": [0, 1] * (n // 2)})
        frames[split] = tmp_path / f"{split}.parquet"
        frame.to_parquet(frames[split])
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "state.json").write_text(
        json.dumps(
            {
                "target_column": "y",
                "head": "v0",
                "versions": [{"id": "v0", "train": str(frames["train"]), "test": str(frames["test"])}],
            }
        )
    )
    monkeypatch.setenv("ASAREE_DATASET_WORKSPACE_DIR", str(tmp_path))
    return "ws"


def test_run_model_script_sanitizes_against_the_given_spec(workspace: str) -> None:
    payload_json = json.dumps({"search_space": [{"name": "max_depth", "low": 3, "high": 6}], "n_trials": 5})
    result = json.loads(
        server.run_model_script(
            code=SCRIPT,
            payload_json=payload_json,
            workspace_id=workspace,
            param_spec_json=json.dumps(SPEC),
        )
    )
    assert "error" not in result, result.get("error")
    bound = {"search_space": [], "fixed_params": {}, "n_trials": 1}
    assert result["sanitized_payload"] == bound
    assert result["model_decisions"]["hp_seen"] == bound
    assert result["n_sanitize_notes"] == 2
    assert result["payload_sha256"] == server.hashlib.sha256(payload_json.encode()).hexdigest()


def test_run_model_script_scores_defaults_for_a_null_payload_with_a_spec(workspace: str) -> None:
    result = json.loads(
        server.run_model_script(
            code=SCRIPT, payload_json="null", workspace_id=workspace, param_spec_json=json.dumps(SPEC)
        )
    )
    assert "error" not in result, result.get("error")
    assert result["n_sanitize_notes"] == 1
    assert result["sanitized_payload"]["n_trials"] == server.DEFAULT_N_TRIALS


def test_run_model_script_without_a_spec_binds_the_payload_unchanged(workspace: str) -> None:
    result = json.loads(
        server.run_model_script(code=SCRIPT, payload_json='{"anything": 1}', workspace_id=workspace)
    )
    assert "error" not in result, result.get("error")
    assert result["model_decisions"]["hp_seen"] == {"anything": 1}
    assert "n_sanitize_notes" not in result

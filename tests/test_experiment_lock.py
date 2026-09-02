from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from asaree.api.experiments import _locked_design_change_is_replicates_only, _reject_locked_mutation


def test_locked_design_allows_only_replicate_count_change() -> None:
    current = {
        "factors": [{"name": "model", "levels": ["a", "b"]}],
        "replicates": 1,
        "metrics": [{"name": "quality", "primary": True, "direction": "maximize"}],
    }
    proposed = {**current, "replicates": 3}

    assert _locked_design_change_is_replicates_only(current, proposed)


def test_locked_design_rejects_other_design_changes() -> None:
    current = {"factors": [{"name": "model", "levels": ["a", "b"]}], "replicates": 1}
    proposed = {"factors": [{"name": "model", "levels": ["a", "c"]}], "replicates": 1}

    assert not _locked_design_change_is_replicates_only(current, proposed)


def test_locked_experiment_rejects_non_replicate_mutations() -> None:
    experiment = SimpleNamespace(
        locked_at=datetime.now(UTC),
        design_spec={"factors": [{"name": "model", "levels": ["a", "b"]}], "replicates": 1},
    )

    _reject_locked_mutation(experiment, {"design_spec": {**experiment.design_spec, "replicates": 2}})

    with pytest.raises(HTTPException) as exc_info:
        _reject_locked_mutation(experiment, {"hypothesis": "a different hypothesis"})

    assert exc_info.value.status_code == 409

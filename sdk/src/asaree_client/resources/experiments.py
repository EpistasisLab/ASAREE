"""Experiment and factorial-cell resource, matching asaree.api.experiments.

``upsert_cell`` is the replacement for the source notebook's two
``client.runs.update(mlm_run_id, metadata={...})`` calls (pre-scoring and
post-scoring) — both become calls to this, merged onto the same
``FactorialCellResult`` row rather than the run.
"""

from __future__ import annotations

import builtins
import uuid
from typing import Any

from asaree_client.models import Cell, Experiment, ExperimentArtifact

ResourceId = uuid.UUID | str
# Distinguishes "omit this kwarg" (leave unchanged) from "pass None"
# (explicitly clear/detach) in update() below -- a plain default of None
# can't tell those apart.
_UNSET: Any = object()


class Experiments:
    def __init__(self, client: Any) -> None:
        self._client = client

    def create(
        self,
        *,
        name: str,
        description: str | None = None,
        design_type: str = "factorial",
        task_brief: dict[str, Any] | None = None,
        factors: builtins.list[dict[str, Any]] | None = None,
    ) -> Experiment:
        payload: dict[str, Any] = {"name": name, "design_type": design_type}
        if description is not None:
            payload["description"] = description
        if task_brief is not None:
            payload["task_brief"] = task_brief
        if factors is not None:
            payload["factors"] = factors
        data = self._client._post("/experiments", json=payload)
        return Experiment(**data)

    def get(self, experiment_id: ResourceId) -> Experiment:
        data = self._client._get(f"/experiments/{experiment_id}")
        return Experiment(**data)

    def list(self) -> builtins.list[Experiment]:
        data = self._client._get("/experiments")
        return [Experiment(**e) for e in data]

    def delete(self, experiment_id: ResourceId) -> None:
        self._client._delete(f"/experiments/{experiment_id}")

    def update(
        self,
        experiment_id: ResourceId,
        *,
        name: str | None = _UNSET,
        description: str | None = _UNSET,
        dataset_id: ResourceId | None = _UNSET,
    ) -> Experiment:
        """Only the fields actually passed are sent (omit one to leave it
        unchanged; pass ``None`` explicitly to clear/detach it) --
        ``dataset_id=None`` attaches nothing / detaches, same as before;
        ``name``/``description`` are new, for renaming an experiment created
        with a placeholder name straight from the GUI."""
        payload: dict[str, Any] = {}
        if name is not _UNSET:
            payload["name"] = name
        if description is not _UNSET:
            payload["description"] = description
        if dataset_id is not _UNSET:
            payload["dataset_id"] = str(dataset_id) if dataset_id else None
        data = self._client._patch(f"/experiments/{experiment_id}", json=payload)
        return Experiment(**data)

    def generate_design(self, experiment_id: ResourceId) -> builtins.list[Cell]:
        """Materialize one cell per combination of the experiment's declared
        factors. Safe to call again after widening a factor's levels."""
        data = self._client._post(f"/experiments/{experiment_id}/generate-design")
        return [Cell(**c) for c in data]

    def upsert_cell(
        self,
        experiment_id: ResourceId,
        cell_label: str,
        *,
        run_id: ResourceId | None = None,
        workspace_id: str | None = None,
        factor_values: dict[str, Any] | None = None,
        metric_values: dict[str, Any] | None = None,
        artifacts: dict[str, Any] | None = None,
    ) -> Cell:
        """Merge fields onto a cell's row — pass just what changed; unset
        fields are left untouched (a pre-scoring call and a post-scoring
        call land on the same row without either erasing the other)."""
        payload: dict[str, Any] = {}
        if run_id is not None:
            payload["run_id"] = str(run_id)
        if workspace_id is not None:
            payload["workspace_id"] = workspace_id
        if factor_values is not None:
            payload["factor_values"] = factor_values
        if metric_values is not None:
            payload["metric_values"] = metric_values
        if artifacts is not None:
            payload["artifacts"] = artifacts
        data = self._client._put(f"/experiments/{experiment_id}/cells/{cell_label}", json=payload)
        return Cell(**data)

    def get_cell(self, experiment_id: ResourceId, cell_label: str) -> Cell:
        data = self._client._get(f"/experiments/{experiment_id}/cells/{cell_label}")
        return Cell(**data)

    def list_cells(self, experiment_id: ResourceId) -> builtins.list[Cell]:
        data = self._client._get(f"/experiments/{experiment_id}/cells")
        return [Cell(**c) for c in data]

    def analyze(
        self,
        experiment_id: ResourceId,
        *,
        condition_factors: builtins.list[str],
        positive_levels: dict[str, Any],
        reference_condition: dict[str, Any],
        primary_metric: str,
        alpha: float = 0.05,
        delta: float = 0.05,
        n_resamples: int = 10_000,
        seed: int = 42,
        failure_flag_key: str = "failure_flag",
        cost_keys: builtins.list[str] | None = None,
    ) -> dict[str, Any]:
        """Run the spinal_surgery use case's specific statistical methodology
        (Freedman-Lane + max-stat FWER, BCa non-inferiority + Holm) against
        this experiment's current cells. Not the generic nonparametric-
        regression capability tracked separately (ASAREE#1)."""
        payload: dict[str, Any] = {
            "condition_factors": condition_factors,
            "positive_levels": positive_levels,
            "reference_condition": reference_condition,
            "primary_metric": primary_metric,
            "alpha": alpha,
            "delta": delta,
            "n_resamples": n_resamples,
            "seed": seed,
            "failure_flag_key": failure_flag_key,
        }
        if cost_keys is not None:
            payload["cost_keys"] = cost_keys
        return self._client._post(f"/experiments/{experiment_id}/analyze", json=payload)  # type: ignore[no-any-return]

    def create_artifact(
        self, experiment_id: ResourceId, *, name: str, kind: str, content: str
    ) -> ExperimentArtifact:
        """A durable landing spot for anything worth keeping past one run --
        e.g. ``analyze(...)``'s own result (``kind="analyze_result"``,
        ``content=json.dumps(result)``) or a flattened CSV export
        (``kind="csv_export"``), in place of writing either to local disk.
        Create-once/append-style: calling this again with the same ``name``
        creates a NEW row, it never overwrites the last one."""
        data = self._client._post(
            f"/experiments/{experiment_id}/artifacts", json={"name": name, "kind": kind, "content": content}
        )
        return ExperimentArtifact(**data)

    def list_artifacts(self, experiment_id: ResourceId) -> builtins.list[ExperimentArtifact]:
        data = self._client._get(f"/experiments/{experiment_id}/artifacts")
        return [ExperimentArtifact(**a) for a in data]

    def get_artifact(self, experiment_id: ResourceId, artifact_id: ResourceId) -> ExperimentArtifact:
        data = self._client._get(f"/experiments/{experiment_id}/artifacts/{artifact_id}")
        return ExperimentArtifact(**data)

    def delete_artifact(self, experiment_id: ResourceId, artifact_id: ResourceId) -> None:
        self._client._delete(f"/experiments/{experiment_id}/artifacts/{artifact_id}")

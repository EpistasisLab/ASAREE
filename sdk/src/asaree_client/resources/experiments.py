"""Experiment, factorial-cell, and replicate resources.

``upsert_replicate`` is the replacement for the source notebook's two
``client.runs.update(mlm_run_id, metadata={...})`` calls (pre-scoring and
post-scoring) — both become calls to this, merged onto the same
replicate-result row rather than the run.
"""

from __future__ import annotations

import builtins
import uuid
from datetime import datetime
from typing import Any

from asaree_client._sentinel import UNSET, UnsetType
from asaree_client.models import (
    DesignImpact,
    DesignRevision,
    Experiment,
    ExperimentArtifact,
    ExperimentResults,
    ExperimentRunResults,
    MeasurementCapabilities,
    MeasurementPlanValidation,
    Replicate,
    Trial,
)

ResourceId = uuid.UUID | str


class Experiments:
    def __init__(self, client: Any) -> None:
        self._client = client

    def create(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        design_type: str = "factorial",
        task_brief: dict[str, Any] | None = None,
        factors: builtins.list[dict[str, Any]] | None = None,
        measurement_plan: dict[str, Any] | None = None,
    ) -> Experiment:
        """Create an experiment and its linked empty protocol canvas atomically."""
        payload: dict[str, Any] = {"design_type": design_type}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if task_brief is not None:
            payload["task_brief"] = task_brief
        if factors is not None:
            payload["factors"] = factors
        if measurement_plan is not None:
            payload["measurement_plan"] = measurement_plan
        data = self._client._post("/experiments", json=payload)
        return Experiment(**data)

    def get(self, experiment_id: ResourceId) -> Experiment:
        data = self._client._get(f"/experiments/{experiment_id}")
        return Experiment(**data)

    def list(self, *, include_archived: bool = False) -> builtins.list[Experiment]:
        params = {"include_archived": True} if include_archived else None
        data = self._client._get("/experiments", params=params)
        return [Experiment(**e) for e in data]

    def import_definition(
        self,
        *,
        name: str,
        graph: dict[str, Any],
        description: str | None = None,
        hypothesis: str | None = None,
        design_type: str = "factorial",
        task_brief: dict[str, Any] | None = None,
        design_spec: dict[str, Any] | None = None,
        measurement_plan: dict[str, Any] | None = None,
        published_graph: dict[str, Any] | None = None,
        protocol_description: str | None = None,
    ) -> Experiment:
        payload: dict[str, Any] = {"name": name, "graph": graph, "design_type": design_type}
        for key, value in {
            "description": description,
            "hypothesis": hypothesis,
            "task_brief": task_brief,
            "design_spec": design_spec,
            "measurement_plan": measurement_plan,
            "published_graph": published_graph,
            "protocol_description": protocol_description,
        }.items():
            if value is not None:
                payload[key] = value
        data = self._client._post("/experiments/import-definition", json=payload)
        return Experiment(**data)

    def delete(self, experiment_id: ResourceId) -> None:
        self._client._delete(f"/experiments/{experiment_id}")

    def update(
        self,
        experiment_id: ResourceId,
        *,
        name: str | None | UnsetType = UNSET,
        description: str | None | UnsetType = UNSET,
        hypothesis: str | None | UnsetType = UNSET,
        dataset_ids: builtins.list[ResourceId] | None | UnsetType = UNSET,
        dataset_id: ResourceId | None | UnsetType = UNSET,
        design_spec: dict[str, Any] | None | UnsetType = UNSET,
        measurement_plan: dict[str, Any] | None | UnsetType = UNSET,
        measurement_validation_protocol_id: ResourceId | None | UnsetType = UNSET,
        metric_recommendations: dict[str, Any] | None | UnsetType = UNSET,
        archived_at: datetime | None | UnsetType = UNSET,
    ) -> Experiment:
        """Only the fields actually passed are sent (omit one to leave it
        unchanged; pass ``None`` explicitly to clear/detach it) --
        ``dataset_ids`` replaces the whole attached list (``[]`` detaches
        everything) and ``dataset_id`` is the one-dataset shorthand, with
        ``dataset_id=None`` attaching nothing / detaching, same as before;
        ``name``/``description``/``hypothesis`` are for editing an
        experiment's own identity/write-up straight from the SDK (matching
        the GUI's Rename/Edit description/Design-tab hypothesis fields).
        ``design_spec`` is a full replacement, not a merge -- the same
        factors/replicates/randomization_seed/metrics/coordination_strategy
        dict the Design tab renders; read the current value with ``get()``
        first if you only want to change one key. ``archived_at`` archives
        (pass a datetime) or unarchives (pass ``None`` explicitly) -- the
        GUI's own Archive/Unarchive action, and the only way end users are
        meant to remove an experiment from their active list (the GUI no
        longer exposes ``delete()`` at all, to prevent accidental data
        loss; it's still here for scripted cleanup)."""
        payload: dict[str, Any] = {}
        if not isinstance(name, UnsetType):
            payload["name"] = name
        if not isinstance(description, UnsetType):
            payload["description"] = description
        if not isinstance(hypothesis, UnsetType):
            payload["hypothesis"] = hypothesis
        if not isinstance(dataset_ids, UnsetType):
            payload["dataset_ids"] = [str(d) for d in dataset_ids] if dataset_ids else []
        if not isinstance(dataset_id, UnsetType):
            payload["dataset_id"] = str(dataset_id) if dataset_id else None
        if not isinstance(design_spec, UnsetType):
            payload["design_spec"] = design_spec
        if not isinstance(measurement_plan, UnsetType):
            payload["measurement_plan"] = measurement_plan
        if not isinstance(measurement_validation_protocol_id, UnsetType):
            payload["measurement_validation_protocol_id"] = (
                str(measurement_validation_protocol_id) if measurement_validation_protocol_id else None
            )
        if not isinstance(metric_recommendations, UnsetType):
            payload["metric_recommendations"] = metric_recommendations
        if not isinstance(archived_at, UnsetType):
            payload["archived_at"] = archived_at.isoformat() if archived_at else None
        data = self._client._patch(f"/experiments/{experiment_id}", json=payload)
        return Experiment(**data)

    def generate_design(
        self,
        experiment_id: ResourceId,
        *,
        hypothesis: str | None | UnsetType = UNSET,
        design_spec: dict[str, Any] | None | UnsetType = UNSET,
        measurement_plan: dict[str, Any] | None | UnsetType = UNSET,
        measurement_validation_protocol_id: ResourceId | None | UnsetType = UNSET,
    ) -> builtins.list[Replicate]:
        """Materialize one cell per combination of the experiment's declared
        factors and their replicate results, returning the current replicates.

        Safe to call again after changing the factors. If the new design
        produces a different set of cells than the current one, the current
        design revision is superseded and a new one opened: results for cells
        that survive the change carry forward, and the rest stay behind in the
        superseded revision as history (see ``list_design_revisions``). Nothing
        is deleted, and the returned list is always exactly the new design.
        """
        payload: dict[str, Any] = {}
        for key, value in {
            "hypothesis": hypothesis,
            "design_spec": design_spec,
            "measurement_plan": measurement_plan,
        }.items():
            if not isinstance(value, UnsetType):
                payload[key] = value
        if not isinstance(measurement_validation_protocol_id, UnsetType):
            payload["measurement_validation_protocol_id"] = (
                str(measurement_validation_protocol_id) if measurement_validation_protocol_id else None
            )
        kwargs = {"json": payload} if payload else {}
        data = self._client._post(f"/experiments/{experiment_id}/generate-design", **kwargs)
        return [Replicate(**replicate) for replicate in data]

    def get_design_impact(self, experiment_id: ResourceId) -> DesignImpact:
        data = self._client._get(f"/experiments/{experiment_id}/design-impact")
        return DesignImpact(**data)

    def validate_measurement_plan(
        self,
        experiment_id: ResourceId,
        *,
        measurement_plan: dict[str, Any] | None,
        metrics: builtins.list[dict[str, Any]],
        graph: dict[str, Any],
    ) -> MeasurementPlanValidation:
        data = self._client._post(
            f"/experiments/{experiment_id}/measurement-plan/validate",
            json={"measurement_plan": measurement_plan, "metrics": metrics, "graph": graph},
        )
        return MeasurementPlanValidation(**data)

    def get_measurement_capabilities(self, experiment_id: ResourceId) -> MeasurementCapabilities:
        data = self._client._get(f"/experiments/{experiment_id}/measurement-capabilities")
        return MeasurementCapabilities(**data)

    def lock(self, experiment_id: ResourceId) -> Experiment:
        data = self._client._post(f"/experiments/{experiment_id}/lock")
        return Experiment(**data)

    def unlock(self, experiment_id: ResourceId) -> Experiment:
        data = self._client._post(f"/experiments/{experiment_id}/unlock")
        return Experiment(**data)

    def list_design_revisions(self, experiment_id: ResourceId) -> builtins.list[DesignRevision]:
        """Every generation of this experiment's design, newest first. The
        first entry (``superseded_at is None``) is the current design."""
        data = self._client._get(f"/experiments/{experiment_id}/design-revisions")
        return [DesignRevision(**r) for r in data]

    def delete_design_revision(self, experiment_id: ResourceId, revision_id: ResourceId) -> None:
        """Permanently delete a superseded design revision and all of its
        cells, including any results scored in them. Refuses (409) on the
        current revision -- regenerate the design to replace that instead."""
        self._client._delete(f"/experiments/{experiment_id}/design-revisions/{revision_id}")

    def upsert_replicate(
        self,
        experiment_id: ResourceId,
        replicate_label: str,
        *,
        run_id: ResourceId | None | UnsetType = UNSET,
        workspace_id: str | None | UnsetType = UNSET,
        factor_values: dict[str, Any] | None | UnsetType = UNSET,
        metric_values: dict[str, Any] | None | UnsetType = UNSET,
        artifacts: dict[str, Any] | None | UnsetType = UNSET,
    ) -> Replicate:
        """Merge fields onto a replicate-result row — pass just what changed; unset
        fields are left untouched (a pre-scoring call and a post-scoring
        call land on the same row without either erasing the other)."""
        payload: dict[str, Any] = {}
        if not isinstance(run_id, UnsetType):
            payload["run_id"] = str(run_id) if run_id else None
        if not isinstance(workspace_id, UnsetType):
            payload["workspace_id"] = workspace_id
        if not isinstance(factor_values, UnsetType):
            payload["factor_values"] = factor_values
        if not isinstance(metric_values, UnsetType):
            payload["metric_values"] = metric_values
        if not isinstance(artifacts, UnsetType):
            payload["artifacts"] = artifacts
        data = self._client._put(f"/experiments/{experiment_id}/replicates/{replicate_label}", json=payload)
        return Replicate(**data)

    def get_replicate(self, experiment_id: ResourceId, replicate_label: str) -> Replicate:
        data = self._client._get(f"/experiments/{experiment_id}/replicates/{replicate_label}")
        return Replicate(**data)

    def list_replicates(
        self, experiment_id: ResourceId, *, revision_id: ResourceId | None = None
    ) -> builtins.list[Replicate]:
        """The current design's replicate results, optionally from a superseded revision."""
        params = {"revision_id": str(revision_id)} if revision_id is not None else None
        data = self._client._get(f"/experiments/{experiment_id}/replicates", params=params)
        return [Replicate(**replicate) for replicate in data]

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

    def create_artifact(self, experiment_id: ResourceId, *, name: str, kind: str, content: str) -> ExperimentArtifact:
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

    def list_trials(self, experiment_id: ResourceId) -> builtins.list[Trial]:
        data = self._client._get(f"/experiments/{experiment_id}/runs")
        return [Trial(**trial) for trial in data]

    def get_results(self, experiment_id: ResourceId) -> ExperimentResults:
        return ExperimentResults(**self._client._get(f"/experiments/{experiment_id}/results"))

    def get_run_results(self, experiment_id: ResourceId) -> ExperimentRunResults:
        return ExperimentRunResults(**self._client._get(f"/experiments/{experiment_id}/run-results"))

    def get_run_results_schema(self, experiment_id: ResourceId) -> dict[str, Any]:
        return self._client._get(f"/experiments/{experiment_id}/run-results.schema.json")  # type: ignore[no-any-return]

    def download_run_results_csv(self, experiment_id: ResourceId) -> bytes:
        return self._client._get_bytes(f"/experiments/{experiment_id}/run-results.csv")  # type: ignore[no-any-return]

    def download_replicates_csv(self, experiment_id: ResourceId) -> bytes:
        return self._client._get_bytes(f"/experiments/{experiment_id}/replicates.csv")  # type: ignore[no-any-return]

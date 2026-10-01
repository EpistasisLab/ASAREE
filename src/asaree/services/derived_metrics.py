"""Custom metrics ASAREE computes in code from what a run already recorded.

Two producers, both deterministic reads of ``ProtocolRun.node_runs`` at
finalization -- nothing is executed here:

* ``asaree.tool_step`` -- a Tool Step's parsed result (e.g. a scoring call's
  response): the whole result, or one field of it per metric via a projection
  (see ``project_value``).
* ``asaree.feature_pipeline`` -- feature counts across a DC -> FTE -> FS chain,
  computed the way the spinal notebook's ``process_metrics`` does rather than
  trusted from an agent's end-of-turn self-report. Only the raw dataset's
  column names are needed beyond the run itself, to tell a created feature
  from a raw passthrough.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeGuard

from asaree.services.measurement_engine import (
    MetricDefinition,
    MetricObservation,
    ProducerBinding,
    ProducerProvenance,
)
from asaree.services.protocol_graph import node_map
from asaree.services.tool_steps import flatten_paths, resolve_upstream_payload

TOOL_STEP_PRODUCER_ID = "asaree.tool_step"
FEATURE_PIPELINE_PRODUCER_ID = "asaree.feature_pipeline"
DERIVED_PRODUCER_IDS = frozenset({TOOL_STEP_PRODUCER_ID, FEATURE_PIPELINE_PRODUCER_ID})

FEATURE_PIPELINE_OUTPUTS = (
    "n_features_after_dc",
    "n_features_after_fte",
    "n_features_after_fs",
    "n_features_created",
    "n_created_selected",
    "frac_created_selected",
)
_STAGE_NODE_KEYS = ("dc_node_id", "fte_node_id", "fs_node_id")
_FIELD_DEFAULTS = {
    "dc_count_field": "n_cols_out",
    "fte_count_field": "n_features_out",
    "fs_count_field": "n_features_out",
    "selected_field": "selected_features",
}

RawColumnsLoader = Callable[[str], Awaitable[frozenset[str] | None]]

# A projection picks one field out of a producer's JSON output:
# ``config.projections[output_key] = {"path": "a.b", "transform": "length"?}``.
# The path is an exact key of ``flatten_paths`` (result keys may contain dots,
# e.g. ``metrics_at_0.5``), and ``length`` -- the only transform -- turns a
# list or object into its size. An output with no projection records the
# producer's whole output.
PROJECTION_TRANSFORMS = frozenset({"length"})


def output_projection(binding: ProducerBinding, output_key: str) -> Mapping[str, Any] | None:
    projections = binding.config.get("projections")
    projection = projections.get(output_key) if isinstance(projections, Mapping) else None
    return projection if isinstance(projection, Mapping) else None


def project_value(paths: Mapping[str, Any], projection: Mapping[str, Any]) -> tuple[Any, str | None]:
    """``(value, error)`` for one projection over a ``flatten_paths`` mapping."""
    path = projection.get("path")
    if not isinstance(path, str) or path not in paths:
        return None, f"No value for {path!r} was recorded."
    value = paths[path]
    if projection.get("transform") == "length":
        if isinstance(value, list | Mapping):
            return len(value), None
        return None, f"{path!r} is not a list or object, so it has no length."
    return _json_safe(value)


def projection_issue(binding: ProducerBinding) -> tuple[str, str, str] | None:
    """``(code, message, config key)`` when a binding's projections are malformed."""
    projections = binding.config.get("projections")
    if projections is None:
        return None
    if not isinstance(projections, Mapping):
        return "reported_projections_invalid", "Field projections must be keyed by producer output.", "projections"
    for output_key, projection in projections.items():
        if (
            output_key not in binding.outputs
            or not isinstance(projection, Mapping)
            or not isinstance(projection.get("path"), str)
            or not projection["path"]
            or projection.get("transform") not in {None, *PROJECTION_TRANSFORMS}
        ):
            return (
                "reported_projection_invalid",
                f"The field projection for {output_key!r} needs an output, a path, and an optional 'length'.",
                f"projections.{output_key}",
            )
    return None


def _is_number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _json_safe(value: Any) -> tuple[Any, str | None]:
    """JSONB rejects NaN/Infinity, so a non-finite number is reported, not stored."""
    if isinstance(value, float) and not math.isfinite(value):
        return None, f"The value is not a finite number ({value})."
    return value, None


def _completed_payload(node_run: Any) -> dict[str, Any] | None:
    if not isinstance(node_run, Mapping) or node_run.get("status") != "completed":
        return None
    return resolve_upstream_payload([node_run])


def _tool_step_result(node_run: Any) -> Any:
    """The whole result of a completed Tool Step: its parsed JSON, else its text."""
    if not isinstance(node_run, Mapping) or node_run.get("status") != "completed":
        return None
    payload = node_run.get("payload")
    return payload if payload is not None else node_run.get("output_text")


def tool_step_values(node_run: Any) -> dict[str, Any] | None:
    """Every addressable value of a completed Tool Step, by dotted path.

    Paths address the tool's parsed result directly (``test_metrics.roc_auc``);
    ``tool_step.*`` addresses the step's own provenance.
    """
    if not isinstance(node_run, Mapping) or node_run.get("status") != "completed":
        return None
    result = node_run.get("payload")
    step = node_run.get("tool_step") or {}
    values = flatten_paths(result) if isinstance(result, Mapping) else {}
    values.update(flatten_paths({"tool_step": step}))
    return values


def _is_created_feature(name: Any, raw_columns: frozenset[str]) -> bool:
    return str(name).rsplit("__", 1)[-1] not in raw_columns


def feature_pipeline_values(
    node_runs: Mapping[str, Any],
    config: Mapping[str, Any],
    raw_columns: frozenset[str] | None,
) -> dict[str, Any]:
    """The notebook's process metrics from the three stages' typed payloads.

    A value is absent (not ``None``) when its inputs are: the caller reports
    that output as unavailable rather than recording a guess.
    """
    field = {key: str(config.get(key) or default) for key, default in _FIELD_DEFAULTS.items()}
    dc = _completed_payload(node_runs.get(str(config.get("dc_node_id") or ""))) or {}
    fte = _completed_payload(node_runs.get(str(config.get("fte_node_id") or ""))) or {}
    fs = _completed_payload(node_runs.get(str(config.get("fs_node_id") or ""))) or {}
    values: dict[str, Any] = {}
    after_dc, after_fte = dc.get(field["dc_count_field"]), fte.get(field["fte_count_field"])
    selected = fs.get(field["selected_field"])
    after_fs = fs.get(field["fs_count_field"])
    if not _is_number(after_fs) and isinstance(selected, list):
        after_fs = len(selected)
    if _is_number(after_dc):
        values["n_features_after_dc"] = after_dc
    if _is_number(after_fte):
        values["n_features_after_fte"] = after_fte
    if _is_number(after_fs):
        values["n_features_after_fs"] = after_fs
    if _is_number(after_dc) and _is_number(after_fte):
        values["n_features_created"] = after_fte - after_dc
    if raw_columns and isinstance(selected, list) and selected and _is_number(after_fs):
        created = sum(_is_created_feature(name, raw_columns) for name in selected)
        values["n_created_selected"] = created
        if after_fs:
            values["frac_created_selected"] = created / after_fs
    return values


async def load_dataset_raw_columns(dataset_id: str) -> frozenset[str] | None:
    """Column names of a registered dataset's raw file (its train split as a fallback)."""
    from asaree.models.database import get_session
    from asaree.models.dataset import RegisteredDataset

    try:
        key = uuid.UUID(dataset_id)
    except (TypeError, ValueError):
        return None
    async with get_session() as db:
        dataset = await db.get(RegisteredDataset, key)
        raw_path = getattr(dataset, "raw_path", None)
        train_path = getattr(dataset, "train_path", None)

    def read() -> frozenset[str] | None:
        import pandas as pd

        try:
            if raw_path:
                return frozenset(str(c) for c in pd.read_csv(raw_path, nrows=0).columns)
            if train_path:
                return frozenset(str(c) for c in pd.read_parquet(train_path).columns)
        except (OSError, ValueError):
            return None
        return None

    return await asyncio.to_thread(read)


def _dataset_id(graph: Mapping[str, Any], dataset_node_id: Any) -> str | None:
    node = node_map(graph).get(str(dataset_node_id or ""))
    data = node.get("data") if isinstance(node, Mapping) else None
    config = data.get("config") if isinstance(data, Mapping) else None
    dataset_id = config.get("dataset_id") if isinstance(config, Mapping) else None
    return str(dataset_id) if dataset_id else None


async def collect_derived_observations(
    binding: ProducerBinding,
    metrics: Mapping[str, MetricDefinition],
    *,
    node_runs: Mapping[str, Any],
    graph: Mapping[str, Any],
    attempt_id: str,
    raw_columns_loader: RawColumnsLoader = load_dataset_raw_columns,
) -> list[MetricObservation]:
    """One observation per output of a derived producer binding."""
    whole: Any = None
    if binding.producer_id == TOOL_STEP_PRODUCER_ID:
        node_run = node_runs.get(str(binding.config.get("node_id") or ""))
        values = tool_step_values(node_run)
        whole = _tool_step_result(node_run)
        missing_source = "The Tool Step did not complete."
        source = "tool_step_result"
    else:
        dataset_id = _dataset_id(graph, binding.config.get("dataset_node_id"))
        raw_columns = await raw_columns_loader(dataset_id) if dataset_id else None
        values = feature_pipeline_values(node_runs, binding.config, raw_columns)
        missing_source = None
        source = "stage_payloads"
    provenance = ProducerProvenance(
        binding_id=binding.id,
        producer_id=binding.producer_id,
        kind=binding.kind,
        version="1",
        binding_config=dict(binding.config),
        evaluation={"source": source},
    )
    observations: list[MetricObservation] = []
    for output_key, metric_id in binding.outputs.items():
        metric = metrics.get(metric_id)
        if metric is None:
            continue
        error: str | None
        projection = output_projection(binding, output_key) if binding.producer_id == TOOL_STEP_PRODUCER_ID else None
        if values is None:
            value, error = None, missing_source
        elif projection is not None:
            value, error = project_value(values, projection)
        elif binding.producer_id == TOOL_STEP_PRODUCER_ID:
            value, error = _json_safe(whole)
        elif output_key not in values:
            value, error = None, f"No value for {output_key!r} was recorded."
        else:
            value, error = _json_safe(values[output_key])
        observations.append(
            MetricObservation(
                metric_id=metric.id,
                metric_name=metric.name,
                value_type=None,
                value=value,
                status="measured" if error is None else "unavailable",
                error=error,
                attempt_id=attempt_id,
                producer=provenance,
                input_provenance={},
            )
        )
    return observations


def derived_binding_issue(binding: ProducerBinding, graph: Mapping[str, Any]) -> tuple[str, str, str] | None:
    """``(code, message, config key)`` when a derived binding's wiring is broken."""
    nodes = node_map(graph)
    if binding.producer_id == TOOL_STEP_PRODUCER_ID:
        node = nodes.get(str(binding.config.get("node_id") or ""))
        if node is None or node.get("type") != "tool_step":
            return "tool_step_missing", "The Tool Step is unavailable.", "node_id"
        data = node.get("data")
        if isinstance(data, Mapping) and data.get("active") is False:
            return "tool_step_disabled", "The Tool Step is disabled.", "node_id"
        return None
    unknown = sorted(set(binding.outputs) - set(FEATURE_PIPELINE_OUTPUTS))
    if unknown:
        return "feature_pipeline_unknown_output", f"Unknown feature-pipeline output {unknown[0]!r}.", "outputs"
    for key in _STAGE_NODE_KEYS:
        node = nodes.get(str(binding.config.get(key) or ""))
        if node is None or node.get("type") not in ("agent", "sub_agent"):
            return "feature_pipeline_stage_missing", f"The {key.split('_')[0].upper()} Agent is unavailable.", key
    needs_columns = {"n_created_selected", "frac_created_selected"} & set(binding.outputs)
    if needs_columns and _dataset_id(graph, binding.config.get("dataset_node_id")) is None:
        return (
            "feature_pipeline_dataset_missing",
            "Counting created features needs the raw Dataset node.",
            "dataset_node_id",
        )
    return None


__all__ = [
    "DERIVED_PRODUCER_IDS",
    "PROJECTION_TRANSFORMS",
    "FEATURE_PIPELINE_OUTPUTS",
    "FEATURE_PIPELINE_PRODUCER_ID",
    "TOOL_STEP_PRODUCER_ID",
    "collect_derived_observations",
    "derived_binding_issue",
    "feature_pipeline_values",
    "load_dataset_raw_columns",
    "output_projection",
    "project_value",
    "projection_issue",
    "tool_step_values",
]

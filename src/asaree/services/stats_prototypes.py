"""Database input/output boundary for both statistics prototypes."""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from asaree.models.factorial_cell import FactorialCell
from asaree.models.factorial_replicate_result import FactorialReplicateResult
from asaree.models.factorial_row_result import FactorialRowResult
from asaree.services.design_revisions import get_current_revision
from asaree.services.factorial_cells import list_replicates


class StatsRequest(BaseModel):
    metric: str = Field(min_length=1, max_length=255)
    factors: list[str] = Field(min_length=1, max_length=6)
    family: str = "gaussian"
    measurement_type: str = Field(default="continuous", pattern="^(continuous|count|binary|proportion|ordinal|auto)$")
    denominator: str | None = None
    ordinal_levels: list[str] = Field(default_factory=list)
    max_order: int = Field(default=2, ge=1, le=3)
    n_boot: int = Field(default=100, ge=20, le=5000)
    n_boot_lrt: int = Field(default=0, ge=0, le=1999)
    n_perm: int = Field(default=999, ge=99, le=10000)
    seed: int = Field(default=20261016, ge=0, le=2**32 - 1)
    tweedie_power: float = Field(default=1.5, gt=1, lt=2)
    source_analysis_id: uuid.UUID | None = None


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool | int | float) and math.isfinite(float(value)):
        return float(value)
    return None


async def read_source(db: AsyncSession, experiment_id: uuid.UUID) -> dict:
    revision = await get_current_revision(db, experiment_id)
    count = await db.scalar(
        select(func.count())
        .select_from(FactorialReplicateResult)
        .join(FactorialCell)
        .where(
            FactorialCell.experiment_id == experiment_id,
            FactorialCell.design_revision_id == (revision.id if revision else None),
        )
    )
    if count and count > 5000:
        raise ValueError("This prototype supports designs with at most 5,000 replicate slots.")
    replicates = await list_replicates(db, experiment_id=experiment_id, revision_id=revision.id) if revision else []
    # Row slots are correlated observations, not independent experimental replicates.
    row_parents = (
        set(
            (
                await db.scalars(
                    select(FactorialRowResult.replicate_result_id)
                    .where(FactorialRowResult.replicate_result_id.in_([r.id for r in replicates]))
                    .distinct()
                )
            ).all()
        )
        if replicates
        else set()
    )
    rows = []
    for replicate in replicates:
        if replicate.id in row_parents:
            continue
        rows.append(
            dict(
                id=str(replicate.id),
                replicate=replicate.replicate_number,
                factors=replicate.factor_values or {},
                metrics={
                    key: value
                    for key, value in (replicate.metric_values or {}).items()
                    if finite_number(value) is not None or isinstance(value, str)
                },
            )
        )
    payload = dict(
        design_revision_id=str(revision.id) if revision else None,
        design_revision=revision.revision if revision else None,
        rows=rows,
        excluded_row_replicates=len(row_parents),
    )
    payload["fingerprint"] = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return payload


def catalog(source: dict) -> dict:
    factors: dict[str, set[str]] = {}
    metrics: dict[str, int] = {}
    for row in source["rows"]:
        for key, value in row["factors"].items():
            if value is not None and not isinstance(value, dict | list):
                factors.setdefault(key, set()).add(str(value))
        for key, value in row["metrics"].items():
            if finite_number(value) is not None or isinstance(value, str):
                metrics[key] = metrics.get(key, 0) + 1
    return dict(
        factors=[
            dict(name=name, levels=sorted(levels)) for name, levels in sorted(factors.items()) if len(levels) >= 2
        ],
        metrics=[dict(name=name, n=n) for name, n in sorted(metrics.items())],
        n_replicates=len(source["rows"]),
        design_revision=source["design_revision"],
        design_revision_id=source["design_revision_id"],
        fingerprint=source["fingerprint"],
        excluded_row_replicates=source["excluded_row_replicates"],
    )


def prepare_frame(source: dict, config: dict) -> pd.DataFrame:
    factors = config["factors"]
    if config.get("denominator") and config["family"] not in ("binomial", "betabinomial", "auto"):
        raise ValueError("A trials denominator is only supported for binomial / beta-binomial outcomes.")
    if len(set(config["ordinal_levels"])) != len(config["ordinal_levels"]):
        raise ValueError("Ordinal levels must be unique.")
    if len(set(factors)) != len(factors):
        raise ValueError("Select distinct factors.")
    available = {f["name"] for f in catalog(source)["factors"]}
    if not set(factors) <= available:
        raise ValueError("Select varying factors present in this source snapshot.")
    rows = []
    for item in source["rows"]:
        values = item["metrics"]
        raw = values.get(config["metric"])
        ordinal = config["measurement_type"] == "ordinal" or config["family"] == "ordinal_logit"
        outcome = (
            config["ordinal_levels"].index(str(raw))
            if ordinal and str(raw) in config["ordinal_levels"]
            else finite_number(raw)
        )
        if raw is not None and ordinal and str(raw) not in config["ordinal_levels"]:
            raise ValueError("Ordinal levels must include every observed category, in order.")
        if outcome is None:
            continue
        factor_values = item["factors"]
        if any(factor_values.get(f) is None or isinstance(factor_values[f], dict | list) for f in factors):
            raise ValueError("A scored replicate has missing or structured factor values.")
        row = {f"factor_{i}": str(factor_values[f]) for i, f in enumerate(factors)}
        row.update(outcome=outcome, outcome_raw=str(raw), replicate=item["replicate"])
        if config.get("denominator"):
            trials = finite_number(values.get(config["denominator"]))
            if trials is None:
                continue
            if trials <= 0 or trials != round(trials) or outcome < 0 or outcome > trials or outcome != round(outcome):
                raise ValueError("Binomial counts require integer successes and positive integer trials.")
            row["trials"] = trials
        rows.append(row)
    frame = pd.DataFrame(rows)
    if len(frame) < 5:
        raise ValueError("At least five scored whole-dataset replicates are required for this metric.")
    if frame.outcome.nunique() < 2:
        raise ValueError("The selected outcome is constant.")
    family = config["family"]
    y = frame.outcome.to_numpy(float)
    if family in ("gamma", "lognormal") and np.any(y <= 0):
        raise ValueError(f"{family} requires strictly positive outcomes.")
    if family in ("poisson", "negbin", "zip", "zinb", "hurdle_poisson", "hurdle_negbin") and (
        np.any(y < 0) or np.any(y != np.round(y))
    ):
        raise ValueError("Count models require nonnegative integer outcomes.")
    if family == "beta" and np.any((y <= 0) | (y >= 1)):
        raise ValueError("Beta regression requires values strictly between zero and one.")
    if family == "binomial" and not config.get("denominator") and not set(y) <= {0, 1}:
        raise ValueError("Binomial outcomes require binary values or a trials denominator.")
    if len(frame) > 5000:
        raise ValueError("The prototype supports up to 5,000 scored replicates per analysis.")
    # Prevent factorial explosion, rank-deficient full designs and unstable fits.
    counts = [frame[f"factor_{i}"].nunique() for i in range(len(factors))]
    if any(count < 2 for count in counts):
        raise ValueError("Each selected factor needs at least two observed levels after missing outcomes are excluded.")
    if math.prod(counts) > 128:
        raise ValueError("Select factors with at most 128 combined cells.")
    return frame


def json_safe(value: Any) -> Any:
    if isinstance(value, pd.DataFrame):
        return json.loads(value.to_json(orient="records"))
    if isinstance(value, np.ndarray):
        return json_safe(value.tolist())
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def compute(backend: str, source: dict, config: dict) -> dict:
    started = time.perf_counter()
    frame = prepare_frame(source, config)
    if backend == "stats_sm":
        from asaree.stats_sm import analyze

        output = analyze(frame, config)
    else:
        from asaree.stats import AnalysisConfig, FactorSpec, OutcomeSpec, run_analysis
        from asaree.stats.design import DesignBuilder, DesignSpec, full_factorial_terms
        from asaree.stats.report import markdown_report

        # Internal names prevent metric/factor collisions with package bookkeeping.
        aliases = {name: f"factor_{i}" for i, name in enumerate(config["factors"])}
        original_frame = frame.copy()
        ordinal = config["family"] == "ordinal_logit" or config["measurement_type"] == "ordinal"
        if ordinal:
            original_frame["outcome"] = frame.outcome_raw
        cfg = AnalysisConfig(
            factors=[FactorSpec(alias) for alias in aliases.values()],
            outcomes=[
                OutcomeSpec(
                    "outcome",
                    measurement_type="ordinal"
                    if ordinal
                    else (
                        "binary"
                        if config["family"] == "binomial" and not config.get("denominator")
                        else config["measurement_type"]
                    ),
                    family=None if config["family"] == "auto" else config["family"],
                    denominator="trials" if config.get("denominator") else None,
                    levels=config["ordinal_levels"] if ordinal else None,
                )
            ],
            replicate_col=None,
            max_interaction_order=config["max_order"],
            n_boot=config["n_boot"],
            n_boot_lrt=config["n_boot_lrt"],
            n_perm=config["n_perm"],
            seed=config["seed"],
            n_jobs=1,
            make_plots=False,
            n_sim_diagnostics=250,
            boot_lrt_time_budget_s=120,
        )
        levels = {alias: sorted(original_frame[alias].unique()) for alias in aliases.values()}
        builder = DesignBuilder(
            DesignSpec(
                list(aliases.values()), levels, full_factorial_terms(list(aliases.values()), config["max_order"])
            )
        )
        x = builder.build(original_frame)
        if np.linalg.matrix_rank(x) < x.shape[1] or len(frame) <= x.shape[1] + 2:
            raise ValueError("Design is rank deficient or has too few residual observations; lower interaction order.")
        result = run_analysis(original_frame, cfg)
        errors = [o.error.splitlines()[0] for o in result.outcomes.values() if o.error]
        if not result.ok or errors:
            raise ValueError("; ".join(errors) or result.validation.to_string(index=False))
        tables = dict(result.tables, validation=result.validation, factor_registry=result.factor_registry)
        diag = result.outcomes["outcome"].diagnostics
        tables["residuals"] = pd.DataFrame(
            dict(fitted=diag["_mu"], pit=diag["_pit"], observed=result.outcomes["outcome"].model.y)
        )
        n = len(frame)
        tables["pit_qq"] = pd.DataFrame(dict(expected=(np.arange(n) + 0.5) / n, observed=np.sort(diag["_pit"])))
        simulations = np.sort(diag["_sims"], axis=0)
        tables["distribution_envelope"] = pd.DataFrame(
            dict(
                quantile=(np.arange(n) + 1) / n,
                observed=np.sort(result.outcomes["outcome"].model.y),
                simulated=np.median(simulations, axis=1),
                sim_low=np.quantile(simulations, 0.025, axis=1),
                sim_high=np.quantile(simulations, 0.975, axis=1),
            )
        )
        output = dict(
            tables=tables,
            notes=[
                "Prototype resolution: increase resampling counts before scientific interpretation.",
                "Factor aliases: " + ", ".join(f"{alias} = {name}" for name, alias in aliases.items()),
            ],
            unavailable=[],
            family=result.outcomes["outcome"].family,
            report=markdown_report(result).replace("contrasts.csv", "the Contrasts table"),
        )
    output.update(
        n_observations=len(frame),
        n_excluded=len(source["rows"]) - len(frame),
        source_fingerprint=source["fingerprint"],
        runtime_s=time.perf_counter() - started,
    )
    return json_safe(output)

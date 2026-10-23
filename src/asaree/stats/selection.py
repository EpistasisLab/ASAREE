"""Outcome documentation, rule-based candidate families and AIC-based selection."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from . import families as F
from .diagnostics import diagnose
from .engine import Model

MEASUREMENT_TYPES = ("continuous", "count", "binary", "proportion", "ordinal", "auto")


@dataclass
class OutcomeSpec:
    """Declared construction of an outcome metric.

    measurement_type: 'continuous' | 'count' | 'binary' | 'proportion' | 'ordinal' | 'auto'
    denominator:      column with the number of trials when the outcome is a count of
                      successes (proportion with known numerator and denominator)
    levels:           ordered category labels for ordinal outcomes (the column's values
                      must be these labels; they are mapped to codes 0..K-1)
    scores:           numeric scores for ordinal categories (default 1..K)
    derived_from:     name of the metric this one is a deterministic transformation of
    allows_negative / allows_zero: declared support (None = infer from data)
    family:           force a family (skips selection, still diagnosed)
    candidates:       replace the rule-based candidate list
    aux_design:       'intercept' or 'full' for the second linear predictor of
                      zero-inflated / hurdle / ZOIB models (None = family default)
    """

    name: str
    measurement_type: str = "auto"
    denominator: str | None = None
    levels: list | None = None
    scores: list | None = None
    derived_from: str | None = None
    allows_negative: bool | None = None
    allows_zero: bool | None = None
    description: str = ""
    family: str | None = None
    candidates: list[str] | None = None
    aux_design: str | None = None
    prespecified: bool = True

    def __post_init__(self):
        if self.measurement_type not in MEASUREMENT_TYPES:
            raise ValueError(f"{self.name}: measurement_type must be one of {MEASUREMENT_TYPES}")


def response_arrays(df: pd.DataFrame, spec: OutcomeSpec):
    """Return (y, aux) on the scale each family expects."""
    y = df[spec.name]
    aux: dict = {}
    if spec.measurement_type == "ordinal" and spec.levels is not None:
        bad = set(pd.unique(y.dropna())) - set(spec.levels)
        if bad:
            raise ValueError(f"{spec.name}: values {sorted(bad)} are not in the declared ordinal levels")
        y = y.map({l: i for i, l in enumerate(spec.levels)})
    y = y.to_numpy(dtype=float)
    if spec.denominator:
        aux["trials"] = df[spec.denominator].to_numpy(dtype=float)
    elif spec.measurement_type == "binary":
        aux["trials"] = np.ones(len(y))
    return y, aux


def working_values(y, aux, spec: OutcomeSpec) -> np.ndarray:
    """Outcome on its natural reporting scale (proportion, score) for rank/OLS methods."""
    if "trials" in aux:
        return y / aux["trials"]
    if spec.measurement_type == "ordinal":
        sc = np.asarray(spec.scores if spec.scores is not None else np.arange(1, len(spec.levels) + 1), float)
        return sc[y.astype(int)]
    return y


def profile_outcome(y, aux, spec: OutcomeSpec, clustered: bool, cluster_desc: str) -> dict:
    """Document measurement properties (declared and observed)."""
    v = y / aux["trials"] if "trials" in aux else y
    integer = bool(np.all(np.isfinite(y)) and np.all(y == np.round(y)))
    prof = dict(
        outcome=spec.name,
        declared_measurement_type=spec.measurement_type,
        description=spec.description,
        n=len(y),
        min=float(np.min(v)),
        max=float(np.max(v)),
        mean=float(np.mean(v)),
        sd=float(np.std(v, ddof=1)),
        skewness=float(stats.skew(v)) if len(v) > 2 else np.nan,
        negative_values_observed=bool(np.any(v < 0)),
        negative_possible=spec.allows_negative if spec.allows_negative is not None else bool(np.any(v < 0)),
        zero_values_observed=int(np.sum(v == 0)),
        zero_possible=spec.allows_zero if spec.allows_zero is not None else bool(np.any(v == 0)),
        one_values_observed=int(np.sum(v == 1)),
        integer_valued=integer,
        bounded_0_1=bool(np.all((v >= 0) & (v <= 1))),
        is_count=spec.measurement_type == "count"
        or (spec.measurement_type == "auto" and integer and np.min(v) >= 0 and len(np.unique(v)) > 2),
        known_numerator_denominator=bool(spec.denominator),
        n_distinct=int(len(np.unique(v))),
        deterministic_transform_of=spec.derived_from or "",
        observations=f"clustered ({cluster_desc})" if clustered else "independent runs (conditional on fixed split)",
    )
    if prof["is_count"] and np.mean(v) > 0:
        prof["variance_to_mean"] = float(np.var(v, ddof=1) / np.mean(v))
        prof["zeros_expected_if_poisson"] = float(np.sum(np.exp(-np.mean(v))))
    return prof


def infer_type(y, aux, spec) -> tuple[str, list[str]]:
    notes = []
    t = spec.measurement_type
    if t != "auto":
        return t, notes
    if "trials" in aux:
        return "proportion", notes
    u = np.unique(y)
    if set(u) <= {0.0, 1.0}:
        t = "binary"
    elif np.all(y == np.round(y)) and np.min(y) >= 0:
        t = "count"
    elif np.all((y >= 0) & (y <= 1)):
        t = "proportion"
    else:
        t = "continuous"
    notes.append(f"measurement type inferred from data as '{t}' (no declaration); confirm with the metric author")
    return t, notes


def candidate_families(y, aux, spec: OutcomeSpec) -> tuple[list[tuple[str, dict]], list[str], str]:
    """Rule-based candidates from the metric's construction. Returns (candidates, notes, measurement_type)."""
    t, notes = infer_type(y, aux, spec)
    kw = {"aux_design": spec.aux_design} if spec.aux_design else {}
    cands: list[tuple[str, dict]] = []
    has0 = bool(np.any(y == 0))
    if spec.family:
        extra = dict(n_levels=len(spec.levels), scores=spec.scores) if spec.family == "ordinal_logit" else {}
        return [(spec.family, {**kw, **extra})], ["family forced by OutcomeSpec.family"], t
    if spec.candidates:
        return [(c, dict(kw)) for c in spec.candidates], ["candidates set by OutcomeSpec.candidates"], t
    if t == "binary":
        aux.setdefault("trials", np.ones(len(y)))
        if not set(np.unique(y)) <= {0.0, 1.0}:
            notes.append("REVIEW: declared binary but values are not 0/1")
        cands = [("binomial", {})]
    elif t == "ordinal":
        if spec.levels is None:
            raise ValueError(f"{spec.name}: ordinal outcomes need OutcomeSpec.levels")
        cands = [("ordinal_logit", dict(n_levels=len(spec.levels), scores=spec.scores))]
    elif t == "proportion" and "trials" in aux:
        cands = [("binomial", {}), ("betabinomial", {})]
        notes.append(
            "quasi-binomial not used: it has no likelihood (no AIC) and no generative model for the "
            "parametric bootstrap; beta-binomial is the likelihood-based overdispersion model and HC3 "
            "Wald tests are already robust to residual overdispersion"
        )
    elif t == "proportion":
        if np.any((y < 0) | (y > 1)):
            notes.append(
                "REVIEW: declared proportion but values fall outside [0, 1]; fitting Gaussian, "
                "consider rescaling the metric"
            )
            cands = [("gaussian", {})]
        elif set(np.unique(y)) <= {0.0, 1.0}:
            notes.append("proportion takes only 0/1 values; treated as binary")
            cands = [("binomial", {})]
            aux["trials"] = np.ones(len(y))
        elif np.any((y == 0) | (y == 1)):
            cands = [("zoib", dict(kw))]
            notes.append(
                "exact 0/1 present: zero/one-inflated beta. A boundary-squeeze transformation "
                "(Smithson & Verkuilen 2006) is an alternative only if 0/1 are measurement artefacts"
            )
        else:
            cands = [("beta", {})]
    elif t == "count":
        if not np.all((y >= 0) & (y == np.round(y))):
            notes.append(
                "REVIEW: declared count but values are not non-negative integers; fitting Gaussian, "
                "consider whether the metric is a rate (use a count with exposure) or transform"
            )
            cands = [("gaussian", {})]
        else:
            cands = [("poisson", {}), ("negbin", {})]
            if has0 and np.any(y > 0):
                cands += [
                    ("zip", dict(kw)),
                    ("zinb", dict(kw)),
                    ("hurdle_poisson", dict(kw)),
                    ("hurdle_negbin", dict(kw)),
                ]
    else:  # continuous
        neg = np.any(y < 0) or bool(spec.allows_negative)
        if neg:
            cands = [("gaussian", {})]
        elif np.all(y > 0):
            cands = [("gamma", {}), ("lognormal", {}), ("gaussian", {})]
        else:
            cands = [("gamma_hurdle", dict(kw)), ("tweedie", {})]
            notes.append(
                "non-negative with exact zeros: two-part (hurdle) and Tweedie candidates; Gaussian "
                "excluded because its likelihood is not comparable with a point mass at zero"
            )
    if spec.derived_from:
        notes.append(
            f"deterministic transformation of '{spec.derived_from}': inference is not independent "
            "evidence; count it once when interpreting multiplicity"
        )
    return cands, notes, t


def fit_candidates(y, aux, x_base, spec: OutcomeSpec, cell_idx, cell_labels, n_sim, rng):
    cands, notes, mtype = candidate_families(y, aux, spec)
    rows, fitted = [], {}
    for name, kw in cands:
        fam = F.make_family(name, **{k: v for k, v in kw.items() if v is not None})
        msg = fam.check_support(y, aux)
        if msg:
            rows.append(dict(outcome=spec.name, family=name, status=f"skipped: {msg}"))
            continue
        fam.configure(y, aux)
        model = Model.from_base(fam, y, x_base, aux)
        try:
            fit = model.fit()
        except Exception as e:  # pragma: no cover - defensive
            rows.append(dict(outcome=spec.name, family=name, status=f"error: {e}"))
            continue
        d = diagnose(model, fit, cell_idx, cell_labels, n_sim, rng)
        fitted[name] = (model, fit, d)
        rows.append(
            dict(
                outcome=spec.name,
                family=name,
                description=fam.description,
                status="fitted",
                converged=fit.converged,
                hessian_pd=fit.hessian_pd,
                loglik=fit.loglik,
                n_params=fit.n_params,
                aic=fit.aic,
                bic=fit.bic,
                ks_uniform_p=d["ks_uniform_p"],
                dispersion_ratio=d["dispersion_ratio"],
                dispersion_p=d["dispersion_p"],
                zero_inflation_p=d.get("zero_inflation_p", np.nan),
                severe_flags=d["severe"],
                flags="; ".join(d["flags"]),
            )
        )
    table = pd.DataFrame(rows)
    return table, fitted, notes, mtype


def choose(table: pd.DataFrame, fitted: dict, spec: OutcomeSpec) -> tuple[str | None, str]:
    ok = table[(table.get("status") == "fitted")].copy() if len(table) else table
    if ok is None or len(ok) == 0:
        return None, "no candidate could be fitted"
    if spec.family:
        selected = ok[ok.family == spec.family]
        if selected.empty or not bool(selected.iloc[0].converged) or not bool(selected.iloc[0].hessian_pd):
            return None, "the requested model did not converge with usable information"
        return spec.family, "family forced by the outcome specification"
    usable = ok[(ok.converged) & (ok.hessian_pd)]
    if len(usable) == 0:
        return None, "no candidate converged with a positive-definite information matrix"
    usable = usable.assign(delta_aic=usable.aic - usable.aic.min())
    clean = usable[~usable.severe_flags]
    pool = clean if len(clean) else usable
    best = pool.sort_values("aic").iloc[0]
    parts = []
    if len(usable) == 1:
        parts.append(f"{best.family} was the only usable candidate implied by the metric's construction")
    else:
        parts.append(
            f"{best.family} has the lowest AIC among {'diagnostically adequate' if len(clean) else 'usable'} "
            f"candidates (AIC={best.aic:.1f})"
        )
        others = usable[usable.family != best.family].sort_values("aic")
        parts.append("; ".join(f"{r.family}: dAIC={r.aic - best.aic:+.1f}" for r in others.itertuples()))
        near = others[(others.aic - best.aic).abs() < 2]
        if len(near):
            parts.append(
                "near-tie (|dAIC|<2) with "
                + ", ".join(near.family)
                + ": effects compared across these models in the stability check"
            )
    if len(clean) == 0:
        parts.append(
            "all candidates show severe diagnostic flags: FLAGGED FOR REVIEW "
            "(consider a transformation or a different metric construction)"
        )
    elif len(clean) < len(usable):
        dropped = usable[usable.severe_flags].family.tolist()
        parts.append("excluded for severe diagnostic failures: " + ", ".join(dropped))
    return best.family, ". ".join(parts)


def akaike_weights(table: pd.DataFrame) -> pd.DataFrame:
    if "aic" not in table or table.aic.isna().all():
        return table
    t = table.copy()
    a = t.aic.where(t.status == "fitted")
    d = a - a.min()
    w = np.exp(-0.5 * d)
    t["delta_aic"] = d
    t["akaike_weight"] = w / w.sum()
    return t

"""Automatic, producer-aware analysis planning and a backend-neutral report.

Metric names are hints, never executable instructions. Native output identities
and declarations take precedence over observed support. Unsupported constructions
produce an explicit unavailable result rather than invented statistical evidence.
"""

from __future__ import annotations

import importlib.metadata
import json
import math
import re

import numpy as np
from statsmodels.stats.multitest import multipletests

from asaree.stats.analysis import analyze

REPORT_VERSION = 3
ORDERED_LABELS = (
    ("very low", "low", "medium", "high", "very high"),
    ("strongly disagree", "disagree", "neutral", "agree", "strongly agree"),
    ("poor", "fair", "good", "very good", "excellent"),
)


DISTRIBUTION_NAMES = {
    "gaussian": "Gaussian (normal)",
    "gamma": "Gamma (log link)",
    "lognormal": "Lognormal",
    "poisson": "Poisson (log link)",
    "negbin": "Negative binomial (NB2, log link)",
    "binomial": "Binomial (logit link)",
    "beta": "Beta (logit link)",
    "betabinomial": "Beta-binomial",
    "ordinal_logit": "Ordered categorical (cumulative logit)",
    "zip": "Zero-inflated Poisson",
    "zinb": "Zero-inflated negative binomial",
    "zoib": "Zero/one-inflated beta",
    "gamma_hurdle": "Hurdle Gamma",
}
LINK_SCALES = {
    "gaussian": ("identity", "response scale (identity link)"),
    "lognormal": ("log", "log scale (OLS on the log outcome)"),
    "gamma": ("log", "log-mean scale (log link)"),
    "poisson": ("log", "log-mean scale (log link)"),
    "negbin": ("log", "log-mean scale (log link)"),
    "zip": ("log", "log-mean scale of the count part (log link)"),
    "zinb": ("log", "log-mean scale of the count part (log link)"),
    "binomial": ("logit", "log-odds scale (logit link)"),
    "beta": ("logit", "logit-mean scale (logit link)"),
    "ordinal_logit": ("cumulative", "cumulative log-odds scale"),
}
INTERACTION_SIGN = " × "


def join_names(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def averaged_phrase(others, levels):
    """Which settings a quantity averages over, and with what weight."""
    if not others:
        return "No other factors to average over."
    parts = [f"{name} ({', '.join(levels[name])})" for name in others]
    return (
        f"Averaged with equal weight over {join_names(parts)}: every combination counts once, "
        "regardless of how many runs it has."
    )


def factor_test_text(factors, other_factors, levels, family, metric):
    """Null hypothesis, question and interpretation for one model term, in that model's scale."""
    link = LINK_SCALES.get(family, ("custom", "model scale"))[0]
    named = INTERACTION_SIGN.join(factors)
    over = f", averaged with equal weight over {join_names(other_factors)}," if other_factors else ""
    if len(factors) == 1:
        (factor,) = factors
        settings = ", ".join(levels[factor])
        quantity = {
            "identity": "expected value",
            "log": "log expected value" if family != "lognormal" else "mean of log(outcome)",
            "logit": "log-odds",
            "cumulative": "cumulative log-odds",
        }.get(link, "model-scale mean")
        null = f"The {quantity}{over} is the same at every {factor} level ({settings})."
        if link == "log" and family != "lognormal" and other_factors:
            null += (
                f" Equivalently, the geometric mean over {join_names(other_factors)} settings of the model's "
                "condition means is equal across levels."
            )
        elif family == "lognormal":
            null += " Equivalently, the outcome's geometric means are equal across levels."
        question = f"Does {factor} change the expected {metric}" + (
            f", on average across {join_names(other_factors)}?" if other_factors else "?"
        )
        interpretation = (
            f"Evidence that {factor} changes {metric}"
            + (f" on average over {join_names(other_factors)}" if other_factors else "")
            + f". Its size and direction are in the {factor} comparison."
        )
        return question, null, interpretation
    first, rest = factors[0], factors[1:]
    rest_names = join_names(rest)
    effect = {
        "identity": ("difference", "difference of differences = 0"),
        "log": ("ratio of expected values", "ratio of ratios = 1"),
        "logit": ("odds ratio", "ratio of odds ratios = 1"),
    }.get(link, ("effect on the model scale", "interaction coefficients = 0"))
    others_text = f" ({over.strip(', ')})" if other_factors else ""
    null = f"The {first} {effect[0]} is the same at every {rest_names} setting{others_text}: {effect[1]}."
    if family == "lognormal":
        null += " This equals a ratio of ratios of means only when the log-scale variance is constant."
    question = f"Does the effect of {first} on {metric} depend on {rest_names}?"
    interpretation = (
        f"Evidence that the {first} {effect[0]} differs between {rest_names} settings, so {named} "
        "effects should be read together rather than separately."
    )
    return question, null, interpretation


def comparison_text(row, family, levels, metric, unit):
    """Exact target, null and interpretation for one comparison row."""
    factors, pairs = row.get("factors") or [], row.get("levels") or []
    scale = row.get("scale", "difference")
    ratio = scale.startswith("ratio")
    op = "÷" if ratio else "−"
    others = row.get("averaged_over") or []
    if len(factors) == 1:
        (factor,), ((left, right),) = factors, pairs
        label = f"{factor}: {left} vs {right}"
        target = f"Mean predicted {metric} at {factor} = {left} {op} mean predicted {metric} at {factor} = {right}."
        null = "ratio = 1" if ratio else "difference = 0"
        interpretation = (
            f"Above 1: {left} has the higher average; below 1: {right} does. "
            f"The estimate is how many times larger the {left} average is."
            if ratio
            else f"Positive: {left} averages higher by that many {unit or 'units'}; negative: lower."
        )
        question = f"How much does {metric} differ between {factor} = {left} and {factor} = {right}?"
    elif len(factors) == 2:
        (a, b), ((a1, a2), (b1, b2)) = factors, pairs
        label = f"{a} ({a1} vs {a2}){INTERACTION_SIGN}{b} ({b1} vs {b2})"
        inner = f"{a1} {op} {a2}"
        target = f"({inner} at {b} = {b1}) {op} ({inner} at {b} = {b2}), using mean predicted {metric}: " + (
            "a ratio of ratios." if ratio else "a difference of differences."
        )
        null = "ratio of ratios = 1" if ratio else "difference of differences = 0"
        interpretation = (
            f"Above 1: the {a1}/{a2} ratio is larger at {b} = {b1} than at {b2}; below 1: smaller; "
            "1: the same ratio at both settings."
            if ratio
            else f"Positive: the {a1} − {a2} difference is larger at {b} = {b1} than at {b2}; negative: smaller."
        )
        question = f"Does the {a} {a1} vs {a2} {'ratio' if ratio else 'difference'} change between {b} = {b1} and {b2}?"
    else:
        return dict(label=row.get("label", ""), target=row.get("label", ""), null="", interpretation="", question="")
    return dict(
        label=label,
        target=target,
        averaged_over=averaged_phrase(others, levels),
        null=null,
        interpretation=interpretation,
        question=question,
    )


def holm_note(count, noun):
    if count == 0:
        return f"no {noun} available"
    if count == 1:
        return f"one {noun.rstrip('s')} in its family, so the Holm-adjusted p equals the unadjusted p"
    return f"Holm across {count} {noun}"


def number(value):
    return isinstance(value, bool | int | float) and math.isfinite(float(value))


def factor_labels(source, factors):
    """Use declared treatment labels, including structured model/prompt levels."""
    mappings = {}
    for name in factors:
        definition = source.get("factor_metadata", {}).get(name, {})
        declared = definition.get("levels", [])
        labels = definition.get("level_labels", [])
        mapping = {}
        if len(declared) == len(labels):
            mapping = {
                json.dumps(value, sort_keys=True, default=str): str(label)
                for value, label in zip(declared, labels, strict=True)
            }
        for row in source["rows"]:
            value = row.get("factors", {}).get(name)
            if value is None:
                continue
            key = json.dumps(value, sort_keys=True, default=str)
            if key not in mapping:
                label = (
                    str(value)
                    if not isinstance(value, dict | list) and len(str(value)) <= 80
                    else (f"{name} level {len(mapping) + 1}")
                )
                if label in mapping.values():
                    label += f" ({len(mapping) + 1})"
                mapping[key] = label
        if len(set(mapping.values())) != len(mapping):
            raise ValueError(f"Treatment labels for {name} are not unique; comparison would merge distinct conditions.")
        mappings[name] = mapping
    return mappings


def coerce_values(values):
    """Lossless numeric strings and conventional binary labels only."""
    if all(number(v) for v in values):
        return values, {}
    numeric = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
    if all(number(v) or isinstance(v, str) and numeric.fullmatch(v.strip()) for v in values):
        converted = [float(v) for v in values]
        if all(number(v) for v in converted):
            return converted, {str(v): float(v) for v in values if isinstance(v, str)}
    if any(
        isinstance(v, str)
        and (
            numeric.fullmatch(v.strip())
            or v.strip().lower() in {"nan", "inf", "infinity", "-inf", "+inf", "null", "none", ""}
        )
        for v in values
    ):
        # Invalid/mixed numeric text is not evidence of binary categories.
        return values, {}
    for labels in (("false", "true"), ("no", "yes"), ("fail", "pass"), ("failure", "success")):
        if all(isinstance(v, str) and v.strip().lower() in labels for v in values):
            mapping = {v: labels.index(v.strip().lower()) for v in values}
            return [mapping[v] for v in values], mapping
    if all(
        isinstance(v, str) and len(v.strip()) <= 48 and len(v.split()) <= 4 and not re.search(r"[.!?]$", v.strip())
        for v in values
    ):
        labels = sorted({v.strip().lower() for v in values})
        if len(labels) == 2 and not any(set(labels) <= set(scale) for scale in ORDERED_LABELS):
            mapping = {v: labels.index(v.strip().lower()) for v in values}
            return [mapping[v] for v in values], mapping
    return values, {}


def plan_metric(source: dict, metric: str) -> dict:
    """Resolve measurement semantics before choosing a likelihood family."""
    meta = source.get("metric_metadata", {}).get(metric, {})
    raw = [row.get("metrics", {}).get(metric) for row in source["rows"]]
    original_values = [v for v in raw if v is not None]
    values, conversion = coerce_values(original_values)
    clues = " ".join(str(meta.get(k, "")) for k in ("name", "description", "unit", "output_key"))
    clues = f"{metric} {clues}".lower().replace("_", " ")
    warnings = []
    kind = meta.get("measurement_type", "auto")
    levels = meta.get("ordinal_levels") or meta.get("levels") or []
    category_labels = []
    denominator = meta.get("denominator")
    if not denominator and re.search(r"\b(successes|success count)\b", clues):
        available = set().union(*(row.get("metrics", {}) for row in source["rows"]))
        denominator = next((k for k in ("trials", "n_trials", "total_trials") if k in available), None)
    if meta.get("exposure") or meta.get("censored"):
        raise ValueError("This metric requires an exposure or censoring model that is not supported yet.")
    if not values:
        details = " ".join(source.get("metric_errors", {}).get(metric, [])[:3])
        raise ValueError("No measured values are available for this metric." + (f" {details}" if details else ""))
    if levels:
        kind = "ordinal"
        levels = [str(v) for v in levels]
        values, conversion = original_values, {}
    elif not all(number(v) for v in values):
        if all(isinstance(v, str) for v in values):
            observed = {v.lower().strip() for v in values}
            scale = next((s for s in ORDERED_LABELS if observed <= set(s)), None)
            if scale and len(observed) > 1:
                category_labels = [v for v in scale if v in observed]
                conversion = {v: category_labels.index(v.lower().strip()) for v in values}
                values = [conversion[v] for v in values]
                levels = [str(i) for i in range(len(category_labels))]
                kind = "ordinal"
                warnings.append("Category order was recognized from conventional ordered labels.")
            else:
                raise ValueError("Values are nonnumeric and their statistical ordering cannot be established.")
        else:
            raise ValueError("The metric contains mixed or structured values; no scalar analysis is appropriate.")
    if kind == "ordinal":
        if len(levels) < 2 or len(set(levels)) != len(levels):
            raise ValueError("An ordered metric requires at least two distinct ordered categories.")
        if any(str(v) not in levels for v in values):
            raise ValueError("Observed categories conflict with the metric's declared order.")
        if any(level not in {str(value) for value in values} for level in levels):
            raise ValueError(
                "Some declared ordered categories have no observations; the full ordinal model is not estimable."
            )
        families = ["ordinal_logit"]
        rationale = "Ordered categories are analyzed with a cumulative-logit model."
    else:
        y = np.asarray(values, float)
        integer = bool(np.all(y == np.round(y)))
        if kind == "auto":
            continuous_unit = str(meta.get("unit", "")).lower() in {
                "usd",
                "seconds",
                "ms",
                "milliseconds",
                "minutes",
                "hours",
                "celsius",
                "kelvin",
                "kg",
                "mg",
            }
            if denominator:
                kind = "proportion"
            elif meta.get("value_type", meta.get("valueType")) == "boolean" or all(isinstance(v, bool) for v in values):
                kind = "binary"
            elif continuous_unit or re.search(r"\b(duration|latency|cost|temperature|distance)\b", clues):
                kind = "continuous"
            elif re.search(r"\b(rating|likert|severity|satisfaction)\b", clues) and integer:
                kind = "ordinal"
                levels = [str(v) for v in sorted(set(values))]
            elif set(y) <= {0, 1}:
                kind = "binary"
            elif integer and np.all(y >= 0):
                kind = "count"
            elif np.all((y >= 0) & (y <= 1)):
                kind = "proportion"
            else:
                kind = "continuous"
            warnings.append(f"Measurement type detected as {kind} from producer context and observed values.")
        if kind == "ordinal":
            families = ["ordinal_logit"]
            rationale = "Ordered numeric ratings are analyzed with a cumulative-logit model."
        elif denominator:
            kind = "proportion"
            families = ["binomial"]
            rationale = "Success counts and their trial totals are analyzed together with a binomial model."
        elif kind == "binary":
            if not set(y) <= {0, 1}:
                raise ValueError("A binary metric contains values other than zero and one.")
            families = ["binomial"]
            rationale = "Binary outcomes are analyzed with a binomial model."
        elif kind == "count":
            if not (integer and np.all(y >= 0)):
                raise ValueError("A count metric contains negative or fractional values.")
            families = ["poisson", "negbin"]
            zero_probability = float(np.exp(-y.mean()))
            zero_se = np.sqrt(zero_probability * (1 - zero_probability) / len(y))
            if len(y) >= 40 and np.mean(y == 0) > zero_probability + 3 * zero_se:
                families += ["zip", "zinb"]
            rationale = "Nonnegative counts are compared using Poisson and overdispersed negative-binomial models."
        elif kind == "proportion":
            if not np.all((y >= 0) & (y <= 1)):
                raise ValueError("A proportion metric contains values outside zero to one.")
            if set(y) <= {0, 1}:
                kind = "binary"
                families = ["binomial"]
                rationale = "Only zero/one outcomes are observed, so the response is analyzed as binary."
            else:
                families = ["zoib"] if np.any((y == 0) | (y == 1)) else ["beta"]
                rationale = (
                    "Bounded fractional values use beta regression, with a boundary component for exact zero/one."
                )
        elif kind == "continuous":
            families = ["gamma", "lognormal", "gaussian"] if np.all(y > 0) else ["gaussian"]
            if np.all(y >= 0) and np.any(y == 0):
                families = ["gamma_hurdle"]
            rationale = "Candidate distributions follow the metric's numeric support; usable fits are compared by AIC."
        else:
            raise ValueError(f"Unsupported measurement construction: {kind}.")
    return dict(
        measurement_type=kind,
        ordinal_levels=levels,
        denominator=denominator,
        candidates=families,
        rationale=rationale,
        warnings=warnings,
        metadata=meta,
        conversion=conversion,
        category_labels=category_labels,
    )


def metric_information(source: dict, metric: str, plan: dict | None = None) -> dict:
    """Display declared meaning without inventing a definition from a metric name."""
    metadata = source.get("metric_metadata", {}).get(metric, {})
    description = metadata.get("description")
    unit = metadata.get("unit")
    if metadata.get("kind") == "runtime":
        from asaree.services.metrics import METRIC_CATALOG

        key = metadata.get("output_key", metadata.get("catalogKey"))
        builtin = next((entry for entry in METRIC_CATALOG if entry["key"] == key), {})
        description = description or builtin.get("shortDescription")
        unit = unit or builtin.get("unit")
    coding = []
    if plan and plan["measurement_type"] == "ordinal":
        labels = plan.get("category_labels") or plan["ordinal_levels"]
        coding.append("Ordered categories (lowest first): " + " → ".join(labels) + ".")
    if plan and plan["measurement_type"] in {"binary", "ordinal"}:
        mapping = plan.get("conversion", {})
        if not mapping and plan["measurement_type"] == "ordinal":
            mapping = {label: index for index, label in enumerate(plan["ordinal_levels"])}
        if mapping:
            coding.append(
                "Category coding: " + "; ".join(f"{label} = {code:g}" for label, code in mapping.items()) + "."
            )
        if plan["measurement_type"] == "binary":
            coding.append("Reported probabilities refer to the category coded 1.")
    return dict(
        description=description.strip()
        if isinstance(description, str) and description.strip()
        else "Description not provided.",
        unit=unit.strip() if isinstance(unit, str) and unit.strip() else None,
        coding=coding,
    )


def unavailable(source: dict, config: dict, reason: str) -> dict:
    observed = sum(row.get("metrics", {}).get(config["metric"]) is not None for row in source["rows"])
    return dict(
        family="unavailable",
        validity="unavailable",
        tables={},
        notes=[reason],
        n_observations=observed,
        n_excluded=len(source["rows"]) - observed,
        source_fingerprint=source["fingerprint"],
        summary=dict(
            version=REPORT_VERSION,
            metric=config["metric"],
            status="unavailable",
            metric_info=metric_information(source, config["metric"]),
            description=("SYNTHETIC DEMONSTRATION. " if source.get("demo") else "")
            + "A reliable statistical comparison could not be produced.",
            findings=[],
            effects=[],
            tests=[],
            warnings=[reason],
            methods=[
                "Inspected the declared metric, producer context, observed values, and experimental design. "
                "No inferential result is reported."
            ],
            provenance=dict(
                fingerprint=source["fingerprint"],
                design_revision=source.get("design_revision"),
                observed=observed,
                policy_version=REPORT_VERSION,
            ),
        ),
    )


def automatic_inputs(source: dict, config: dict, plan: dict) -> tuple[dict, dict, dict]:
    """Resolve the exact analyzed rows, shared with saved-report chart recovery."""
    observed_levels = {}
    for row in source["rows"]:
        for key, value in row.get("factors", {}).items():
            if value is not None:
                observed_levels.setdefault(key, set()).add(json.dumps(value, sort_keys=True, default=str))
    factors = config.get("factors") or sorted(key for key, levels in observed_levels.items() if len(levels) > 1)
    if not factors or len(factors) > 6:
        raise ValueError("Analysis requires between one and six varying experimental factors.")
    treatment_labels = factor_labels(source, factors)
    resolved = dict(
        config,
        factors=factors,
        measurement_type=plan["measurement_type"],
        ordinal_levels=plan["ordinal_levels"],
        denominator=plan["denominator"],
        robust=True,
    )
    converted_source = dict(
        source,
        rows=[
            dict(
                row,
                factors={
                    name: treatment_labels[name].get(
                        json.dumps(row.get("factors", {}).get(name), sort_keys=True, default=str)
                    )
                    for name in factors
                },
                metrics={
                    **row["metrics"],
                    config["metric"]: plan["conversion"].get(
                        str(row["metrics"].get(config["metric"])), row["metrics"].get(config["metric"])
                    ),
                },
            )
            for row in source["rows"]
        ],
    )
    return converted_source, resolved, treatment_labels


def analyze_automatic(source: dict, config: dict) -> dict:
    """Build a plan, fit supported candidates, and expose only validated inference."""
    from asaree.services.stats_source import json_safe, prepare_frame
    from asaree.stats.custom import analyze_custom

    try:
        plan = plan_metric(source, config["metric"])
        if config["metric"] in source.get("metric_conflicts", []):
            raise ValueError(
                "The metric's recorded definition changed between runs; these measurements cannot be pooled."
            )
        if len(source.get("protocol_revision_ids", [])) > 1:
            raise ValueError("Runs span different published protocols; statistical pooling was withheld.")
        dependence = source.get("dependence", {})
        if dependence.get("paired") or dependence.get("clustered") or dependence.get("repeated"):
            raise ValueError(
                "Paired or repeated observations require a dependence model; independent-run tests withheld."
            )
        converted_source, resolved, treatment_labels = automatic_inputs(source, config, plan)
        factors = resolved["factors"]
        candidates, fitted = [], []
        candidate_families = list(plan["candidates"])
        for family in candidate_families:
            candidate_config = dict(resolved, family=family)
            try:
                frame = prepare_frame(converted_source, candidate_config)
                cell_counts = frame.groupby([f"factor_{i}" for i in range(len(factors))]).size()
                if cell_counts.min() < 3:
                    raise ValueError("At least three measured independent runs per observed condition are required.")
                if family in {"zoib", "gamma_hurdle", "betabinomial"}:
                    output = json_safe(analyze_custom(frame, candidate_config))
                    diagnostics = output["tables"]["diagnostics"][0]
                    valid = (
                        diagnostics["converged"]
                        and diagnostics["hessian_positive_definite"]
                        and not diagnostics["severe"]
                    )
                    fit = dict(
                        aic=diagnostics["aic"],
                        usable=valid,
                        n=len(frame),
                        likelihood_basis="custom likelihood engine (all parameters at their MLE)",
                        exclusion_reason="" if valid else "fit failed convergence, Hessian or diagnostic checks",
                    )
                else:
                    output = json_safe(analyze(frame, candidate_config))
                    # AIC is already on the response scale with every estimated parameter counted.
                    fit = output["tables"].get("model_fit", [{}])[0]
                valid = bool(fit.get("usable"))
                aic = fit.get("aic") if number(fit.get("aic")) else None
                candidates.append(
                    dict(
                        family=family,
                        name=DISTRIBUTION_NAMES.get(family, family),
                        usable=valid,
                        aic=aic,
                        n=fit.get("n", len(frame)),
                        n_parameters=fit.get("n_parameters"),
                        likelihood_basis=fit.get("likelihood_basis"),
                        **({} if valid else {"reason": fit.get("exclusion_reason") or "fit failed reliability checks"}),
                    )
                )
                if not valid and family == "binomial" and plan["denominator"]:
                    candidate_families.append("betabinomial")
                if valid:
                    fitted.append((aic, family, output, frame))
            except (ValueError, RuntimeError, KeyError, np.linalg.LinAlgError, FloatingPointError) as exc:
                candidates.append(
                    dict(family=family, name=DISTRIBUTION_NAMES.get(family, family), usable=False, reason=str(exc))
                )
        if not fitted:
            reason = "; ".join(f"{r['family']}: {r.get('reason', 'fit failed reliability checks')}" for r in candidates)
            result = unavailable(source, config, f"No usable model. {reason}")
            result["summary"]["provenance"]["candidates"] = candidates
            result["summary"]["methods"].append(
                "Attempted models: " + ", ".join(row["family"] for row in candidates) + ". None passed validation."
            )
            return result
        ranked = [row for row in fitted if row[0] is not None]
        if ranked:
            _, family, output, frame = min(ranked, key=lambda r: r[0])
            rule = "Selected the usable candidate with the lowest AIC."
        else:
            # An exact fit makes continuous likelihoods unbounded, so AIC cannot rank candidates.
            _, family, output, frame = fitted[0]
            rule = (
                "AIC is undefined for every usable candidate (exact fit), so the first usable candidate in the "
                "measurement plan's order was used for descriptive estimates."
            )
        sizes = {row.get("n") for row in candidates if row.get("aic") is not None}
        if len(sizes) > 1:
            raise ValueError("Candidate models were fitted to different observations; AIC is not comparable.")
        best = min((row["aic"] for row in candidates if row["usable"] and row.get("aic") is not None), default=None)
        for row in candidates:
            row["selected"] = row["family"] == family
            ranked_row = row["usable"] and best is not None and row.get("aic") is not None
            row["delta_aic"] = row["aic"] - best if ranked_row else None
            row["status"] = "Selected" if row["selected"] else "Usable, not selected" if row["usable"] else "Excluded"
        selected_config = dict(resolved, family=family)
        if family not in {"zoib", "gamma_hurdle", "betabinomial"}:
            # Select once, then resample only the selected family, never every candidate.
            from asaree.services.stats_resampling import native_permutations

            output = json_safe(analyze(frame, dict(selected_config, _resample=True)))
            try:
                permutations, permutation_reason = native_permutations(frame, selected_config)
            except (ValueError, RuntimeError, np.linalg.LinAlgError, FloatingPointError) as exc:
                permutations, permutation_reason = [], f"Permutation check unavailable: {exc}"
            output["tables"]["permutation_tests"] = permutations
            if permutation_reason:
                output["permutation_reason"] = permutation_reason
        elif resolved.get("denominator") and frame.trials.nunique() != 1:
            output["tables"]["permutation_tests"] = []
            output["permutation_reason"] = (
                "Permutation check unavailable: unequal trial totals do not justify exchanging proportions."
            )
        if family in {"zoib", "gamma_hurdle", "betabinomial"}:
            for row in output["tables"].get("permutation_tests", []):
                if ":" in row["term"]:
                    row.update(
                        p_perm=None,
                        p_holm=None,
                        reason=(
                            "Interaction permutation withheld: the custom multipart model has no exchangeable "
                            "residual, and null-model simulation is not implemented for it."
                        ),
                    )
        else:
            # Main-effect permutations and interaction resampling form one Holm family.
            rows = list(output["tables"].get("permutation_tests", []))
            rows += [
                dict(row, n_perm=row.get("completed", 0)) for row in output["tables"].get("interaction_resampling", [])
            ]
            valid = [i for i, row in enumerate(rows) if number(row.get("p_perm"))]
            adjusted = multipletests([rows[i]["p_perm"] for i in valid], method="holm")[1] if valid else []
            for row in rows:
                row["p_holm"] = None
            for i, value in zip(valid, adjusted, strict=True):
                rows[i]["p_holm"] = float(value)
            output["tables"]["permutation_tests"] = rows
        warnings = list(plan["warnings"])
        if source.get("demo"):
            warnings.insert(0, "DEMONSTRATION: synthetic data, not results from a real experiment.")
        warnings.append("Inference assumes independent runs and is conditional on the observed experimental inputs.")
        if source.get("excluded_row_replicates"):
            warnings.append(
                f"{source['excluded_row_replicates']} row-mode replicates were excluded to avoid pseudoreplication."
            )
        excluded = len(source["rows"]) - len(frame)
        if excluded:
            warnings.append(f"{excluded} runs lacked a usable metric or trial total; missingness may affect results.")
        if len(fitted) > 1:
            warnings.append(
                "Model selection used these observations; reported intervals do not include selection uncertainty."
            )
        if family in {"zoib", "gamma_hurdle", "betabinomial"}:
            warnings.append("A specialized custom model was used for this measurement construction.")
        if resolved["n_boot"] < 1000:
            warnings.append("Exploratory simulation count: confidence intervals need more draws for final reporting.")
        counts = frame.groupby([f"factor_{i}" for i in range(len(factors))]).size()
        if counts.max() != counts.min():
            warnings.append("Run counts differ across conditions; comparisons weight conditions equally.")
        output.update(
            validity="valid", n_observations=len(frame), n_excluded=excluded, source_fingerprint=source["fingerprint"]
        )
        cell_columns = [f"factor_{i}" for i in range(len(factors))]
        constant = int((frame.groupby(cell_columns).outcome.nunique() == 1).sum())
        if constant and not output.get("inference_reason"):
            warnings.append(
                f"In {constant} of {len(counts)} conditions every replicate had the same value. The robust "
                "covariance treats those condition means as having no sampling variability, so tests and intervals "
                "that rely on them may be too narrow."
            )
        output["tables"]["automatic_candidates"] = candidates
        levels = {name: sorted(frame[f"factor_{i}"].unique()) for i, name in enumerate(factors)}
        model_selection = selection_summary(output, family, candidates, rule, constant, len(counts))
        output["summary"] = build_summary(
            output, source, resolved, plan, family, warnings, model_selection=model_selection, levels=levels
        )
        if source.get("demo"):
            output["summary"]["description"] = "SYNTHETIC DEMONSTRATION. " + output["summary"]["description"]
        output["summary"]["provenance"]["treatment_labels"] = {
            name: list(mapping.values()) for name, mapping in treatment_labels.items()
        }
        from asaree.services.stats_distribution import observed_distribution

        output["summary"]["distribution"] = observed_distribution(
            frame.outcome.to_numpy(float),
            (plan.get("category_labels") or plan.get("ordinal_levels"))
            if plan["measurement_type"] == "ordinal"
            else None,
        )
        return json_safe(output)
    except (ValueError, KeyError) as exc:
        return unavailable(source, config, str(exc))


def selection_summary(output, family, candidates, rule, constant_cells, n_cells):
    """Selected model, candidate AIC table and model-appropriate diagnostics."""
    fit = next(iter(output["tables"].get("model_fit", [])), {})
    diagnostic = next(iter(output["tables"].get("diagnostics", [])), {})
    selected = next(row for row in candidates if row["family"] == family)
    rows = []

    def add(label, value, note):
        if number(value):
            rows.append(dict(label=label, value=float(value), note=note))

    if family in ("gaussian", "lognormal"):
        scale = " (log scale)" if family == "lognormal" else ""
        add("Residual skewness" + scale, diagnostic.get("residual_skewness"), "0 for symmetric residuals.")
        add(
            "Residual normality p" + scale,
            diagnostic.get("residual_normality_p"),
            "D'Agostino–Pearson test; HC3 inference does not require normality.",
        )
    if family in ("poisson", "binomial", "negbin", "gamma"):
        add(
            "Pearson dispersion",
            diagnostic.get("pearson_dispersion"),
            "About 1 when the variance function fits; Poisson/binomial fits above 3 are excluded."
            if family in ("poisson", "binomial")
            else "Scale estimate on the model's variance function.",
        )
    if family == "negbin":
        add("NB2 dispersion α", diagnostic.get("negbin_alpha"), "Variance = mean + α·mean².")
    if family == "gamma":
        add(
            "Gamma shape (ML)",
            diagnostic.get("gamma_shape_ml"),
            "Coefficient of variation within a condition ≈ 1/√shape.",
        )
    covariance = output.get("covariance") or {}
    add("Maximum leverage", covariance.get("max_leverage"), "HC3 divides each score by (1 − leverage).")
    if fit.get("exact_fit"):
        rows.append(dict(label="Residual variation", value=None, note="None: every run equals its fitted value."))
    if constant_cells:
        rows.append(
            dict(
                label="Conditions with identical replicates",
                value=None,
                note=f"{constant_cells} of {n_cells}",
            )
        )
    return dict(
        selected=family,
        selected_name=DISTRIBUTION_NAMES.get(family, family),
        aic=selected.get("aic"),
        n=selected.get("n"),
        n_parameters=selected.get("n_parameters"),
        likelihood_basis=selected.get("likelihood_basis"),
        rule=rule,
        candidates=candidates,
        diagnostics=rows,
        note="Lower AIC means better relative fit among candidates fitted to the same runs with comparable "
        "response-scale likelihoods; it does not show that the selected model fits adequately in absolute terms.",
    )


def build_summary(output, source, config, plan, family, warnings, model_selection=None, levels=None):
    """One interpretation shared by the UI and HTML export; no generated claims."""
    factors = config["factors"]
    aliases = {f"factor_{i}": name for i, name in enumerate(factors)}
    aliases.update({f"C(f{i}, Sum)": name for i, name in enumerate(factors)})
    metric = config["metric"]
    unit = plan["metadata"].get("unit")
    levels = levels or {name: [] for name in factors}

    def label(value):
        text = str(value)
        for alias in sorted(aliases, key=len, reverse=True):
            text = text.replace(alias, aliases[alias])
        return text

    def recorded_method(row):
        raw = row.get("p_method") or "Test method not recorded"
        if raw.startswith("parametric bootstrap LRT"):
            return raw.replace("parametric bootstrap LRT", "Parametric-bootstrap likelihood-ratio test", 1)
        if raw.startswith("HC3 delta-method Wald"):
            return "HC3 delta-method Wald z-test (response-scale contrast)"
        if raw.startswith("HC3 Wald"):
            return raw.replace("HC3 Wald", "HC3 Wald chi-square test", 1)
        return raw

    specialized = family in {"zoib", "gamma_hurdle", "betabinomial"}
    covariance = output.get("covariance") or (
        dict(requested="HC3", computed="HC3", implementation="custom likelihood engine (generalized HC3)")
        if specialized
        else dict(requested="HC3", computed=None)
    )
    estimator = covariance.get("computed") or "unavailable"
    scale_text = LINK_SCALES.get(family, ("custom", "model scale"))[1]
    inference_reason = output.get("inference_reason")
    if specialized:
        quantity = output["tables"].get("quantities", [{}])[0]
        ok, failed = quantity.get("n_boot_ok", 0), quantity.get("n_boot_failed", 0)
        bootstrap = dict(
            method="Parametric-bootstrap percentile intervals; fixed selected model",
            requested=config["n_boot"],
            attempted=ok + failed,
            successful=ok,
            failed=failed,
            status="completed" if ok >= 0.9 * config["n_boot"] else "Too many failed refits",
            seed=config["seed"],
        )
    else:
        bootstrap = next(
            iter(output["tables"].get("resampling", [])),
            dict(requested=config["n_boot"], attempted=0, successful=0, failed=0, status="Unavailable"),
        )
    permutation_rows = {label(row["term"]): row for row in output["tables"].get("permutation_tests", [])}
    if bootstrap["status"] != "completed" and not inference_reason:
        warnings.append(f"Bootstrap intervals unavailable: {bootstrap['status']}. Analytic inference is retained.")
    if output.get("permutation_reason"):
        warnings.append(output["permutation_reason"])
    if inference_reason:
        warnings.append(inference_reason + " Tests, intervals and resampling checks are marked unavailable.")
    if covariance.get("computed") == "HC0":
        warnings.append(f"HC0 robust covariance was used. {covariance.get('reason', '')}".strip())

    effects = []
    for row in output["tables"].get("contrasts", []):
        estimate, low, high = (row.get(k) for k in ("estimate", "ci_low", "ci_high"))
        p = row.get("p_holm", row.get("p_primary_holm"))
        if specialized:
            low, high = row.get("analytic_ci_low"), row.get("analytic_ci_high")
            p = row.get("p_response_holm")
        usable = all(number(v) for v in (estimate, low, high, p))
        scale = row.get("scale", "difference")
        text = comparison_text(row, family, levels, metric, unit) if not specialized else None
        if not specialized and "link-scale contrast = 0" in row.get("null_hypothesis", ""):
            purpose = (
                "Tests whether the selected conditions differ on the model's link scale; "
                "the estimated effect is displayed on the measurement scale."
            )
        elif text and text["question"]:
            purpose = f"{text['question']} Null: {text['null']}."
        elif scale in {"difference_in_differences", "ratio_of_ratios"}:
            purpose = (
                "Checks whether the difference between conditions changes across the other factor "
                f"(null interaction = {1 if scale == 'ratio_of_ratios' else 0})."
            )
        else:
            purpose = (
                "Checks whether the first condition's predicted average differs from the second "
                f"(null {'ratio = 1' if scale == 'ratio' else 'difference = 0'})."
            )
        method = recorded_method(row)
        interval = row.get("ci_method")
        if specialized:
            method = "HC3 delta-method Wald z-test (response-scale contrast)"
            interval = (
                "Log-scale delta-method confidence interval"
                if scale in {"ratio", "ratio_of_ratios"}
                else "Delta-method confidence interval"
            )
        boot_low, boot_high = (
            (row.get("ci_low"), row.get("ci_high"))
            if specialized
            else (row.get("bootstrap_ci_low"), row.get("bootstrap_ci_high"))
        )
        boot_usable = bootstrap["status"] == "completed" and all(number(v) for v in (boot_low, boot_high))
        boot_status = "completed" if boot_usable else bootstrap["status"]
        if boot_usable and not inference_reason and abs(boot_high - boot_low) <= 1e-9 * max(1.0, abs(boot_high)):
            # Every refit reproduced the same value: resampling saw no variation, not certainty.
            boot_usable = False
            boot_status = "Unavailable: every bootstrap refit gave the same value (no within-condition variation)"
        if inference_reason:
            boot_usable, boot_status = False, f"Unavailable: {inference_reason}"
        null = 1 if scale in {"ratio", "ratio_of_ratios"} else 0
        analytic_excludes = usable and (low > null or high < null)
        boot_excludes = boot_usable and (boot_low > null or boot_high < null)
        unavailable_reason = row.get("unavailable_reason") or (inference_reason if not usable else None)
        effects.append(
            dict(
                comparison=text["label"] if text else label(row.get("label", "")),
                estimate=estimate if number(estimate) else None,
                ci_low=low if usable else None,
                ci_high=high if usable else None,
                p_adjusted=p if usable else None,
                method=method if usable else "Inference unavailable",
                purpose=purpose,
                target=text["target"] if text else None,
                averaged_over=text["averaged_over"] if text else None,
                null_hypothesis=text["null"] if text else None,
                interpretation=text["interpretation"] if text else None,
                question=text["question"] if text else None,
                holm_family=row.get("family"),
                factors=row.get("factors"),
                covariance=estimator if usable else None,
                interval_method=interval if usable else None,
                unavailable_reason=None if usable else unavailable_reason or "Inference unavailable for this fit.",
                bootstrap_ci_low=boot_low if boot_usable else None,
                bootstrap_ci_high=boot_high if boot_usable else None,
                bootstrap_status=boot_status,
                interval_agreement=(
                    "Intervals agree" if analytic_excludes == boot_excludes else "Intervals differ; investigate"
                )
                if usable and boot_usable
                else "Interval comparison unavailable",
                scale=row.get("scale", "difference"),
                unit=unit if str(row.get("scale", "difference")).startswith("difference") else None,
                result="Evidence of a difference"
                if usable and p < 0.05
                else "No clear evidence of a difference"
                if usable
                else "Inference unavailable",
            )
        )
    family_sizes = {}
    for effect in effects:
        if effect["p_adjusted"] is not None:
            key = INTERACTION_SIGN.join(effect["factors"] or [effect["holm_family"] or "other"])
            family_sizes[key] = family_sizes.get(key, 0) + 1

    tests = []
    term_rows = [row for row in output["tables"].get("tests", []) if row.get("family", "terms") == "terms"]
    if specialized:
        from asaree.stats.inference import holm

        analytic_term_p = holm(np.array([row.get("p_wald_hc3", np.nan) for row in term_rows], float))
    else:
        analytic_term_p = [row.get("p_holm") for row in term_rows]
    available_tests = sum(number(p) for p in analytic_term_p)
    for row, p in zip(term_rows, analytic_term_p, strict=True):
        term = label(row.get("term", row.get("hypothesis", "")))
        term_factors = term.split(":")
        others = [f for f in factors if f not in term_factors]
        question, null, interpretation = factor_test_text(term_factors, others, levels, family, metric)
        permutation = permutation_rows.get(term, {})
        permutation_p = permutation.get("p_holm")
        kind = permutation.get("kind") or "permutation"
        purpose = f"{question} Null: {null}"
        if "in every model part (joint)" in row.get("null_hypothesis", ""):
            purpose += " Coefficients are tested jointly across the model's parts."
        method = ("HC3 Wald chi-square test" if specialized else recorded_method(row)) if number(p) else None
        resampling_name = "parametric bootstrap check" if kind == "parametric bootstrap" else "permutation check"
        resampling_label = (
            "Null-model parametric bootstrap of the HC3 Wald statistic"
            if kind == "parametric bootstrap"
            else "Freedman–Lane permutation of the HC3 Wald statistic"
            if len(term_factors) > 1
            else "Label permutation within strata; working-scale OLS F (sensitivity check)"
        )
        tests.append(
            dict(
                effect=term,
                label=INTERACTION_SIGN.join(term_factors),
                p_adjusted=p if number(p) else None,
                statistic=row.get("statistic") if number(row.get("statistic")) else None,
                df=row.get("df"),
                method=method or "Inference unavailable",
                purpose=purpose,
                question=question,
                null_hypothesis=null,
                interpretation=interpretation,
                covariance=estimator if method else None,
                scale=scale_text,
                averaged_over=averaged_phrase(others, levels),
                unavailable_reason=None
                if method
                else row.get("unavailable_reason") or inference_reason or "Inference unavailable for this fit.",
                permutation_p_adjusted=permutation_p if number(permutation_p) else None,
                permutation_method=label(permutation["method"]) if permutation.get("method") else None,
                resampling_kind=kind if permutation else None,
                resampling_name=resampling_name,
                resampling_label=resampling_label
                if permutation and permutation.get("method") != "Permutation withheld"
                else None,
                permutation_status="completed"
                if number(permutation_p)
                else permutation.get("reason") or output.get("permutation_reason", "Unavailable"),
                permutation_result=(
                    f"Evidence in {resampling_name}"
                    if permutation_p < 0.05
                    else f"No clear evidence in {resampling_name}"
                )
                if number(permutation_p)
                else "Unavailable",
                result="Evidence of an effect"
                if number(p) and p < 0.05
                else "No clear evidence of an effect"
                if number(p)
                else "Inference unavailable",
            )
        )
    if any(e["p_adjusted"] is None for e in effects) and not inference_reason:
        warnings.append("Some comparisons have no reliable uncertainty estimate; no conclusion is drawn for them.")

    def common(values):
        values = {v for v in values if v}
        return values.pop() if len(values) == 1 else None

    resampled_tests = [t for t in tests if t["permutation_p_adjusted"] is not None]
    column_methods = dict(
        tests=dict(
            p_adjusted=(
                f"{common(t['method'] for t in tests if t['p_adjusted'] is not None)} · "
                + holm_note(available_tests, "factor tests")
            )
            if common(t["method"] for t in tests if t["p_adjusted"] is not None)
            else None,
            permutation="Holm across " + f"{len(resampled_tests)} resampling p-values; procedure per row"
            if resampled_tests
            else None,
        ),
        effects=dict(
            p_adjusted=(
                f"{common(e['method'] for e in effects if e['p_adjusted'] is not None)} · "
                "Holm within each comparison family"
            )
            if common(e["method"] for e in effects if e["p_adjusted"] is not None)
            else None,
            interval=common(e["interval_method"] for e in effects),
            bootstrap=f"{bootstrap.get('method', 'Bootstrap')} · {bootstrap['successful']}/{bootstrap['requested']} "
            "successful refits"
            if bootstrap["status"] == "completed"
            else None,
        ),
    )

    # Structured technical details: one entry per named test, comparison and check.
    selection = model_selection or {}
    details = []
    selection_items = [
        plan["rationale"],
        selection.get("rule", ""),
        "Lower AIC means better relative fit among comparable candidates fitted to the same runs; "
        "it does not show that the selected model fits adequately in absolute terms.",
        f"Model terms: {', '.join(factors)} with interactions through order {config['max_order']}, sum-to-zero coding.",
    ]
    if sum(bool(row.get("usable")) for row in selection.get("candidates", [])) > 1:
        selection_items.append(
            "Model selection used these observations; reported intervals do not include selection uncertainty."
        )
    details.append(dict(title="Model selection", items=[item for item in selection_items if item]))
    test_columns = [
        ("target", "Target"),
        ("method", "Method"),
        ("covariance", "Covariance"),
        ("scale", "Scale"),
        ("null", "Null hypothesis"),
        ("interpretation", "Interpretation"),
    ]
    details.append(
        dict(
            title="Factor tests",
            items=[
                "Each test is a Wald chi-square test of all coefficients belonging to one model term, using the "
                f"{estimator} robust covariance, on the model's scale: {scale_text}. For log-scale models the "
                "main-effect tests compare geometric, not arithmetic, averages over the other factors' settings, "
                "so they can differ slightly from the comparisons of predicted averages below."
            ],
            columns=test_columns,
            rows=[
                dict(
                    target=t["label"],
                    method=(
                        f"{t['method']}; {t['df']:g} df; statistic {t['statistic']:.4g}"
                        if t["statistic"] is not None and number(t.get("df"))
                        else t["method"]
                    ),
                    covariance=t["covariance"] or "—",
                    scale=t["scale"],
                    null=t["null_hypothesis"] + " " + t["averaged_over"],
                    interpretation=t["interpretation"]
                    if t["p_adjusted"] is not None
                    else f"Unavailable: {t['unavailable_reason']}",
                )
                for t in tests
            ],
        )
    )
    details.append(
        dict(
            title="Comparisons",
            items=[
                "Each comparison uses the model's predicted mean for every condition, averaged with equal weight "
                "over the settings named below. Ratios divide the first-listed setting by the second; differences "
                "subtract it. Ratio tests and intervals are computed for the log ratio by the delta method and "
                "back-transformed, so intervals stay positive."
            ],
            columns=[
                ("target", "Target"),
                ("averaged", "Averaged over"),
                ("method", "Method"),
                ("covariance", "Covariance"),
                ("null", "Null"),
                ("interpretation", "Interpretation"),
            ],
            rows=[
                dict(
                    target=f"{e['comparison']}: {e['target'] or ''}",
                    averaged=e["averaged_over"] or "—",
                    method=f"{e['method']}; {e['interval_method']}" if e["interval_method"] else e["method"],
                    covariance=e["covariance"] or "—",
                    null=e["null_hypothesis"] or "—",
                    interpretation=e["interpretation"]
                    if e["p_adjusted"] is not None
                    else f"Descriptive only. Unavailable: {e['unavailable_reason']}",
                )
                for e in effects
            ],
        )
    )
    permutation_details = []
    for t in tests:
        row = permutation_rows.get(t["effect"])
        if not row:
            continue
        requested = row.get("requested", config["n_perm"])
        completed = row.get("completed", row.get("n_perm", 0)) or 0
        failed = row.get("failed", 0) or 0
        statistic = row.get("statistic") or "Type III F (OLS, working scale)"
        if row.get("method") in ("Permutation withheld", "Withheld"):
            procedure = "Not run"
        elif row.get("kind") == "permutation" and ":" not in t["effect"]:
            procedure = (
                f"{label(row.get('method', ''))} (exact for the sharp null that {t['label']} has no effect at "
                f"any setting of the other factors); statistic: working-scale OLS F, a sensitivity check, not the "
                f"fitted model's Wald statistic"
            )
        else:
            procedure = f"{label(row.get('method', ''))}; statistic: {statistic} (the analytic test's own statistic)"
        if number(row.get("p_perm")):
            outcome = (
                f"{requested} requested, {completed} completed, {failed} failed. Monte Carlo p = "
                f"(exceedances + 1)/(completed + 1) = {row['p_perm']:.4g} (minimum {1 / (completed + 1):.2g}); "
                f"Holm-adjusted {row.get('p_holm', float('nan')):.4g}."
            )
        else:
            outcome = f"{requested} requested, {completed} completed, {failed} failed. {row.get('reason') or ''}"
        permutation_details.append(
            dict(
                target=t["label"],
                procedure=procedure,
                null=row.get("null_hypothesis")
                or f"{t['label']} has no effect on the distribution of {metric} at any setting of the other factors.",
                assumptions=row.get("assumptions")
                or "Independent runs whose working-scale values are exchangeable within each stratum under the null.",
                draws=outcome,
                seed=f"{row.get('seed', config['seed'])} / stream {row.get('seed_stream', '—')}",
            )
        )
    details.append(
        dict(
            title="Permutation and null-resampling checks",
            items=[
                "Main effects: labels are exchanged within strata of the other factors (whole runs), with a "
                "working-scale OLS F statistic. Interactions: the fitted model's own HC3 Wald statistic is "
                "resampled around the fitted no-interaction model, by Freedman–Lane permutation when errors are "
                "exchangeable on some scale, otherwise by a parametric (null-model) bootstrap.",
                "Resampling p-values for the factor tests are Holm-adjusted together, separately from the analytic "
                "p-values. Agreement between methods is descriptive, not proof of validity.",
            ],
            columns=[
                ("target", "Target"),
                ("procedure", "Procedure"),
                ("null", "Null hypothesis"),
                ("assumptions", "Required assumptions"),
                ("draws", "Draws and p-value"),
                ("seed", "Seed"),
            ],
            rows=permutation_details,
        )
    )
    bootstrap_items = [
        f"{bootstrap.get('method', 'Bootstrap')}: {bootstrap['requested']} requested, {bootstrap['attempted']} "
        f"attempted, {bootstrap['successful']} successful, {bootstrap['failed']} failed. Status: "
        f"{bootstrap['status']}. Seed {bootstrap.get('seed', config['seed'])}"
        + (f", stream {bootstrap['seed_stream']}" if bootstrap.get("seed_stream") else "")
        + ".",
        "Intervals require a completed run with at least 90% successful refits. The selected model family is held "
        "fixed, so model-selection uncertainty is not included.",
    ]
    if not specialized:
        bootstrap_items.append(
            "Whole runs are resampled with replacement within each observed condition and the selected model is "
            "refitted; factor values and trial totals stay attached to their runs. Intervals are the 2.5th and "
            "97.5th percentiles of the refitted comparison."
        )
    details.append(dict(title="Bootstrap intervals", items=bootstrap_items))
    holm_items = [
        "Analytic factor tests: " + holm_note(available_tests, "factor tests") + ".",
        "Resampling checks: " + holm_note(len(resampled_tests), "resampling p-values") + ".",
    ]
    for title, count in family_sizes.items():
        holm_items.append(f"Comparisons for {title}: " + holm_note(count, "comparisons") + ".")
    holm_items += [
        "“Analytic result” means the analytic Holm-adjusted p-value is below 0.05; it does not use the "
        "resampling or bootstrap results.",
        "Confidence intervals (analytic and bootstrap) are pointwise 95% intervals, not multiplicity-adjusted. "
        "Interval agreement compares whether both exclude the null value.",
        "Separate metrics are not corrected together; cross-metric findings are exploratory. A result without "
        "clear evidence does not show that an effect is zero, and statistical evidence is not practical importance.",
    ]
    details.append(dict(title="Multiplicity and interpretation", items=holm_items))

    methods = [plan["rationale"]]
    for section in details:
        methods.extend(f"{section['title']} — {item}" for item in section.get("items", []))
        for row in section.get("rows", []):
            methods.append(
                f"{section['title']} — "
                + "; ".join(f"{title}: {row[key]}" for key, title in section["columns"] if row.get(key))
            )
    if plan.get("conversion"):
        conversion = plan["conversion"]
        methods.append(
            "String conversion: "
            + (
                ", ".join(f"{label!r} → {value:g}" for label, value in conversion.items())
                if len(conversion) <= 8
                else f"Parsed {len(conversion)} distinct numeric text values as numbers; originals remain stored."
            )
        )
    if plan["ordinal_levels"]:
        methods.append(
            "Ordered categories (lowest first): "
            + ", ".join(plan.get("category_labels") or plan["ordinal_levels"])
            + ". Equal spacing is not assumed by the ordinal likelihood. Comparisons summarize expected "
            "category codes (1 through K), not distances in physical units; permutation checks use pooled ranks."
        )
    if plan["measurement_type"] == "binary" and plan.get("conversion"):
        methods.append("Reported probabilities refer to the category coded 1; the codes do not imply a numeric rating.")
    if specialized:
        methods.append(
            "Custom likelihood engine. "
            "Analytic comparison tests and intervals use the response-scale HC3 delta method; "
            "bootstrap intervals simulate from the fitted model and share its distributional assumptions."
        )
    else:
        methods.append(
            f"Robust covariance: {estimator} ("
            + (covariance.get("implementation") or covariance.get("reason") or "not computed")
            + "). statsmodels' cov_type='HC3' is HC0 for GLM and discrete models, so the sandwich is computed "
            "explicitly and the installed matrix is verified."
        )
    from asaree.services.stats_findings import describe_findings

    findings = describe_findings(tests)
    versions = {name: importlib.metadata.version(name) for name in ("numpy", "scipy", "statsmodels")}
    return dict(
        version=REPORT_VERSION,
        metric=metric,
        status="valid",
        metric_info=metric_information(source, metric, plan),
        description=f"Analyzed {len(source['rows']) - output['n_excluded']} runs for {metric} "
        f"as a {plan['measurement_type']} measurement.",
        findings=findings,
        effects=effects,
        tests=tests,
        warnings=warnings,
        methods=methods,
        details=details,
        column_methods=column_methods,
        model_fit=selection,
        covariance=covariance,
        inference_reason=inference_reason,
        resampling=dict(bootstrap=bootstrap, permutations=list(permutation_rows.values())),
        metric_definition=plan["metadata"],
        provenance=dict(
            fingerprint=source["fingerprint"],
            design_revision=source.get("design_revision"),
            seed=config["seed"],
            libraries=versions,
            policy_version=REPORT_VERSION,
            candidates=output["tables"]["automatic_candidates"],
            covariance=covariance,
        ),
    )

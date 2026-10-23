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

from asaree.stats_sm.analysis import analyze

REPORT_VERSION = 2
ORDERED_LABELS = (
    ("very low", "low", "medium", "high", "very high"),
    ("strongly disagree", "disagree", "neutral", "agree", "strongly agree"),
    ("poor", "fair", "good", "very good", "excellent"),
)


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
        unavailable=[],
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


def analyze_automatic(source: dict, config: dict) -> dict:
    """Build a plan, fit supported candidates, and expose only validated inference."""
    from asaree.services.stats_prototypes import compute, json_safe, prepare_frame

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
                    output = compute("stats", converted_source, candidate_config)
                    diagnostics = output["tables"]["diagnostics"][0]
                    valid = (
                        diagnostics["converged"]
                        and diagnostics["hessian_positive_definite"]
                        and not diagnostics["severe"]
                    )
                    fit = dict(aic=diagnostics["aic"], usable=valid)
                else:
                    output = json_safe(analyze(frame, candidate_config))
                    fit = output["tables"].get("model_fit", [{}])[0]
                    # OLS on log(y) reports a likelihood on the transformed scale.
                    # Apply the Jacobian before comparing with response-scale models.
                    if family == "lognormal" and fit.get("aic") is not None:
                        fit["aic"] += 2 * float(np.log(frame.outcome).sum())
                    if family in {"gaussian", "lognormal"} and fit.get("aic") is not None:
                        fit["aic"] += 2  # Count the estimated residual-variance parameter.
                valid = bool(fit.get("usable"))
                reason = next(
                    (r.get("flags") for r in output["tables"].get("diagnostics", []) if r.get("flags")),
                    "fit failed convergence or covariance checks",
                )
                candidates.append(
                    dict(family=family, usable=valid, aic=fit.get("aic"), **({"reason": reason} if not valid else {}))
                )
                if not valid and family == "binomial" and plan["denominator"]:
                    candidate_families.append("betabinomial")
                if valid:
                    fitted.append((float(fit["aic"]), family, output, frame))
            except (ValueError, RuntimeError, KeyError, np.linalg.LinAlgError, FloatingPointError) as exc:
                candidates.append(dict(family=family, usable=False, reason=str(exc)))
        if not fitted:
            reason = "; ".join(f"{r['family']}: {r.get('reason', 'fit failed reliability checks')}" for r in candidates)
            result = unavailable(source, config, f"No usable model. {reason}")
            result["summary"]["provenance"]["candidates"] = candidates
            result["summary"]["methods"].append(
                "Attempted models: " + ", ".join(row["family"] for row in candidates) + ". None passed validation."
            )
            return result
        _, family, output, frame = min(fitted, key=lambda r: r[0])
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
                            "Interaction permutation withheld: exchangeable additive errors are not established "
                            "for this non-Gaussian model."
                        ),
                    )
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
        output["tables"]["automatic_candidates"] = candidates
        output["summary"] = build_summary(output, source, resolved, plan, family, warnings)
        if source.get("demo"):
            output["summary"]["description"] = "SYNTHETIC DEMONSTRATION. " + output["summary"]["description"]
        output["summary"]["provenance"]["treatment_labels"] = {
            name: list(mapping.values()) for name, mapping in treatment_labels.items()
        }
        output["summary"]["distribution"] = dict(
            n=len(frame),
            minimum=float(frame.outcome.min()),
            maximum=float(frame.outcome.max()),
            mean=float(frame.outcome.mean()),
            sd=float(frame.outcome.std()),
            distinct=int(frame.outcome.nunique()),
            zeros=int((frame.outcome == 0).sum()),
        )
        return json_safe(output)
    except (ValueError, KeyError) as exc:
        return unavailable(source, config, str(exc))


def build_summary(output, source, config, plan, family, warnings):
    """One interpretation shared by the UI and HTML export; no generated claims."""
    factors = config["factors"]
    aliases = {f"factor_{i}": name for i, name in enumerate(factors)}
    aliases.update({f"C(f{i}, Sum)": name for i, name in enumerate(factors)})

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
            dict(
                requested=config["n_boot"],
                attempted=0,
                successful=0,
                failed=0,
                status="Unavailable",
            ),
        )
    permutation_rows = {label(row["term"]): row for row in output["tables"].get("permutation_tests", [])}
    if bootstrap["status"] != "completed":
        warnings.append(f"Bootstrap intervals unavailable: {bootstrap['status']}. Analytic inference is retained.")
    if output.get("permutation_reason"):
        warnings.append(output["permutation_reason"])

    effects = []
    for row in output["tables"].get("contrasts", []):
        estimate, low, high = (row.get(k) for k in ("estimate", "ci_low", "ci_high"))
        p = row.get("p_holm", row.get("p_primary_holm"))
        if specialized:
            low, high = row.get("analytic_ci_low"), row.get("analytic_ci_high")
            p = row.get("p_response_holm")
        usable = all(number(v) for v in (estimate, low, high, p))
        scale = row.get("scale", "difference")
        if not specialized and "link-scale contrast = 0" in row.get("null_hypothesis", ""):
            purpose = (
                "Tests whether the selected conditions differ on the model's link scale; "
                "the estimated effect is displayed on the measurement scale."
            )
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
        null = 1 if scale in {"ratio", "ratio_of_ratios"} else 0
        analytic_excludes = usable and (low > null or high < null)
        boot_excludes = boot_usable and (boot_low > null or boot_high < null)
        effects.append(
            dict(
                comparison=label(row.get("label", "")),
                estimate=estimate if number(estimate) else None,
                ci_low=low if usable else None,
                ci_high=high if usable else None,
                p_adjusted=p if usable else None,
                method=method if usable else "Inference unavailable",
                purpose=purpose,
                interval_method=interval if usable else None,
                bootstrap_ci_low=boot_low if boot_usable else None,
                bootstrap_ci_high=boot_high if boot_usable else None,
                bootstrap_status="completed" if boot_usable else bootstrap["status"],
                interval_agreement=(
                    "Intervals agree" if analytic_excludes == boot_excludes else "Intervals differ; investigate"
                )
                if usable and boot_usable
                else "Interval comparison unavailable",
                scale=row.get("scale", "difference"),
                unit=plan["metadata"].get("unit")
                if str(row.get("scale", "difference")).startswith("difference")
                else None,
                result="Evidence of a difference"
                if usable and p < 0.05
                else "No clear evidence of a difference"
                if usable
                else "Inference unavailable",
            )
        )
    tests = []
    term_rows = [row for row in output["tables"].get("tests", []) if row.get("family", "terms") == "terms"]
    if specialized:
        from asaree.stats.inference import holm

        analytic_term_p = holm(np.array([row.get("p_wald_hc3", np.nan) for row in term_rows], float))
    else:
        analytic_term_p = [row.get("p_holm") for row in term_rows]
    for row, p in zip(term_rows, analytic_term_p, strict=True):
        term = row.get("term", row.get("hypothesis", ""))
        permutation = permutation_rows.get(label(term), {})
        permutation_p = permutation.get("p_holm")
        purpose = (
            "Checks whether the effect of one factor depends on the others in this interaction."
            if ":" in term
            else "Checks whether this factor contributes to the fitted model after accounting for other included terms."
        )
        if "in every model part (joint)" in row.get("null_hypothesis", ""):
            purpose += " Coefficients are tested jointly across the model's parts."
        tests.append(
            dict(
                effect=label(term),
                p_adjusted=p if number(p) else None,
                method=("HC3 Wald chi-square test" if specialized else recorded_method(row))
                if number(p)
                else "Inference unavailable",
                purpose=purpose,
                permutation_p_adjusted=permutation_p if number(permutation_p) else None,
                permutation_method=label(permutation["method"]) if permutation.get("method") else None,
                permutation_status="completed"
                if number(permutation_p)
                else permutation.get("reason") or output.get("permutation_reason", "Unavailable"),
                permutation_result="Evidence in sensitivity check"
                if number(permutation_p) and permutation_p < 0.05
                else "No clear evidence in sensitivity check"
                if number(permutation_p)
                else "Unavailable",
                result="Evidence of an effect"
                if number(p) and p < 0.05
                else "No clear evidence of an effect"
                if number(p)
                else "Inference unavailable",
            )
        )
    if any(e["p_adjusted"] is None for e in effects):
        warnings.append("Some comparisons have no reliable uncertainty estimate; no conclusion is drawn for them.")
    methods = [
        plan["rationale"],
        f"Selected model: {family}. Factors: {', '.join(factors)}. "
        f"Interactions included through order {config['max_order']}.",
        "Comparisons are equally weighted differences or ratios of predicted means; 95% confidence intervals.",
        "Factor tests evaluate model terms; comparisons evaluate predicted overall means. "
        "These can answer different questions for nonlinear or multipart models.",
        "Differences subtract the second listed condition from the first; ratios divide the first by the second. "
        "The null value is 0 for differences and 1 for ratios. Interactions compare effects across settings.",
        "Holm correction is applied across factor tests and within each factor's comparison family. "
        "Separate metrics are not corrected together; cross-metric findings are exploratory.",
        "Analytic and permutation results are reported independently; neither is selected based on significance.",
        "Permutation p-values are separately Holm-adjusted working-scale OLS sensitivity checks, not permutations "
        "of the model's Wald statistic. Labels are exchanged within other-factor strata for main effects; "
        "interactions use approximate Freedman–Lane reduced-model residual permutation. These require "
        "exchangeability under the tested null, beyond independent runs. Nonlinear/multipart models and "
        "rank-based ordinal checks may test different null hypotheses; "
        "agreement is descriptive, not proof of validity.",
        "Analytic and bootstrap intervals are pointwise 95% intervals, not multiplicity-adjusted. Their agreement "
        "compares whether they include the null; it is not a comparison with the Holm-adjusted p-value.",
    ]
    methods.append(
        f"Bootstrap — {bootstrap.get('method', 'unavailable')}: {bootstrap['requested']} requested, "
        f"{bootstrap['attempted']} attempted, {bootstrap['successful']} successful, {bootstrap['failed']} failed. "
        f"Status: {bootstrap['status']}. Intervals require completion and at least 90% successful refits. "
        "The selected family is held fixed; model-selection uncertainty is not included."
    )
    for term, row in permutation_rows.items():
        description = (
            f"Permutation — {term}: {config['n_perm']} requested, {row['n_perm']} completed; "
            f"{label(row.get('method', ''))}; "
            f"working scale: {row.get('working_scale', 'response')}. "
        )
        if row.get("reason"):
            description += str(row["reason"])
        elif row["n_perm"]:
            description += (
                f"Monte Carlo p-value uses (exceedances + 1)/(permutations + 1); minimum {1 / (row['n_perm'] + 1):.6g}."
            )
        methods.append(description)
    # Describe the recorded execution, not a fixed family-to-test lookup. A
    # manual Gaussian fit can use t/F while its automatic robust fit uses z/chi2.
    explanations = set()
    for section, rows in (("Factor tests", tests), ("Comparisons", effects)):
        for row in rows:
            if row["method"] in {"Inference unavailable", "Test method not recorded"}:
                continue
            explanations.add(f"{section} — {row['method']}: {row['purpose']}")
    methods.extend(sorted(explanations))
    for interval in sorted({row["interval_method"] for row in effects if row["interval_method"]}):
        explanation = (
            "Simulates and refits the model to estimate uncertainty in each comparison."
            if "bootstrap" in interval.lower()
            else "Approximates uncertainty in the predicted comparison using the fitted parameter covariance."
        )
        methods.append(f"Confidence intervals — {interval}: {explanation}")
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
            "bootstrap intervals simulate from the fitted model and share its distributional assumptions. "
            f"Null-model bootstrap likelihood-ratio tests requested: {config.get('n_boot_lrt', 0)} per hypothesis."
        )
        if config.get("n_boot_lrt", 0):
            for row in output["tables"].get("tests", []):
                methods.append(
                    f"Additional bootstrap likelihood-ratio test — {label(row.get('hypothesis', ''))}: "
                    f"{config['n_boot_lrt']} requested, {row.get('n_boot_lrt_attempted', 0)} attempted, "
                    f"{row.get('n_boot_lrt', 0)} successful, {row.get('n_boot_lrt_failed', 0)} failed. "
                    f"Recorded method: {recorded_method(row)}. Main report p-values remain analytic."
                )
    else:
        methods.append(
            "statsmodels fits; HC3 covariance for Gaussian/lognormal models and HC0 for other models. "
            "Nonlinear comparison intervals use the delta method."
        )
        methods.append(
            "Bootstrap intervals resample whole independent runs within each observed cell and refit "
            "the selected model. Factor values and trial totals stay attached to their runs."
        )
    findings = [
        f"{t['effect']}: analytic — {t['result'].lower()}; permutation — {t['permutation_result'].lower()}."
        for t in tests
    ]
    versions = {name: importlib.metadata.version(name) for name in ("numpy", "scipy", "statsmodels")}
    return dict(
        version=REPORT_VERSION,
        metric=config["metric"],
        status="valid",
        metric_info=metric_information(source, config["metric"], plan),
        description=f"Analyzed {len(source['rows']) - output['n_excluded']} runs for {config['metric']} "
        f"as a {plan['measurement_type']} measurement.",
        findings=findings,
        effects=effects,
        tests=tests,
        warnings=warnings,
        methods=methods,
        resampling=dict(bootstrap=bootstrap, permutations=list(permutation_rows.values())),
        metric_definition=plan["metadata"],
        provenance=dict(
            fingerprint=source["fingerprint"],
            design_revision=source.get("design_revision"),
            seed=config["seed"],
            libraries=versions,
            policy_version=REPORT_VERSION,
            candidates=output["tables"]["automatic_candidates"],
        ),
    )

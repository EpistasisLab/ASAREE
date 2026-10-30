"""Deterministic design accounting; no inferred causal or dependence structure."""

from __future__ import annotations

import itertools
import json
import math


def design_specification(source, config, frame=None):
    declared = source.get("factor_metadata", {})
    observed = {}
    for row in source["rows"]:
        for name, value in row.get("factors", {}).items():
            if value is not None:
                observed.setdefault(name, set()).add(json.dumps(value, sort_keys=True, default=str))
    selected = config.get("factors") or sorted(name for name, values in observed.items() if len(values) > 1)
    rows = []
    for name in sorted(set(declared) | set(observed) | set(selected)):
        values = sorted(observed.get(name, set()))
        if name in selected:
            status, reason = (
                "Included",
                "Selected explicitly" if config.get("factors") else "All observed varying factors",
            )
        elif len(values) == 1:
            status, reason = "Constant setting", "No observed variation; no separate effect can be estimated"
        elif not values:
            status, reason = "Excluded", "Declared factor has no observed values"
        else:
            status, reason = (
                "Excluded",
                config.get("excluded_factor_reasons", {}).get(
                    name, "Omitted by the explicit factor selection; scientific justification was not supplied"
                ),
            )
        rows.append(
            dict(
                factor=name,
                status=status,
                reason=reason,
                observed_levels=[json.loads(value) for value in values],
                declared_levels=declared.get(name, {}).get("levels", []),
            )
        )
    order = min(config.get("max_order", 2), len(selected))
    terms = [list(term) for k in range(1, order + 1) for term in itertools.combinations(selected, k)]
    omitted = [list(term) for k in range(order + 1, len(selected) + 1) for term in itertools.combinations(selected, k)]
    result = dict(
        factors=rows,
        included_factors=selected,
        terms=terms,
        excluded_terms=omitted,
        dependence=source.get("dependence", {}),
        structure=source.get("design_structure", {}),
        experimental_unit="One whole-dataset run; replicate numbers alone do not identify matched subjects",
        intention_status="Requires human confirmation against the intended experiment",
    )
    if frame is not None:
        counts = frame.groupby([f"factor_{i}" for i in range(len(selected))]).size()
        result.update(
            observed_conditions=len(counts),
            possible_conditions=math.prod(frame[f"factor_{i}"].nunique() for i in range(len(selected))),
            minimum_runs=int(counts.min()),
            maximum_runs=int(counts.max()),
            analyzed_levels={name: sorted(frame[f"factor_{i}"].unique()) for i, name in enumerate(selected)},
        )
    return result


def specification_section(spec):
    items = [
        spec["experimental_unit"],
        spec["intention_status"] + ".",
        "Included terms: " + ("; ".join(" × ".join(t) for t in spec["terms"]) or "None") + ".",
        "Excluded interaction terms (configured maximum order): "
        + ("; ".join(" × ".join(t) for t in spec["excluded_terms"]) or "None")
        + ".",
        "Dependence declarations: " + json.dumps(spec["dependence"], sort_keys=True) + ". "
        "Blocking/repeated-measures structure: " + json.dumps(spec["structure"], sort_keys=True) + ". "
        "An empty declaration does not establish independence.",
    ]
    if "observed_conditions" in spec:
        items.append(
            f"Observed conditions: {spec['observed_conditions']} of {spec['possible_conditions']} "
            f"combinations of analyzed levels; {spec['minimum_runs']}–{spec['maximum_runs']} runs per "
            "observed condition. Missing combinations are model predictions, not observed conditions."
        )
        items.append(
            "Levels retained after metric/trial exclusions: " + json.dumps(spec["analyzed_levels"], sort_keys=True)
        )
    return dict(
        title="Experimental design and analysis specification",
        items=items,
        columns=[
            ("factor", "Factor"),
            ("status", "Use"),
            ("levels", "Observed / declared levels"),
            ("reason", "Reason"),
        ],
        rows=[
            dict(
                row,
                levels=json.dumps(row["observed_levels"], ensure_ascii=False, sort_keys=True)
                + " / "
                + json.dumps(row["declared_levels"], ensure_ascii=False, sort_keys=True),
            )
            for row in spec["factors"]
        ],
    )

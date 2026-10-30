"""Seeded, separately scoped native-beta null simulations; no database or model calls.

Use --simulations to choose the study size. Small runs are smoke checks, not
evidence that the false-positive rate is below 10%. Outputs record uncertainty,
failed/withheld analyses, software versions, configuration and simulation seeds.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import itertools
import json
import platform
from pathlib import Path

import numpy as np

from asaree.services.stats_source import StatsRequest, compute
from asaree.stats.validation import rejection_summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--simulations", type=int, required=True)
    parser.add_argument("--seed", type=int, default=20261030)
    parser.add_argument("--n-boot", type=int, default=20)
    parser.add_argument("--n-perm", type=int, default=99)
    args = parser.parse_args()
    if args.simulations < 1:
        parser.error("--simulations must be positive")
    config = StatsRequest(metric="fraction", n_boot=args.n_boot, n_perm=args.n_perm, seed=args.seed).model_dump()
    designs = [
        ("one-factor-three-level", [3], False),
        ("unbalanced-two-factor", [3, 2], True),
        ("three-factor", [2, 2, 2], False),
    ]
    studies = []
    result = dict(
        execution_status="incomplete",
        scope="Native beta regression only; separate from any earlier six-report validation",
        configuration=config,
        seed=args.seed,
        python=platform.python_version(),
        libraries={name: importlib.metadata.version(name) for name in ("numpy", "scipy", "statsmodels")},
        limitations="Small null-rejection and pointwise null-coverage smoke study only. "
        "not validation of actual experiment reports, and not a basis for ruling out a 10% false-positive "
        "rate unless the recorded uncertainty and scope support that statement.",
        studies=studies,
    )
    args.destination.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.destination.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")

    save()
    for design_index, (name, levels, unbalanced) in enumerate(designs):
        outcomes, records = [], []
        study = dict(
            design=name,
            factor_levels=levels,
            runs_per_condition="20, 25, 30 cyclically" if unbalanced else 20,
            data_generating_process="Independent Beta(3, 5) observations; all factor effects zero; "
            "common precision 8; no blocks or repeated measures",
            rejection_event="Any analytic Holm-adjusted factor-test p < configured alpha",
            requested_simulations=args.simulations,
            execution_status="incomplete",
            simulations=records,
        )
        studies.append(study)
        save()
        for simulation in range(args.simulations):
            seed = int(np.random.SeedSequence([args.seed, design_index, simulation]).generate_state(1)[0])
            rng = np.random.default_rng(seed)
            rows = []
            for condition, values in enumerate(itertools.product(*(range(n) for n in levels))):
                for replicate in range(20 + (condition % 3) * 5 if unbalanced else 20):
                    rows.append(
                        dict(
                            id=str(len(rows)),
                            replicate=replicate + 1,
                            factors={f"Factor {i + 1}": str(v) for i, v in enumerate(values)},
                            metrics={"fraction": float(rng.beta(3, 5))},
                        )
                    )
            source = dict(
                rows=rows,
                metric_metadata={"fraction": {"measurement_type": "proportion"}},
                design_revision=1,
                design_revision_id="beta-null-simulation",
                excluded_row_replicates=0,
            )
            source["fingerprint"] = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
            try:
                output = compute(source, dict(config, seed=seed))
                summary = output["summary"]
                tests = summary["tests"]
                pvalues = [test["p_adjusted"] for test in tests]
                if output["execution_status"] == "incomplete":
                    outcome = "incomplete"
                elif not tests or any(p is None for p in pvalues):
                    outcome = "withheld"
                else:
                    outcome = "rejected" if any(p < config["alpha"] for p in pvalues) else "not_rejected"
                records.append(
                    dict(
                        seed=seed,
                        outcome=outcome,
                        selected_family=output["family"],
                        tests=[
                            dict(
                                term=t["effect"],
                                p_adjusted=t["p_adjusted"],
                                resampling_p_adjusted=t.get("permutation_p_adjusted"),
                            )
                            for t in tests
                        ],
                        warnings=summary["warnings"],
                        interval_coverage={
                            effect["comparison"]: dict(
                                analytic=(effect["ci_low"] <= 0 <= effect["ci_high"])
                                if effect["ci_low"] is not None and effect["ci_high"] is not None
                                else None,
                                bootstrap=(effect["bootstrap_ci_low"] <= 0 <= effect["bootstrap_ci_high"])
                                if effect["bootstrap_ci_low"] is not None and effect["bootstrap_ci_high"] is not None
                                else None,
                            )
                            for effect in summary["effects"]
                        },
                    )
                )
            except (ValueError, RuntimeError, FloatingPointError, np.linalg.LinAlgError) as exc:
                outcome = "failed"
                records.append(dict(seed=seed, outcome=outcome, reason=str(exc)))
            outcomes.append(outcome)
            study["summary"] = rejection_summary(outcomes)
            coverage = []
            comparisons = sorted({name for record in records for name in record.get("interval_coverage", {})})
            for comparison in comparisons:
                for method in ("analytic", "bootstrap"):
                    events = []
                    for record in records:
                        covered = record.get("interval_coverage", {}).get(comparison, {}).get(method)
                        events.append(
                            "not_rejected"
                            if covered is True
                            else "rejected"
                            if covered is False
                            else record["outcome"]
                            if record["outcome"] in {"failed", "incomplete"}
                            else "withheld"
                        )
                    coverage_summary = rejection_summary(events)
                    coverage.append(
                        dict(
                            comparison=comparison,
                            method=method,
                            event="Pointwise 95% interval excludes known null difference zero",
                            summary=coverage_summary,
                        )
                    )
            study["pointwise_noncoverage"] = coverage
            save()
        study["execution_status"] = "completed"
        save()
        print(
            f"{name}: {outcomes.count('rejected')}/{len(outcomes)} rejections; "
            f"{outcomes.count('withheld')} withheld; {outcomes.count('failed')} failed"
        )
    result["execution_status"] = "completed"
    save()


if __name__ == "__main__":
    main()

"""Plain-language descriptions of saved statistical tests; no generated claims."""

import math


def primary_comparison_result(effect):
    low, high = effect.get("primary_ci_low"), effect.get("primary_ci_high")
    if (
        effect.get("primary_status") != "completed"
        or not all(isinstance(v, int | float) and math.isfinite(v) for v in (low, high))
        or low > high
    ):
        return "Primary inference unavailable"
    null = 1 if effect.get("scale", "difference").startswith("ratio") else 0
    return "Evidence of a difference" if low > null or high < null else "No clear evidence of a difference"


def describe_report_findings(report):
    secondary = describe_findings(report.get("tests", []), report.get("alpha", 0.05))
    if report.get("primary_analysis") != "parametric_bootstrap_intervals":
        return secondary
    findings = []
    for effect in report.get("effects", []):
        result = primary_comparison_result(effect)
        name = effect["comparison"]
        if result == "Primary inference unavailable":
            findings.append(
                f"Primary parametric-bootstrap inference for {name} was unavailable. "
                "Sensitivity results do not replace it."
            )
        else:
            evidence = "evidence" if result == "Evidence of a difference" else "no clear evidence"
            findings.append(
                f"The primary parametric-bootstrap interval found {evidence} of a difference for {name} "
                "(pointwise; not multiplicity-adjusted)."
            )
    return findings + ["Secondary factor test: " + finding for finding in secondary]


def describe_findings(tests: list[dict], alpha: float = 0.05) -> list[str]:
    def evidence(value):
        if not isinstance(value, int | float) or not math.isfinite(value):
            return None
        return value < alpha

    findings = []
    for test in tests:
        factors = str(test["effect"]).split(":")
        interaction = len(factors) > 1
        main = evidence(test.get("p_adjusted"))
        check = evidence(test.get("permutation_p_adjusted"))
        name = test.get("resampling_name") or "permutation check"
        if interaction:
            subject = (
                f"the effect of {factors[0]} changes across {factors[1]} settings"
                if len(factors) == 2
                else f"the effects of {' and '.join(factors)} depend on one another"
            )
        else:
            subject = f"{factors[0]} has an effect on this metric"
        if main is not None and check == main:
            findings.append(
                f"Both the main test and the {name} found evidence that {subject}."
                if main
                else f"Neither the main test nor the {name} found clear evidence that {subject}."
            )
            continue
        conclusion = (
            f"The main test found evidence that {subject}."
            if main
            else f"The main test found no clear evidence that {subject}."
        )
        if main is None:
            conclusion = f"The main statistical test for {' and '.join(factors)} was unavailable."
            if check is None:
                findings.append(f"{conclusion[:-1]}, and so was its {name}.")
                continue
        if check is None:
            sensitivity = f"A {name} was not available for this comparison."
        else:
            sensitivity = (
                f"The {name} found evidence that {subject}."
                if check
                else f"The {name} found no clear evidence that {subject}."
            )
            if main is not None:
                sensitivity += " The two methods differ; interpret this finding cautiously."
        findings.append(f"{conclusion} {sensitivity}")
    return findings

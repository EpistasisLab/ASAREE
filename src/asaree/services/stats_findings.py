"""Plain-language descriptions of saved statistical tests; no generated claims."""

import math


def describe_findings(tests: list[dict]) -> list[str]:
    def evidence(value):
        if not isinstance(value, int | float) or not math.isfinite(value):
            return None
        return value < 0.05

    findings = []
    for test in tests:
        factors = str(test["effect"]).split(":")
        interaction = len(factors) > 1
        main = evidence(test.get("p_adjusted"))
        check = evidence(test.get("permutation_p_adjusted"))
        if interaction:
            subject = (
                f"the effect of {factors[0]} changes across {factors[1]} settings"
                if len(factors) == 2 else f"the effects of {' and '.join(factors)} depend on one another"
            )
        else:
            subject = f"{factors[0]} has an effect on this metric"
        if main is not None and check == main:
            findings.append(
                f"Both the main test and the permutation check found evidence that {subject}."
                if main else f"Neither the main test nor the permutation check found clear evidence that {subject}."
            )
            continue
        conclusion = (
            f"The main test found evidence that {subject}."
            if main else f"The main test found no clear evidence that {subject}."
        )
        if main is None:
            conclusion = f"The main statistical test for {' and '.join(factors)} was unavailable."
        if check is None:
            sensitivity = "A permutation check was not available for this comparison."
        else:
            sensitivity = (
                f"The permutation check found evidence that {subject}."
                if check else f"The permutation check found no clear evidence that {subject}."
            )
            if main is not None:
                sensitivity += " The two methods differ; interpret this finding cautiously."
        findings.append(f"{conclusion} {sensitivity}")
    return findings

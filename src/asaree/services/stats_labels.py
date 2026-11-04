"""Format reports without using human-readable punctuation as term identity."""

import re
from copy import deepcopy
from itertools import combinations


def display_factor_name(name: str) -> str:
    return re.sub(r"\s*:\s*", " › ", name)


def term_factors(term: str, factors: list[str]) -> list[str]:
    """Decode internal terms before substituting names, including legacy reports."""
    aliases = {f"factor_{i}": name for i, name in enumerate(factors)}
    aliases.update({f"C(f{i}, Sum)": name for i, name in enumerate(factors)})
    parts = term.split(":")
    if all(part in aliases for part in parts):
        return [aliases[part] for part in parts]
    # Older summaries already substituted names. Match whole known names,
    # never blindly split their embedded colons into experimental factors.
    for order in range(1, len(factors) + 1):
        matches = [list(group) for group in combinations(factors, order) if ":".join(group) == term]
        if matches:
            return matches[0] if len(matches) == 1 else [term]
    return [term] if factors else parts


def present_summary(
    report: dict,
    factors: list[str] | None = None,
    family: str | None = None,
    output: dict | None = None,
) -> dict:
    """Upgrade saved display text in memory; preserve numeric results and stored keys."""
    from asaree.services.stats_automatic import averaged_phrase, factor_test_text

    result = deepcopy(report)
    resolved = report.get("provenance", {}).get("resolved_config", {})
    factors = (
        resolved.get("factors")
        or factors
        or report.get("design_specification", {}).get("included_factors")
        or list(report.get("provenance", {}).get("treatment_labels", {}))
    )
    family = resolved.get("family") or family or "gaussian"
    levels = report.get("design_specification", {}).get("analyzed_levels", {})
    levels = {name: levels.get(name, []) for name in factors}
    raw_terms = [
        row.get("term", row.get("hypothesis", ""))
        for row in (output or {}).get("tables", {}).get("tests", [])
        if row.get("family", "terms") == "terms"
    ]
    tests = result.get("tests", [])
    for index, row in enumerate(tests):
        structured = bool(row.get("factors"))
        raw_term = raw_terms[index] if len(raw_terms) == len(tests) else row["effect"]
        names = row.get("factors") or term_factors(raw_term, factors)
        row["factors"] = names
        row["label"] = " × ".join(display_factor_name(name) for name in names)
        row["kind"] = "main" if len(names) == 1 else "interaction"
        # Correct old narratives that mistook a name's colon for an interaction.
        if not structured and factors:
            others = [name for name in factors if name not in names]
            question, null, interpretation = factor_test_text(
                names,
                others,
                {**{name: [] for name in names}, **levels},
                family,
                report["metric"],
            )
            if family in {"zoib", "gamma_hurdle", "betabinomial"}:
                null = row.get("null_hypothesis") or null
                interpretation = "Tests whether the named term's coefficients are zero in every fitted regression part."
            row.update(
                question=question,
                null_hypothesis=null,
                explanation=interpretation,
                purpose=f"{question} Null: {null}",
                averaged_over=averaged_phrase(others, levels),
            )

    # Older technical details contain copies of the same mistaken term labels
    # and narratives. Refresh those copies without refitting or changing evidence.
    method_updates = {}

    def method_line(row, columns):
        return "Factor tests — " + "; ".join(f"{title}: {row[key]}" for key, title in columns if row.get(key))

    for section in result.get("details", []):
        if section.get("title") != "Factor tests" or len(section.get("rows", [])) != len(tests):
            continue
        for detail, test in zip(section["rows"], tests, strict=True):
            old_method = method_line(detail, section["columns"])
            detail["target"] = test["label"]
            if test.get("explanation"):
                detail["explanation"] = test["explanation"]
            if test.get("null_hypothesis"):
                detail["null"] = test["null_hypothesis"] + " " + test.get("averaged_over", "")
            method_updates[old_method] = method_line(detail, section["columns"])
    result["methods"] = [method_updates.get(line, line) for line in result.get("methods", [])]

    for row in result.get("effects", []):
        for name in row.get("factors") or factors:
            for prefix in (name, display_factor_name(name)):
                if row["comparison"].startswith(prefix + ": "):
                    row["comparison"] = display_factor_name(name) + " = " + row["comparison"][len(prefix) + 2 :]
                    break

    pattern = (
        re.compile("|".join(re.escape(name) for name in sorted(factors, key=len, reverse=True))) if factors else None
    )

    def format_text(value):
        if isinstance(value, str):
            # Replace complete known names only, not separators in prose or levels.
            return pattern.sub(lambda match: display_factor_name(match[0]), value) if pattern else value
        if isinstance(value, list):
            return [format_text(item) for item in value]
        if isinstance(value, dict):
            return {
                key: item if key in {"effect", "factors", "provenance", "holm_family"} else format_text(item)
                for key, item in value.items()
            }
        return value

    return format_text(result)

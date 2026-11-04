"""Display punctuation must not change factor, numeric-result or workspace identity."""

from copy import deepcopy

import numpy as np

from asaree.services.stats_findings import describe_report_findings
from asaree.services.stats_html import render_html
from asaree.services.stats_labels import present_summary, term_factors
from asaree.services.stats_source import StatsRequest, compute

MODEL = "Azure AI Foundry:Model"
PROMPT = "PubMed Retrieval Agent:Prompt"


def test_term_identity_is_decoded_before_substituting_names():
    factors = [MODEL, PROMPT]
    assert term_factors("C(f0, Sum)", factors) == [MODEL]
    assert term_factors("C(f0, Sum):C(f1, Sum)", factors) == factors
    assert term_factors("factor_0:factor_1", factors) == factors
    assert term_factors(MODEL, factors) == [MODEL]
    assert term_factors(f"{MODEL}:{PROMPT}", factors) == factors


def test_saved_raw_terms_disambiguate_names_that_contain_an_entire_interaction():
    report = dict(metric="Outcome", tests=[dict(effect="A:B", p_adjusted=.2)], effects=[])
    output = dict(tables=dict(tests=[dict(term="C(f0, Sum):C(f1, Sum)")]))
    displayed = present_summary(report, ["A", "B", "A:B"], "gaussian", output)
    assert displayed["tests"][0]["factors"] == ["A", "B"]
    assert displayed["tests"][0]["label"] == "A × B"


def test_saved_report_labels_and_findings_are_corrected_without_mutating_results():
    report = dict(
        metric="Duration",
        status="valid",
        description="Saved report",
        effects=[],
        methods=[],
        warnings=[],
        tests=[
            dict(effect=MODEL, label="Azure AI Foundry × Model", p_adjusted=0.01, result="Evidence of an effect"),
            dict(effect=f"{MODEL}:{PROMPT}", p_adjusted=0.2, result="No clear evidence of an effect"),
        ],
    )
    before = deepcopy(report)
    displayed = present_summary(report, [MODEL, PROMPT], "gaussian")
    assert displayed["tests"][0]["factors"] == [MODEL]
    assert displayed["tests"][0]["label"] == "Azure AI Foundry › Model"
    assert displayed["tests"][1]["label"] == "Azure AI Foundry › Model × PubMed Retrieval Agent › Prompt"
    assert displayed["tests"][1]["label"].count("×") == 1
    assert "has an effect" in describe_report_findings(displayed)[0]
    assert "changes across" in describe_report_findings(displayed)[1]
    assert report == before
    assert [row["p_adjusted"] for row in displayed["tests"]] == [0.01, 0.2]
    assert present_summary(displayed, [MODEL, PROMPT], "gaussian") == displayed
    html = render_html(dict(config=dict(factors=[MODEL, PROMPT]), output=dict(family="gaussian", summary=report)))
    assert "Main-effect tests" in html and "Interaction tests" in html
    assert "Azure AI Foundry › Model × PubMed Retrieval Agent › Prompt" in html
    assert "Azure AI Foundry × Model" not in html


def test_automatic_report_keeps_two_main_effects_and_one_interaction_for_colon_names():
    rng = np.random.default_rng(29)
    source = dict(
        rows=[
            dict(
                id=str(i),
                replicate=i // 4 + 1,
                factors={MODEL: "Small" if i % 2 else "Large", PROMPT: "Brief" if i % 4 < 2 else "Detailed"},
                metrics={"Outcome": float(rng.normal() + (i % 2))},
            )
            for i in range(64)
        ],
        fingerprint="label-regression",
        design_revision=1,
        excluded_row_replicates=0,
        metric_metadata={},
    )
    original = deepcopy(source)
    result = compute(source, StatsRequest(metric="Outcome", n_boot=20, n_perm=99).model_dump())
    assert result["validity"] == "valid"
    tests = result["summary"]["tests"]
    assert [row["factors"] for row in tests] == [[MODEL], [PROMPT], [MODEL, PROMPT]]
    assert [row["kind"] for row in tests] == ["main", "main", "interaction"]
    assert tests[0]["label"] == "Azure AI Foundry › Model"
    assert tests[2]["label"].count("×") == 1
    assert tests[0]["question"].startswith("Does Azure AI Foundry › Model change")
    assert result["summary"]["effects"][0]["comparison"].startswith("Azure AI Foundry › Model =")
    assert source == original

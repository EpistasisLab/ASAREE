"""Pure normalization coverage for the experiment Results scorecard."""

import csv
import io
import json
from decimal import Decimal
from types import SimpleNamespace

from asaree.services.csv_export import result_rows_schema, result_rows_to_csv
from asaree.services.experiment_run_results import (
    _aggregate_metric_values,
    _declared_metric_aggregations,
    _declared_metric_directions,
    _declared_runtime_metrics,
    _has_execution_evidence,
    _merge_legacy_facets,
    _node_labels,
    _numeric_metrics,
    _primary_metric,
    _sum,
    _usage,
)


def test_usage_normalizes_provider_token_names_and_derives_total() -> None:
    agent_run = SimpleNamespace(
        token_usage={"prompt_tokens": 120, "completion_tokens": 30},
        cost_estimate=0.042,
    )
    assert _usage(agent_run) == {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "cost_usd": 0.042,
    }


def test_usage_keeps_motoro_numeric_cost_estimates() -> None:
    agent_run = SimpleNamespace(token_usage={}, cost_estimate=Decimal("0.042000"))
    assert _usage(agent_run)["cost_usd"] == 0.042


def test_usage_keeps_unreported_values_unknown_instead_of_zero() -> None:
    assert _usage(SimpleNamespace(token_usage=None, cost_estimate=None)) == {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "cost_usd": None,
    }


def test_boolean_metric_is_a_binary_numeric_outcome() -> None:
    assert _numeric_metrics({"passed": True, "failed": False}) == {"passed": 1.0, "failed": 0.0}


def test_primary_metric_uses_the_design_direction() -> None:
    assert _primary_metric(
        {
            "metrics": [
                {"name": "Loss", "catalogKey": "loss", "kind": "runtime", "primary": True, "direction": "minimize"}
            ]
        }
    ) == (
        "loss",
        "minimize",
    )


def test_declared_runtime_metrics_are_projected_from_execution_telemetry() -> None:
    spec = {
        "metrics": [
            {"catalogKey": "cost_usd", "kind": "runtime", "name": "Cost", "primary": True},
            {"catalogKey": "total_tokens", "kind": "runtime", "name": "Total tokens", "primary": False},
        ]
    }
    execution = {"cost_usd": 0.04, "total_tokens": 150, "duration_seconds": 2.5}
    assert _declared_runtime_metrics(spec, execution) == {"cost_usd": 0.04, "total_tokens": 150.0}
    assert _primary_metric(spec) == ("cost_usd", "maximize")


def test_only_builtin_metrics_declare_aggregations() -> None:
    assert _declared_metric_aggregations(
        {
            "metrics": [
                {"name": "Quality", "kind": "custom", "valueType": "number", "aggregation": "mean"},
                {
                    "name": "Cost",
                    "catalogKey": "cost_usd",
                    "kind": "runtime",
                    "valueType": "number",
                    "aggregation": "sum",
                },
            ]
        }
    ) == {"cost_usd": "sum"}


def test_only_builtin_metrics_declare_ranking_directions() -> None:
    assert _declared_metric_directions(
        {
            "metrics": [
                {"name": "Quality", "kind": "custom", "valueType": "number", "direction": "maximize"},
                {
                    "name": "Cost",
                    "catalogKey": "cost_usd",
                    "kind": "runtime",
                    "valueType": "number",
                    "direction": "minimize",
                },
            ]
        }
    ) == {"cost_usd": "minimize"}


def test_cell_metric_aggregations_apply_the_declared_operation() -> None:
    assert _aggregate_metric_values([1.0, 2.0, 3.0], "mean") == 2.0
    assert _aggregate_metric_values([1.0, 2.0, 3.0], "sum") == 6.0


def test_cell_metric_rollups_sum_current_replicates() -> None:
    assert _sum([1250.0, 300.5, 49.5]) == 1600.0
    assert _sum([]) is None


def test_results_csv_includes_projected_runtime_metrics() -> None:
    csv_text = result_rows_to_csv(
        [
            {
                "replicate_label": "cell__rep1",
                "replicate_number": 1,
                "cell_label": "cell",
                "status": "completed",
                "factor_values": {"model": "small"},
                "metric_values": {"duration_seconds": 2.5, "total_tokens": 150},
                "cost_usd": 0.04,
            }
        ]
    )
    header, row = csv_text.strip().splitlines()
    assert "duration_seconds" in header and "total_tokens" in header
    assert "2.5" in row and "150" in row


def test_results_csv_keeps_an_empty_column_for_an_unreported_custom_metric() -> None:
    design_spec = {"metrics": [{"name": "Reviewer report", "kind": "custom"}]}
    csv_text = result_rows_to_csv(
        [
            {
                "replicate_label": "cell__rep1",
                "replicate_number": 1,
                "cell_label": "cell",
                "status": "completed",
                "factor_values": {},
                "metric_values": {},
            }
        ],
        design_spec=design_spec,
    )

    reader = csv.DictReader(io.StringIO(csv_text))
    row = next(reader)
    assert "Reviewer report" in (reader.fieldnames or [])
    assert row["Reviewer report"] == ""
    schema = result_rows_schema(
        [{"cell_label": "cell", "replicate_number": 1, "factor_values": {}, "metric_values": {}}],
        design_spec=design_spec,
    )
    reported = next(column for column in schema["columns"] if column["name"] == "Reviewer report")
    assert reported == {"name": "Reviewer report", "role": "reported", "value_type": "opaque"}


def test_results_csv_projects_script_stdout_and_execution_metadata() -> None:
    envelope = {
        "code_sha256": "abc123",
        "script": "score.py",
        "exit_code": 0,
        "stdout": "arbitrary output\n",
        "stderr": "",
    }
    result = json.dumps(envelope)
    rows = [
        {
            "cell_label": "cell",
            "replicate_number": 1,
            "factor_values": {},
            "metric_values": {"Script custom metric": result},
            "metric_observations": [
                {
                    "metric_id": "script-metric",
                    "metric_name": "Script custom metric",
                    "status": "measured",
                    "value": result,
                    "producer": {"producer_id": "asaree.python_script"},
                }
            ],
        }
    ]
    design_spec = {
        "metrics": [
            {
                "id": "script-metric",
                "name": "Script custom metric",
                "kind": "custom",
                "valueType": "opaque",
            }
        ]
    }

    exported = next(csv.DictReader(io.StringIO(result_rows_to_csv(rows, design_spec))))

    assert exported["Script custom metric"] == "arbitrary output\n"
    assert json.loads(exported["Script custom metric__raw_result"]) == envelope
    assert exported["Script custom metric__code_sha256"] == "abc123"
    assert exported["Script custom metric__script"] == "score.py"
    assert exported["Script custom metric__exit_code"] == "0"
    assert exported["Script custom metric__stderr"] == ""
    assert "Script custom metric__stdout" not in exported

    schema = result_rows_schema(rows, design_spec=design_spec)
    columns = {column["name"]: column for column in schema["columns"]}
    assert columns["Script custom metric"] == {
        "name": "Script custom metric",
        "role": "reported",
        "value_type": "string",
    }
    assert columns["Script custom metric__raw_result"]["value_type"] == "json"
    assert columns["Script custom metric__exit_code"]["value_type"] == "integer"


def test_results_csv_unions_optional_script_result_metadata_across_rows() -> None:
    success = {
        "code_sha256": "abc123",
        "script": "score.py",
        "exit_code": 0,
        "stdout": "ok",
        "stderr": "",
    }
    timeout = {
        "code_sha256": "abc123",
        "script": "score.py",
        "timed_out": True,
        "error": "the script timed out",
        "stdout": "partial",
        "stderr": "trace",
    }

    def row(number: int, value: dict[str, object]) -> dict[str, object]:
        result = json.dumps(value)
        return {
            "cell_label": "cell",
            "replicate_number": number,
            "factor_values": {},
            "metric_values": {"Score": result},
            "metric_observations": [
                {
                    "metric_id": "score",
                    "metric_name": "Score",
                    "status": "measured",
                    "value": result,
                    "producer": {"producer_id": "asaree.python_script"},
                }
            ],
        }

    exported = list(csv.DictReader(io.StringIO(result_rows_to_csv([row(1, success), row(2, timeout)]))))

    assert exported[0]["Score"] == "ok"
    assert exported[0]["Score__timed_out"] == ""
    assert exported[0]["Score__error"] == ""
    assert exported[1]["Score"] == "partial"
    assert exported[1]["Score__exit_code"] == ""
    assert exported[1]["Score__timed_out"] == "1"
    assert exported[1]["Score__error"] == "the script timed out"


def test_results_csv_preserves_observation_statuses_and_artifacts_as_json() -> None:
    csv_text = result_rows_to_csv(
        [
            {
                "cell_label": "cell",
                "replicate_number": 1,
                "factor_values": {},
                "metric_values": {
                    "Reviewer note": "needs follow-up",
                    "Reviewer payload": {"flags": ["manual-review"]},
                },
                "metric_observations": [
                    {
                        "metric_id": "roc-auc",
                        "metric_name": "ROC-AUC",
                        "status": "unavailable",
                        "value": None,
                        "error": "Probability output is missing.",
                    }
                ],
                "evaluation_artifacts": [
                    {"artifact_key": "confusion_matrix", "kind": "confusion_matrix", "payload": [[4, 1], [2, 5]]}
                ],
                "legacy_values": [
                    {
                        "metric_id": "legacy-note",
                        "metric_name": "Reviewer note",
                        "value": "needs follow-up",
                        "producer": {"producer_id": "legacy.unknown"},
                    },
                    {
                        "metric_id": "legacy-payload",
                        "metric_name": "Reviewer payload",
                        "value": {"flags": ["manual-review"]},
                        "producer": {"producer_id": "legacy.unknown"},
                    },
                ],
            }
        ]
    )

    reader = csv.DictReader(io.StringIO(csv_text))
    row = next(reader)
    assert "Reviewer note" not in (reader.fieldnames or [])
    assert "Reviewer payload" not in (reader.fieldnames or [])
    statuses = json.loads(row["observation_statuses"])
    artifacts = json.loads(row["evaluation_artifacts"])
    legacy_values = json.loads(row["legacy_values"])
    assert statuses == {
        "roc-auc": {
            "name": "ROC-AUC",
            "status": "unavailable",
            "error": "Probability output is missing.",
        }
    }
    assert artifacts == [{"artifact_key": "confusion_matrix", "kind": "confusion_matrix", "payload": [[4, 1], [2, 5]]}]
    assert legacy_values[0]["value"] == "needs follow-up"
    assert legacy_values[0]["producer"]["producer_id"] == "legacy.unknown"
    assert legacy_values[1]["value"] == {"flags": ["manual-review"]}


def test_results_csv_projects_categorical_factors_to_short_level_labels() -> None:
    csv_text = result_rows_to_csv(
        [
            {
                "replicate_label": "small__rep1",
                "factor_values": {"critic": True, "model": "small", "temperature": 0.2},
                "metric_values": {"accuracy": 0.8, "passed": True},
            },
            {
                "replicate_label": "large__rep1",
                "factor_values": {"critic": False, "model": "large", "temperature": 0.8},
                "metric_values": {"accuracy": 0.9, "passed": False},
            },
        ],
        {"factors": [{"name": "model", "levels": ["small", "large"], "level_labels": ["small", "large"]}]},
    )
    rows = list(csv.DictReader(io.StringIO(csv_text)))

    assert "critic_enabled" in rows[0]
    assert "temperature" in rows[0]
    assert "model" in rows[0]
    by_model = {row["model"]: row for row in rows}
    assert by_model["small"]["critic_enabled"] == "1"
    assert by_model["large"]["critic_enabled"] == "0"
    assert by_model["small"]["temperature"] == "0.2"
    assert by_model["large"]["temperature"] == "0.8"
    assert by_model["small"]["passed"] == "1"
    assert by_model["large"]["passed"] == "0"


def test_results_csv_orders_identity_factors_metrics_then_operational_metadata() -> None:
    csv_text = result_rows_to_csv(
        [
            {
                "replicate_label": "internal-only",
                "cell_label": "first_a__later_x",
                "replicate_number": 2,
                "factor_values": {"later": "x", "first": "a"},
                "metric_values": {"Quality": 0.9},
                "duration_seconds": 12.5,
                "status": "completed",
                "run_id": "run-1",
            }
        ],
        {
            "factors": [
                {"name": "first", "levels": ["a", "b"], "level_labels": ["level1", "level2"]},
                {"name": "later", "levels": ["x", "y", "z"], "level_labels": ["level1", "level2", "level3"]},
            ]
        },
    )

    header = csv_text.splitlines()[0].split(",")
    assert header == [
        "cell_label",
        "replicate_number",
        "first",
        "later",
        "duration_seconds",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cost_usd",
        "Quality",
        "status",
        "observation_statuses",
        "evaluation_artifacts",
        "obsolete",
        "run_id",
        "protocol_revision_id",
        "updated_at",
        "error",
    ]
    assert "replicate_label" not in header


def test_results_csv_omits_legacy_values_when_no_row_has_legacy_data() -> None:
    rows = [{"cell_label": "cell", "replicate_number": 1, "factor_values": {}, "metric_values": {}}]

    exported = next(csv.DictReader(io.StringIO(result_rows_to_csv(rows))))
    schema = result_rows_schema(rows)

    assert "legacy_values" not in exported
    assert "legacy_values" not in {column["name"] for column in schema["columns"]}


def test_results_csv_sorts_long_form_factor_values_in_natural_level_order() -> None:
    csv_text = result_rows_to_csv(
        [
            {"cell_label": "third", "replicate_number": 1, "factor_values": {"prompt": "third"}},
            {"cell_label": "first", "replicate_number": 2, "factor_values": {"prompt": "first"}},
            {"cell_label": "second", "replicate_number": 1, "factor_values": {"prompt": "second"}},
        ],
        {
            "factors": [
                {
                    "name": "prompt",
                    "levels": ["first", "second", "third"],
                    "level_labels": ["level1", "level2", "level3"],
                }
            ]
        },
    )

    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert [row["prompt"] for row in rows] == ["level1", "level2", "level3"]


def test_results_csv_schema_describes_categorical_labels_and_binary_outcomes() -> None:
    schema = result_rows_schema(
        [
            {"factor_values": {"model": "small"}, "metric_values": {"passed": True}},
            {"factor_values": {"model": "large"}, "metric_values": {"passed": False}},
        ],
        {"passed": "boolean"},
    )
    factor = next(column for column in schema["columns"] if column["name"] == "model")
    outcome = next(column for column in schema["columns"] if column["name"] == "passed")
    assert factor == {
        "name": "model",
        "role": "factor",
        "source_factor": "model",
        "encoding": "categorical",
        "value_type": "string",
        "levels": ["level1", "level2"],
    }
    assert outcome == {"name": "passed", "role": "outcome", "value_type": "boolean", "cell_aggregation": "mean"}


def test_results_csv_uses_persisted_level_labels_instead_of_long_treatment_values() -> None:
    long_prompts = [
        "Classify every record and explain each decision in exhaustive detail.",
        "Describe this dataset in full detail, including every available column and caveat.",
        "Summarize the dataset for a clinical researcher in one concise paragraph.",
    ]
    rows = [{"factor_values": {"Agent:System prompt": prompt}} for prompt in long_prompts]
    design_spec = {
        "factors": [
            {
                "name": "Agent:System prompt",
                "levels": long_prompts,
                "level_labels": ["classifier", "full_description", "concise_summary"],
            }
        ]
    }

    csv_text = result_rows_to_csv(rows, design_spec)
    schema = result_rows_schema(rows, design_spec=design_spec)

    header = csv_text.splitlines()[0]
    assert "agent_system_prompt" in header
    assert "Describe this dataset" not in header
    exported_rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert [row["agent_system_prompt"] for row in exported_rows] == [
        "classifier",
        "concise_summary",
        "full_description",
    ]
    factor = next(column for column in schema["columns"] if column["name"] == "agent_system_prompt")
    assert set(factor["levels"]) == {"classifier", "full_description", "concise_summary"}


def test_results_csv_uses_the_declared_factor_name_with_its_short_label() -> None:
    prompt = "Call run_wired_script(), then explain why fixed seeds help reproducibility."
    csv_text = result_rows_to_csv(
        [{"cell_label": "Answer style:concise", "replicate_number": 1, "factor_values": {"Answer style": prompt}}],
        {
            "factors": [
                {"name": "Answer style", "levels": [prompt], "level_labels": ["concise"]}
            ]
        },
    )

    row = next(csv.DictReader(io.StringIO(csv_text)))
    assert row["cell_label"] == "Answer style:concise"
    assert row["answer_style"] == "concise"
    assert "answer_approach" not in row


def test_results_csv_distinguishes_measured_null_from_an_unavailable_custom_metric() -> None:
    rows = [
        {"replicate_label": "called", "metric_values": {"Judge result": None}},
        {"replicate_label": "not-called", "metric_values": {}},
    ]
    design_spec = {
        "metrics": [
            {
                "name": "Judge result",
                "kind": "custom",
                "valueType": "opaque",
                "direction": "neutral",
                "aggregation": "none",
                "primary": False,
            }
        ]
    }

    exported = list(csv.DictReader(io.StringIO(result_rows_to_csv(rows, design_spec))))

    assert exported[0]["Judge result"] == "null"
    assert exported[1]["Judge result"] == ""


def test_results_csv_keeps_a_declared_opaque_metric_when_legacy_facets_also_name_it() -> None:
    rows = [
        {
            "replicate_label": "judge-completed",
            "metric_values": {"LLM judge evaluation": {"score": 4, "passed": True}},
            "legacy_values": [{"metric_name": "LLM judge evaluation", "value": {"score": 4, "passed": True}}],
        }
    ]
    design_spec = {
        "metrics": [
            {
                "name": "LLM judge evaluation",
                "kind": "custom",
                "valueType": "opaque",
                "direction": "neutral",
                "aggregation": "none",
                "primary": False,
            }
        ]
    }

    exported = next(csv.DictReader(io.StringIO(result_rows_to_csv(rows, design_spec))))

    assert exported["LLM judge evaluation"] == '{"passed":true,"score":4}'


def test_current_opaque_observation_is_not_projected_as_a_legacy_value() -> None:
    metrics = [
        {
            "id": "judge-evaluation",
            "name": "LLM judge evaluation",
            "kind": "custom",
            "valueType": "opaque",
        }
    ]
    facets = _merge_legacy_facets(
        {"LLM judge evaluation": {"score": 4, "passed": True}},
        None,
        [{"metric_id": "judge-evaluation", "metric_name": "LLM judge evaluation", "status": "measured"}],
        [],
        metrics=metrics,
        attempt_id="attempt-1",
    )

    assert facets.legacy_values == []


def test_node_labels_prefers_the_canvas_name_over_its_durable_id() -> None:
    assert _node_labels({"nodes": [{"id": "node-mtj4m99c-l0beqhd2", "data": {"label": "Model evaluator"}}]}) == {
        "node-mtj4m99c-l0beqhd2": "Model evaluator"
    }


def test_node_labels_uses_the_canvas_placeholder_when_an_old_node_has_no_title() -> None:
    assert _node_labels({"nodes": [{"id": "node-old", "type": "agent", "data": {}}]}) == {"node-old": "Agent"}


def test_execution_evidence_excludes_completed_configuration_nodes() -> None:
    assert _has_execution_evidence({"status": "completed", "output_text": None, "error": None}) is False
    assert _has_execution_evidence({"status": "completed", "run_id": "agent-run"}) is True
    assert _has_execution_evidence({"status": "failed", "error": "boom"}) is True

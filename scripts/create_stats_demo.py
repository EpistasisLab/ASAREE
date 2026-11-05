"""Create an unrun ticket-triage experiment using the ASAREE SDK.

Install the SDK from this checkout: pip install -e ./sdk
Set ASAREE_BASE_URL and ASAREE_API_KEY, then run:
    python scripts/create_stats_demo.py --provider openai --model YOUR_MODEL

The model must already be configured in the token owner's Profile. No model
calls or experiment runs are launched by this script. See --help for options.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from pathlib import Path
from typing import Any

NAME = "Statistics demo — ticket triage"
PROVIDERS = ("openai", "anthropic", "azure_foundry", "openrouter", "local")
# Only the classifier's selected input text is placed in its prompt. The
# answer key and the other input condition are confined to the scoring stage.
TICKETS = [
    ("I was billed twice for the same month. Please reverse the extra charge.", "billing"),
    ("I need the tax ID corrected on last month's invoice.", "billing"),
    ("My renewal price is higher than the amount we agreed on.", "billing"),
    ("The app works now; please refund the fee from the outage.", "billing"),
    ("Keep my subscription, but remove the charge for the add-on I never ordered.", "billing"),
    ("My old phone is gone and I can't get the sign-in verification code.", "account"),
    ("Please transfer the workspace owner role to my colleague.", "account"),
    ("I changed jobs and need my sign-in email updated.", "account"),
    ("Don't delete anything. Restore my access after the administrator removed me.", "account"),
    ("I know my password, but my account says it is locked.", "account"),
    ("The invoice PDF downloads as an empty file in two browsers.", "technical"),
    ("I can sign in, but the dashboard spins forever after loading.", "technical"),
    ("The cancel button throws an error. I only tested it; keep my plan active.", "technical"),
    ("Every export has the wrong dates even though the source records are correct.", "technical"),
    ("The payment settings page crashes before I can view it; no charge is disputed.", "technical"),
    ("Stop my subscription before the next renewal; I don't need a refund.", "cancellation"),
    ("The bug was fixed, but we are leaving anyway. Please close the paid plan.", "cancellation"),
    ("Please disable auto-renewal. Keep access until the term ends.", "cancellation"),
    ("Our budget was cut. End the subscription; the last invoice is correct.", "cancellation"),
    ("I found the password reset email. My actual request is to terminate the plan.", "cancellation"),
]

MINIMAL = "Classify each ticket as billing, account, technical, or cancellation."
RUBRIC = """Classify the customer's current requested action, not isolated keywords.
billing: disputed charges, refunds, invoice corrections, or agreed prices.
account: identity, sign-in access, ownership, or membership changes.
technical: broken product behavior without a current request to end the plan.
cancellation: an explicit request to end a plan or stop its renewal.
Ignore quoted history and unrelated text. Negated requests do not count.
Examples: 'The invoice page crashes' -> technical; 'The bug is fixed, but end
my plan' -> cancellation; 'Keep my plan, refund the extra fee' -> billing;
'Replace the owner who left the company' -> account."""


def dataset_rows() -> list[dict[str, str]]:
    noise = [
        'Old closed thread: "cancel my plan". That is NOT my request today.',
        "Unrelated footer: billing | password | error | cancellation.",
        'Quoted resolved incident: "refund the duplicate charge". Ignore that history.',
        "Previous agent suggested resetting a password. That did not address my request.",
    ]
    return [
        {
            "ticket_id": f"T{i:02}",
            "clean_text": text,
            "noisy_text": f"{noise[(i - 1) % len(noise)]}\nCURRENT REQUEST: {text}\nsent frm my phne",
            "expected_label": label,
        }
        for i, (text, label) in enumerate(TICKETS, 1)
    ]


def scoring_source() -> str:
    key = {row["ticket_id"]: row["expected_label"] for row in dataset_rows()}
    return f"""import json

ANSWER_KEY = {key!r}

def score(predictions):
    if not isinstance(predictions, dict):
        raise ValueError("Predictions must be an object keyed by ticket ID")
    if set(predictions) != set(ANSWER_KEY):
        raise ValueError("Exactly the 20 expected ticket IDs are required")
    if any(label not in set(ANSWER_KEY.values()) for label in predictions.values()):
        raise ValueError("Unknown classification label")
    correct = sum(predictions[key] == label for key, label in ANSWER_KEY.items())
    return {{"accuracy": correct / 20, "misclassified_count": 20 - correct,
            "passed": correct >= 17}}
"""


def build_definition(provider: str, model: str, replicates: int, dataset_id: str, dataset_name: str) -> dict[str, Any]:
    rows = dataset_rows()
    prompts = [
        "Classify all 20 tickets below. Return ONLY a JSON object with this shape: "
        '{"predictions":{"T01":"billing", ...}}. Include each ticket ID exactly once.\n'
        + json.dumps([{"ticket_id": row["ticket_id"], "text": row[column]} for row in rows], indent=2)
        for column in ("clean_text", "noisy_text")
    ]
    source = scoring_source()
    scorer_prompt = (
        "You are the scoring stage, not a classifier. Preserve the upstream predictions exactly. "
        "Do not repair, relabel, or infer missing predictions. Use run_wired_script with its code argument "
        "to execute the following exact scoring source, followed by ONE statement: "
        "print(json.dumps(score(PREDICTIONS))). Replace PREDICTIONS with the upstream predictions dictionary "
        "as a Python literal. The explicit code argument is required to supply predictions to the script. "
        "Do not call the wired script without arguments: it only defines the scoring function. "
        "If execution fails or upstream predictions are missing, report the error and omit all metric fields. "
        "After a successful call, return only the JSON object printed in stdout, verbatim. "
        "Never calculate or invent scores yourself. Do not open the dataset workspace.\n\n"
        f"Scoring source:\n```python\n{source}```"
    )

    def node(identifier, kind, label, config, x, y, **data):
        return {
            "id": identifier,
            "type": kind,
            "position": {"x": x, "y": y},
            "data": {"label": label, "config": config, **data},
        }

    def edge(source, target, handle=None):
        return {
            "id": f"{source}-{target}",
            "source": source,
            "target": target,
            "sourceHandle": handle,
            "targetHandle": handle,
        }

    runtime = [
        ("cost_usd", "Cost", "USD"),
        ("duration_seconds", "Duration", "seconds"),
        ("total_tokens", "Total tokens", "tokens"),
    ]
    metrics = [
        {
            "id": key,
            "name": name,
            "catalogKey": key,
            "kind": "runtime",
            "valueType": "number",
            "direction": "minimize",
            "aggregation": "sum",
            "unit": unit,
        }
        for key, name, unit in runtime
    ] + [{"id": key, "name": key, "kind": "custom"} for key in ("accuracy", "misclassified_count", "passed")]
    plan = {
        "metrics": [
            {
                "id": key,
                "name": name,
                "value_type": "number",
                "direction": "minimize",
                "aggregation": "sum",
                "unit": unit,
            }
            for key, name, unit in runtime
        ]
        + [{"id": m["id"], "name": m["name"]} for m in metrics if m["kind"] == "custom"],
        "producers": [
            {
                "id": "runtime",
                "producer_id": "asaree.runtime",
                "kind": "runtime",
                "outputs": {key: key for key, _, _ in runtime},
                "config": {},
            },
            {
                "id": "scores",
                "producer_id": "asaree.agent_output",
                "kind": "reported",
                "outputs": {key: key for key in ("accuracy", "misclassified_count", "passed")},
                "config": {
                    "agent_node_id": "scorer",
                    "projections": {key: {"path": key} for key in ("accuracy", "misclassified_count", "passed")},
                },
            },
        ],
        "inputs": [{"producer_binding_id": "runtime", "input_key": "facts", "source_key": "attempt.runtime"}],
    }
    return {
        "description": (
            "Synthetic ticket-triage benchmark: minimal vs rubric prompting × clean vs noisy inputs. "
            "Each replicate classifies the same 20 tickets. A separate scoring agent executes Python "
            "against a fixed answer key and reports accuracy, error count, and pass (>=17/20 correct). "
            "Runtime metrics include classification AND scoring. No guaranteed treatment effect; "
            "constant outcomes may receive descriptive-only reports."
        ),
        "hypothesis": "A rubric improves accuracy more on noisy tickets, at a measurable token and latency cost.",
        "design_spec": {
            "factors": [
                {
                    "name": "Prompt style",
                    "levels": [MINIMAL, RUBRIC],
                    "level_labels": ["Minimal", "Rubric"],
                    "level_type": "text",
                },
                {"name": "Input quality", "levels": prompts, "level_labels": ["Clean", "Noisy"], "level_type": "text"},
            ],
            "replicates": replicates,
            "randomization_seed": 20261104,
            "coordination_strategy": {"slug": "sequential"},
            "metrics": metrics,
        },
        "measurement_plan": plan,
        "graph": {
            "nodes": [
                node(
                    "classifier",
                    "agent",
                    "Classify tickets",
                    {
                        "prompt": prompts[0],
                        "goal": "Classify the 20 tickets without changing their IDs.",
                        "system_prompt": MINIMAL,
                        "output_contract": None,
                    },
                    100,
                    200,
                    factor_bindings={"config.system_prompt": "Prompt style", "config.prompt": "Input quality"},
                ),
                node(
                    "scorer",
                    "agent",
                    "Score predictions",
                    {
                        "prompt": scorer_prompt,
                        "goal": "Run Python scoring and return its JSON stdout exactly.",
                        "system_prompt": "Use the supplied scoring code. Never estimate or alter its results.",
                        "output_contract": None,
                    },
                    600,
                    200,
                ),
                node(
                    "model",
                    f"model_{provider}",
                    model,
                    {
                        "provider": provider,
                        "model": model,
                        "max_tokens": 4096,
                    },
                    100,
                    -150,
                ),
                node(
                    "script",
                    "script",
                    "Fixed answer-key scorer",
                    {
                        "name": "score_tickets",
                        "description": "Exact scorer; the scoring agent supplies predictions.",
                        "language": "python",
                        "code": source,
                    },
                    600,
                    600,
                ),
                node(
                    "dataset",
                    "dataset",
                    "Benchmark and answer key (scorer only)",
                    {
                        "dataset_id": dataset_id,
                        "dataset_name": dataset_name,
                        "target_column": "expected_label",
                    },
                    950,
                    550,
                ),
            ],
            "edges": [
                edge("classifier", "scorer"),
                edge("model", "classifier", "model"),
                edge("model", "scorer", "model"),
                edge("script", "scorer", "tool"),
                edge("dataset", "scorer", "dataset"),
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("ASAREE_BASE_URL"), help="API origin; no /api suffix")
    parser.add_argument("--gui-url", help="Frontend origin, if different from the API origin")
    parser.add_argument("--provider", choices=PROVIDERS, required=True)
    parser.add_argument("--model", required=True, help="A tool-capable model configured in your ASAREE profile")
    parser.add_argument("--name", default=NAME)
    parser.add_argument("--replicates", type=int, default=15, help="Replicates per cell (default: 15)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the definition without connecting or creating anything"
    )
    args = parser.parse_args()
    if args.replicates < 3:
        parser.error("Use at least 3 replicates per cell; 15 is recommended for this demo.")
    if args.dry_run:
        definition = build_definition(args.provider, args.model, args.replicates, "DATASET_ID", "DATASET_NAME")
        print(json.dumps(definition, indent=2))
        return
    if not args.base_url or not os.environ.get("ASAREE_API_KEY"):
        parser.error("Set ASAREE_BASE_URL (or --base-url) and ASAREE_API_KEY.")
    from asaree_client import AsareeClient

    with AsareeClient(base_url=args.base_url, api_key=os.environ["ASAREE_API_KEY"]) as client:
        if any(experiment.name == args.name for experiment in client.experiments.list()):
            parser.error("An experiment with this name already exists. Open it in the GUI or choose another --name.")
        if args.provider != "local" and not any(
            setting.provider == args.provider for setting in client.llm_settings.list()
        ):
            parser.error(f"Configure {args.provider} in Profile → LLM credentials before creating the demo.")
        # Register the benchmark once. Each execution's classifier receives a
        # frozen, label-free prompt, not the answer-key dataset or its tools.
        with tempfile.TemporaryDirectory(prefix="asaree-stats-demo-") as directory:
            csv_path = Path(directory) / "tickets.csv"
            rows = dataset_rows()
            with csv_path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            dataset = client.datasets.create(
                name=f"{args.name} — benchmark",
                file_path=str(csv_path),
                target_column="expected_label",
                description="20 synthetic support tickets; clean/noisy conditions and fixed reference labels.",
            )
        print(f"Dataset created: {dataset.id}", flush=True)
        definition = build_definition(args.provider, args.model, args.replicates, str(dataset.id), dataset.name)
        experiment = client.experiments.import_definition(name=args.name, **definition)
        print(f"Experiment created: {experiment.id}", flush=True)
        protocol = client.protocols.list(experiment_id=experiment.id)[0]
        client.experiments.update(experiment.id, dataset_ids=[dataset.id])
        validation = client.experiments.validate_measurement_plan(
            experiment.id,
            measurement_plan=definition["measurement_plan"],
            metrics=definition["design_spec"]["metrics"],
            graph=definition["graph"],
        )
        if not validation.valid:
            issues = "; ".join(issue.message for issue in validation.issues)
            raise RuntimeError("Measurement validation failed: " + issues)
        replicates = client.experiments.generate_design(experiment.id)
        client.protocols.publish(protocol.id)
        gui = (args.gui_url or args.base_url).rstrip("/")
        print(f"Ready: 4 cells, {len(replicates)} whole-dataset replicates. No runs launched.")
        print(f"Open: {gui}/experiments/{experiment.id}/protocol")
        print("Use Run all cells in the GUI. After completion: Results → Statistics → Analyze all metrics.")


if __name__ == "__main__":
    main()

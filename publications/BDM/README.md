# Myocardial infarction use case

A complete, runnable ASAREE experiment: four agents in series
(**DC → FTE → FS → MLM**) build a binary classifier for chronic heart failure
after a myocardial infarction, each one handing a versioned dataset workspace
to the next behind an optional critic gate, and a deterministic **Score** Tool
Step evaluates MLM's approved hyperparameter payload on the held-out split.

It's the public counterpart of the spinal-surgery use case in the paper — same
protocol shape, on a dataset anyone can download.

## What's here

| File | |
| --- | --- |
| `myocardial-azure-foundry-v0.8.0.json` | The experiment, wired to **Azure Foundry** — what `import_use_case.py` imports by default |
| `myocardial-anthropic-v0.8.0.json` | The same, wired to **Anthropic** |
| `myocardial-openai-v0.8.0.json` | The same, wired to **OpenAI** |
| `mi_ZSN.csv` | The dataset — 1700 admissions × 111 features, target `mi_ZSN` |
| `dict_ZSN.json` | The data dictionary for those 111 columns |
| `stats/` | The paper's analysis scripts and outputs for the spinal runs (not part of this walkthrough) |

The provider variants are the same protocol graph — identical agents, prompts,
critic gates, and tool wiring. They differ only in the shared LLM node and the
factors bound to it, because a node's provider is fixed when the node is
created and can't be switched afterwards. Pick the one matching the API key you
have:

| File | Model factor | Effort factor | Design size |
| --- | --- | --- | --- |
| `myocardial-anthropic-v0.8.0.json` | `claude-sonnet-5`, `claude-opus-5` | `medium`, `xhigh` | 8 cells / 160 replicates |
| `myocardial-openai-v0.8.0.json` | `gpt-5-mini`, `gpt-5` | `medium`, `high` | 8 cells / 160 replicates |
| `myocardial-azure-foundry-v0.8.0.json` | `claude-sonnet-5`, `claude-opus-5` | `medium`, `xhigh` | 8 cells / 160 replicates |

All three are 2 × 2 × 2 designs (model × effort × critic on/off) at 20
replicates, with the smaller/larger model of a family at the middle and top of
its provider's effort ladder. The two ladders aren't the same length: OpenAI's
`reasoning_effort` stops at `high`, so `high` is that variant's counterpart to
Anthropic's `xhigh`, not a rung below it.

Factor levels are ordered reference first, treatment second: smaller model →
larger model, medium → highest effort, and critic off → critic on. This matches
the Results analysis's −1/+1 coding and the paper's stated contrasts.

Model levels are editable on the Design tab after importing, and the model field
accepts any id you type — the levels above are just what the catalog can vouch
for. Whether a model gets an Effort or a Temperature control is a per-model fact
declared in `motoro.services.model_capabilities`, not a per-provider one, so if
you swap a model in, check which of the two the node then offers: an effort
factor bound to a temperature-based model varies nothing at runtime, silently.

## ASAREE v0.8.0 execution contract

The three provider variants use the same factors, replicates, agents, and
critic gates, with these v0.8.0 execution details:

- **Dataset connector.** Its edges use the current `dataset` handle rather than
  the legacy `resource` spelling, and the Dataset node now sits *above* the
  agents, since that connector lives on the agent's top edge (Pattern, Skill,
  Dataset, Knowledge above; AI, Memory, Tool below).
- **No workspace Tool nodes.** An agent with a Dataset wired is granted the
  workspace tools implicitly (`_resolve_dataset_tool_config`), so the three
  `Workspace (open_workspace, accept_stage)` nodes were redundant and are gone.
- **Prompts stopped dictating what the run now binds.** ASAREE seeds the cell's
  workspace before the agent's first turn, and a wired Script reaches
  `run_model_script` as a path in ambient `_meta` — so DC/FTE/FS call a bare
  `open_workspace(stage=...)` instead of passing `experiment_id`/`cell_label`/
  `name`.
- **Scoring is a Tool Step, not an agent.** Like the paper's notebook
  (`score_payload`), nothing about the scoring call is left to a model: the
  generic `tool-step-score` node calls `run_model_script` directly, mapping
  each argument to a source -- the wired Script's code, MLM's approved payload
  (as canonical JSON), the replicate's workspace, and fixed values
  (`random_seed=20260705`, `selection_metric=average_precision`, and the
  XGBoost search space as `param_spec_json`). `run_model_script` itself runs
  the notebook's `sanitize_payload` port against that spec (every
  out-of-vocabulary or out-of-bound suggestion is dropped and noted, so a
  malformed payload still scores instead of crashing). The step fails unless
  the tool reports the exact `code_sha256` and `payload_sha256` of what it
  sent, and what it sent is recorded on the replicate *before* the call.
- **Iteration budget.** Every Reason + Act pattern allows 12 iterations, the
  notebook's `max_iterations`.
- **Published execution.** The import helper publishes the localized graph as
  an immutable protocol revision before generating or running replicates.
- **Visible output contracts.** Each agent's declared output shape lives in a
  connected Output Parser node rather than the legacy hidden
  `config.output_contract` field. The contracts and runtime behavior are
  unchanged; the canvas now exposes where each structured payload is defined.
- **Declared measurements.** Each scoring value is its own numeric metric,
  read by exact dotted path from the Tool Step's result
  (`asaree.tool_step` — e.g. `test_metrics.average_precision`,
  `test_metrics.metrics_at_0.5.f1`): PR-AUC, ROC-AUC, Brier and
  operating-point diagnostics, Optuna/XGBoost decisions, the SHA-256 guards,
  and `n_schema_violations` (entries the sanitizer dropped, from the tool's
  `n_sanitize_notes`). Feature counts
  (`n_features_after_dc/fte/fs`, `n_features_created`,
  `n_engineered_features_selected`, `frac_created_selected`) are computed by
  `asaree.feature_pipeline` from the stage payloads and the raw dataset's
  columns, the way the notebook's `process_metrics` does, rather than trusted
  from an agent's self-report. The same producer reports the FTE recipe's
  `n_recipe_ops`, `recipe_depth` and `recipe_hash`. The notebook's critic and
  control-flow columns come from the built-in runtime producer: critic
  invocations, rejections split by partial vs. full scope (an unscoped
  rejection counts as full), revision rounds, and Reason+Act runs that hit
  their iteration ceiling. The remaining agent-stage values come from their
  Output Parser payloads.
- **MLM brief and critic task_brief.** The notebook's `summarize_for_mlm`
  brief is rebuilt in two parts. The edge from Critic (FS) into the MLM is set
  to **Selected fields** (`edge.data.handoff`, on the edge's hover toolbar):
  `selected_features`, `n_features_out`, `observed_class_distribution`,
  `class_balance_check`, `notes_for_mlm`. The FS prose report doesn't pass.
  The FTE/DC parts, which come from further upstream, are field references in
  the MLM goal. They're narrowed the same way the notebook narrows them:
  `{{node:agent-fte.engineering_recipe[name]}}` passes only the step names,
  `{{node:agent-fte.encoding_map[feature, encoding]}}` passes only those two
  keys, and DC passes `notes_for_fte`. If FS extracted none of the selected fields, the edge
  falls back to the full FS output. Each Critic Gate's system prompt includes
  the `task_brief`, because the canvas sends a critic only the output it
  reviews.

That `open_workspace(stage=...)` call is deliberately kept: seeding the
workspace materializes `v0_raw`, but *not* a stage's `.scratch` input, which is
what the `asaree-sklearn-*` servers read. The run context the agent receives
says "do not call open_workspace" — true for the data, not for the scratch
staging — so each prompt says so explicitly.

Nothing else about how a replicate runs changed. The workspace tools the deleted
nodes allow-listed (`open_workspace`, `accept_stage`) are still reachable —
implicitly, along with the rest of `WORKSPACE_AGENT_TOOLS`.

## The dataset

The **ZSN** target of
[UCI's Myocardial Infarction Complications](https://archive.ics.uci.edu/dataset/579/myocardial+infarction+complications)
(Golovenkin et al.) — 1700 admissions, predicting chronic heart failure as a
complication. 23.2% positive, so `average_precision` is the design's primary
metric rather than accuracy.

The column names are short Russian-derived codes (`nr11`, `zab_leg_01`,
`S_AD_KBRIG`), which is why `dict_ZSN.json` matters: it's what
`asaree-sklearn-eda`'s `get_data_dictionary` serves back to an agent that asks
what a column means. ASAREE itself never parses it.

## Walkthrough (ASAREE v0.8.0)

**0. Get ASAREE running** — see the [root README](../../README.md), then open
http://localhost:5173. Every MCP server this use case needs (`asaree-workspace`
and the six `asaree-sklearn-*` servers) ships with ASAREE and registers itself
on startup; there is nothing to install or register by hand.

**1. Register.** Create an account and sign in.

**2. Issue an API token.** In **Profile → API tokens**, create a token and copy
it when shown. Export it together with the API base URL:

```bash
export ASAREE_BASE_URL=http://localhost:8000
export ASAREE_API_KEY=...
```

**3. Import the current definition.** From the repository root, choose the
provider file that matches the credential you will use:

```bash
uv run --with ./sdk python publications/BDM/import_use_case.py \
  publications/BDM/myocardial-openai-v0.8.0.json
```

The helper registers and splits the dataset, maps deployment-specific dataset
and MCP-server identifiers, atomically creates the experiment and canvas,
attaches the dataset, validates the measurement plan, publishes the immutable
protocol revision, and generates the design. Re-running it updates the same
named experiment.

**4. Add the LLM credential.** Open the shared LLM node — it feeds all five
agents and all four critic gates, so it's the only place a model gets chosen.
Add the credential from the node itself, or from **Profile → LLM credentials**.
Once it's saved, the Model dropdown lists what that credential can actually
reach, and the node shows Effort or Temperature depending on which one the
selected model accepts.

The helper has already completed the two data steps below. They are recorded
here to make the experiment definition explicit.

**5. Dataset registration.** The *Myocardial Infarction Dataset* node is bound
to:

| Field | Value |
| --- | --- |
| Name | `myocardial_infarction` |
| CSV file | `mi_ZSN.csv` |
| Target column | `mi_ZSN` |
| Data dictionary | `dict_ZSN.json` |

The **Data dictionary**
field is what makes this dataset workable: the agents are told to resolve every
column's meaning with `get_data_dictionary` rather than guess from its name, and
`dict_ZSN.json` is what that tool serves back. Nothing else to configure —
`asaree-workspace` publishes the dictionary into each cell's own workspace
directory when the pipeline opens it, so the reader finds it on the same shared
filesystem it already reads the data from.

**6. Train/test split.** The registered dataset uses this **Quick split**:

| Field | Value |
| --- | --- |
| Target column | `mi_ZSN` |
| Test size | `0.3` |
| Seed | `42` |

Leave *Group column* empty — with a target column and no groups the split is
stratified, which matters here: at a 23.2% positive rate an unstratified 30%
holdout can leave the two halves at materially different base rates, and every
metric in the design is prevalence-sensitive.

The split is what the agents actually see. The workspace an agent opens is
built from the train half; the test half is only touched by the final model
script. Re-splitting at a different seed overwrites rather than accumulating.

**7. Inspect the generated design.** The **Design** and **Cells** tabs show
2 × 2 × 2 factor combinations = **8 cells**, with 20 replicates per cell
(**160 replicates** total). The imported canvas is already published.

**8. Run.** Two ways:

- *Run cell* (top-right of the canvas) to pick one replicate, then **Run** — do
  this first.
- *Run all cells* in the top bar enqueues every pending replicate as one batch.

Watch progress on the canvas itself, or in the side panel's **Runs** tab;
results land in **Cells** and **Results**.

## Cost

Generating the grid is free — cells and replicates are rows until a run starts — but 160 runs
of a four-agent pipeline, half of them at the top of the effort ladder, is a
real bill. Run one cell
first. If you only want a smoke test, lower **Replicates** on the Design tab and
regenerate before running; ASAREE opens a new design revision when the change
would remove existing replicate slots and retains the superseded revision in
design history.

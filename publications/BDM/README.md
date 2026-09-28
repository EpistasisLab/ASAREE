# Myocardial infarction use case

A complete, runnable ASAREE experiment: five agents in series
(**DC → FTE → FS → MLM → Score**) build a binary classifier for chronic heart
failure after a myocardial infarction, each one handing a versioned dataset
workspace to the next, each (except Score) behind an optional critic gate.

It's the public counterpart of the spinal-surgery use case in the paper — same
protocol shape, on a dataset anyone can download.

## What's here

| File | |
| --- | --- |
| `myocardial-azure-foundry-latest.json` | The experiment, wired to **Azure Foundry** — what `import_use_case.py` imports by default |
| `myocardial-anthropic-latest.json` | The same, wired to **Anthropic** |
| `myocardial-openai-latest.json` | The same, wired to **OpenAI** |
| `myocardial-*-v0.2.0.json` | Archival graphs from the original runs — see "Versions" below |
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
| `myocardial-anthropic-latest.json` | `claude-sonnet-5`, `claude-opus-5` | `medium`, `xhigh` | 8 cells / 80 replicates |
| `myocardial-openai-latest.json` | `gpt-5-mini`, `gpt-5` | `medium`, `high` | 8 cells / 80 replicates |
| `myocardial-azure-foundry-latest.json` | `claude-sonnet-5`, `claude-opus-5` | `medium`, `xhigh` | 8 cells / 80 replicates |

All three are 2 × 2 × 2 designs (model × effort × critic on/off) at 10
replicates, with the smaller/larger model of a family at the middle and top of
its provider's effort ladder. The two ladders aren't the same length: OpenAI's
`reasoning_effort` stops at `high`, so `high` is that variant's counterpart to
Anthropic's `xhigh`, not a rung below it.

Model levels are editable on the Design tab after importing, and the model field
accepts any id you type — the levels above are just what the catalog can vouch
for. Whether a model gets an Effort or a Temperature control is a per-model fact
declared in `motoro.services.model_capabilities`, not a per-provider one, so if
you swap a model in, check which of the two the node then offers: an effort
factor bound to a temperature-based model varies nothing at runtime, silently.

## Versions

Each provider variant ships twice. `-v0.2.0` is the graph retained from the
original runs. Don't modernize it: its value is as a historical artifact, not
as a v0.8.0 import target.

`-latest` is the maintained v0.8.0 copy — the same factors, replicates, agents,
and critic gates, brought up to the current execution contract:

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
  `name`, and Score calls `run_model_script` with **no** `code` argument
  instead of retyping the wired script.
- **Published execution.** The import helper publishes the localized graph as
  an immutable protocol revision before generating or running replicates.
- **Declared measurement source.** The complete held-out scoring response is
  captured as the opaque `Model evaluation` metric from MI-Score's exact
  `asaree-sklearn-model.run_model_script` call. This retains the returned test
  metrics and SHA-256 guards with producer provenance; an arbitrary successful
  tool call is not promoted as a result.

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
  publications/BDM/myocardial-openai-latest.json
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
2 × 2 × 2 factor combinations = **8 cells**, with 10 replicates per cell
(**80 replicates** total). The imported canvas is already published.

**8. Run.** Two ways:

- *Run cell* (top-right of the canvas) to pick one replicate, then **Run** — do
  this first.
- *Run all cells* in the top bar enqueues every pending replicate as one batch.

Watch progress on the canvas itself, or in the side panel's **Runs** tab;
results land in **Cells** and **Results**.

## Cost

Generating the grid is free — cells and replicates are rows until a run starts — but 80 runs
of a five-agent pipeline, half of them at the top of the effort ladder, is a
real bill. Run one cell
first. If you only want a smoke test, lower **Replicates** on the Design tab and
regenerate before running; ASAREE opens a new design revision when the change
would remove existing replicate slots and retains the superseded revision in
design history.

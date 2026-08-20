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
| `myocardial-anthropic.json` | The experiment, wired to an **Anthropic** LLM node |
| `myocardial-openai.json` | The same, wired to **OpenAI** |
| `myocardial-azure-foundry.json` | The same, wired to **Azure Foundry** — what the paper's runs used |
| `mi_ZSN.csv` | The dataset — 1700 admissions × 111 features, target `mi_ZSN` |
| `dict_ZSN.json` | The data dictionary for those 111 columns |
| `stats/` | The paper's analysis scripts and outputs for the spinal runs (not part of this walkthrough) |

The three JSON files are the same protocol graph — identical agents, prompts,
critic gates, and tool wiring. They differ only in the shared LLM node and the
factors bound to it, because a node's provider is fixed when the node is
created and can't be switched afterwards. Pick the one matching the API key you
have:

| File | Model factor | Effort factor | Cells |
| --- | --- | --- | --- |
| `myocardial-anthropic.json` | `claude-sonnet-5`, `claude-opus-5` | `medium`, `xhigh` | 80 |
| `myocardial-openai.json` | `gpt-5-mini`, `gpt-5` | `medium`, `high` | 80 |
| `myocardial-azure-foundry.json` | `claude-sonnet-5`, `claude-opus-5` | `medium`, `xhigh` | 80 |

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

## Walkthrough

Everything below happens in the GUI.

**0. Get ASAREE running** — see the [root README](../../README.md), then open
http://localhost:5173. Every MCP server this use case needs (`asaree-workspace`
and the six `asaree-sklearn-*` servers) ships with ASAREE and registers itself
on startup; there is nothing to install or register by hand.

**1. Register.** Create an account and sign in.

**2. Create an experiment.** The **+** button in the header makes one
(`Untitled Experiment 1`) and drops you straight onto its protocol canvas.
Rename it from the canvas's **⋮** menu → *Rename experiment*.

**3. Import the use case.** **⋮** → *Import from file…* → pick the JSON for
your provider. That lays out the whole graph and, because a factor is
meaningless without both halves of its binding, brings the design spec with it:
its factors, five metrics, 10 replicates, the critic-gate coordination strategy.

Import **merges alongside, never replaces** — nothing you already have is
overwritten, but importing twice into the same canvas gives you two copies of
the graph. Use a fresh, empty experiment.

**4. Add the LLM credential.** Open the shared LLM node — it feeds all five
agents and all four critic gates, so it's the only place a model gets chosen.
Add the credential from the node itself, or from **Profile → LLM credentials**.
Once it's saved, the Model dropdown lists what that credential can actually
reach, and the node shows Effort or Temperature depending on which one the
selected model accepts.

**5. Register the dataset.** Open the *Myocardial Infarction Dataset* node →
*Register new dataset*:

| Field | Value |
| --- | --- |
| Name | `myocardial_infarction` |
| CSV file | `mi_ZSN.csv` |
| Target column | `mi_ZSN` |
| Data dictionary | `dict_ZSN.json` |

It's selected on the node as soon as it's registered. The **Data dictionary**
field is what makes this dataset workable: the agents are told to resolve every
column's meaning with `get_data_dictionary` rather than guess from its name, and
`dict_ZSN.json` is what that tool serves back. Nothing else to configure —
`asaree-workspace` publishes the dictionary into each cell's own workspace
directory when the pipeline opens it, so the reader finds it on the same shared
filesystem it already reads the data from.

**6. Split it 70/30.** Same node inspector → *Split dataset* → **Quick split**:

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

**7. Generate the cells.** Side panel → **Design** tab → *Generate design*. It
reports the total: 2 × 2 × 2 factors × 10 replicates = **80 cells**.

**8. Run.** Two ways:

- *Run cell* (top-right of the canvas) to pick one cell, then **Run** — do this
  first.
- *Run all cells* in the top bar: every not-yet-scored cell at once, enqueued as
  one batch.

Watch progress on the canvas itself, or in the side panel's **Runs** tab;
results land in **Cells** and **Results**.

## Cost

Generating the grid is free — cells are rows until a run starts — but 80 runs
of a five-agent pipeline, half of them at the top of the effort ladder, is a
real bill. Run one cell
first, and if you only want a smoke test, lower **Replicates** on the Design
tab *before* generating: `Generate design` is additive, so it creates missing
combinations and never deletes. Lowering replicates afterwards leaves the extra
cells in place (harmless unless you press *Run all cells*).

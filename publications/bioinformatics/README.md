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
| `myocardial-use-case.json` | The experiment: protocol graph (36 edges, 5 agents, 4 critic gates, 8 MCP tool bindings) + design spec |
| `mi_ZSN.csv` | The dataset — 1700 admissions × 111 features, target `mi_ZSN` |
| `dict_ZSN.json` | The data dictionary for those 111 columns |
| `import_use_case.py` | Imports all of the above into a running ASAREE |
| `stats/` | The paper's analysis scripts and outputs for the spinal runs (not part of the import) |

The dataset is the **ZSN** target of
[UCI's Myocardial Infarction Complications](https://archive.ics.uci.edu/dataset/579/myocardial+infarction+complications)
(Golovenkin et al.) — 1700 admissions, predicting chronic heart failure as a
complication. 23.2% positive, so `average_precision` is the design's primary
metric rather than accuracy.

The column names are short Russian-derived codes (`nr11`, `zab_leg_01`,
`S_AD_KBRIG`), which is why `dict_ZSN.json` matters: `asaree-sklearn-eda`'s
`get_data_dictionary` serves it back to an agent that asks what a column means.
ASAREE itself never parses it.

## Prerequisites

**1. A running ASAREE.** From the repo root:

```bash
GH_TOKEN=$(gh auth token) docker compose up -d --build
```

Every MCP server this use case needs (`asaree-workspace` and the six
`asaree-sklearn-*` servers) ships with ASAREE and auto-registers on startup —
there is nothing to register by hand.

**2. A user and an API token** — see the SDK's
[Auth bootstrap](../../sdk/README.md#auth-bootstrap). Export both:

```bash
export ASAREE_BASE_URL=http://localhost:8000
export ASAREE_API_KEY=<your token>
```

**3. An Azure Foundry credential**, since the protocol's LLM node is
`azure_foundry` (levels `claude-sonnet-5` and `claude-opus-5`):

```python
from asaree_client import AsareeClient

with AsareeClient() as client:
    client.llm_settings.set_key("azure_foundry", "<api key>", api_base="<resource name>")
```

To run against a different provider instead, change the LLM node's `provider`
on the protocol canvas after importing, and the `Azure Foundry:Model` factor's
levels to that provider's model ids.

## Import

```bash
uv run --with ./sdk python publications/bioinformatics/import_use_case.py
```

It prints what it did and the experiment id to open. Idempotent — run it again
and it reuses everything already there under the same names instead of making a
second copy.

Five steps:

1. **Registers the dataset** as `myocardial_infarction` from `mi_ZSN.csv`, with
   `dict_ZSN.json` attached and `mi_ZSN` as the target column.
2. **Splits it 70/30**, stratified on the target, seed 42 (see below).
3. **Creates the experiment** `Myocardial Infarction Use Case` and attaches the
   dataset to it.
4. **Creates the protocol** from the file's `graph`, repointing the dataset and
   MCP-server UUIDs baked into the export at your own install's rows.
5. **Applies the design spec** and materializes its cells.

Then open `/experiments/{id}/protocol` — the graph, its Design/Cells/Runs/
Results tabs, and a full grid of unstarted cells are all there.

## The split

`test_size=0.3`, `seed=42`, stratified on `mi_ZSN` — ASAREE's own
`POST /datasets/{id}/split/quick` (`client.datasets.quick_split`), not a
pre-split pair of files. Stratifying matters here: at a 23.2% positive rate an
unstratified 30% holdout can land the two halves at materially different base
rates, and every metric in the design is prevalence-sensitive.

The split is what the agents actually see — the workspace an agent opens is
built from `train_path`, and `test_path` is only touched by the final model
script. Re-splitting overwrites rather than accumulating, so if you want a
different holdout:

```python
client.datasets.quick_split(dataset_id, target_column="mi_ZSN", test_size=0.2, seed=7)
```

The importer skips step 2 entirely if the dataset already has both paths set,
so a re-import won't clobber a split you chose yourself.

## Running it

The design is 2 × 2 × 2 factors × 10 replicates = **80 cells**, each one a full
five-agent pipeline at reasoning effort up to `xhigh`. Generating the grid costs
nothing — cells are rows until you start a run — but running all 80 is a real
bill. Start with one:

```python
run = client.protocols.run(protocol_id, cell_label="<a label from the Cells tab>")
client.protocols.get_run(protocol_id, run.id)   # poll; starts as "pending"
```

When you're satisfied, launch the rest — one run per not-yet-scored cell,
enqueued all at once:

```python
batch = client.protocols.run_cells(protocol_id)
```

To make the grid itself smaller instead, lower `replicates` in
`myocardial-use-case.json`'s `design_spec` **before importing** (or edit it on
the Design tab and re-generate). `generate-design` is additive — it creates
missing combinations and never deletes, so lowering replicates afterwards
leaves the 72 extra cells in place. They're harmless if you never run them;
`run_cells` is what would pick them up.

## Optional: let agents read the data dictionary

`get_data_dictionary` fetches the registered dataset's `dictionary_json` back
out of the ASAREE API, and datasets are owner-scoped — so the MCP server needs
a token of its own to read yours. Set `ASAREE_INTERNAL_MCP_API_KEY` in `.env`
(any ordinary user API token; whoever owns it decides whose dictionaries the
server can see) and restart:

```bash
docker compose up -d
```

Left empty, the tool returns an `error` and the agents proceed on bare column
names — which for this dataset means reasoning about `nr11` and `zab_leg_01`
with no idea what they are. Worth setting.

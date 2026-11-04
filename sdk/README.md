# asaree-client

A synchronous SDK for ASAREE. It covers the complete programmatic research
workflow: agents and runs, experiments/designs/results, protocol publishing and
execution, datasets and workspace lineage, MCP servers, Agent Skills, OKF
knowledge, and per-user LLM settings.

Deliberately not a copy of `ares_client`: ASAREE's runs execute through a
background worker (`POST /runs` returns while the run is pending), so
`runs.wait()` polls until a terminal result. The notebook's old
`client.runs.update(run_id, metadata=...)` calls have no equivalent — that
data now belongs on a `FactorialReplicateResult` row, written via
`client.experiments.upsert_replicate(...)`.

## Auth bootstrap

ASAREE has no static server-wide API key; each user is provisioned once and
issues their own token through an unauthenticated client:

```python
from asaree_client import AsareeClient

with AsareeClient(base_url="http://localhost:8000") as bootstrap:
    user = bootstrap.users.create(email="researcher@example.com", password="secure-password")
    credential = bootstrap.users.issue_token(user.id, password="secure-password")

print(credential.token)  # Save once; the server never returns this value again.
```

Set the resulting token as `ASAREE_API_KEY` (sent as `X-API-Key`) for
subsequent clients. Alternatively, use `client.auth.register()` and
`client.auth.login()` for a refreshable Bearer session; successful login and
refresh calls update that client automatically.

## Usage

```python
from asaree_client import AsareeClient

client = AsareeClient(base_url="http://localhost:8000", api_key="...")

agent = client.agents.create(name="scorer", goal="...", model_config_data={"model": "claude-sonnet-5"})
experiment = client.experiments.create(name="tier-x-effort", factors=[
    {"name": "tier", "levels": ["baseline", "critic"]},
    {"name": "effort", "levels": ["low", "high"]},
])
# Creation also provisions the experiment's empty protocol canvas. Retrieve it
# to build the graph programmatically; the same canvas is immediately visible
# when this user opens the experiment in the GUI.
protocol = client.protocols.list(experiment_id=experiment.id)[0]
replicates = client.experiments.generate_design(experiment.id)

for replicate in replicates:
    run = client.runs.start(agent.id, "...", metadata={"workspace_id": replicate.replicate_label})
    client.experiments.upsert_replicate(
        experiment.id, replicate.replicate_label,
        run_id=run.id, metric_values={"roc_auc": 0.91},
    )

results = client.experiments.get_results(experiment.id)
```

### Runnable statistics demo

From the repository root, install this checkout's SDK and create the demo:

```bash
python -m pip install -e ./sdk
export ASAREE_BASE_URL="https://your-asaree-host"
export ASAREE_API_KEY="your-test-user-token"
python scripts/create_stats_demo.py --provider openai --model YOUR_CONFIGURED_MODEL
```

Choose a tool-capable model already configured in that user's Profile. Other
providers are supported through `--provider`. If the frontend has a different
origin, supply `--gui-url`; the script prints the experiment's canvas link.

This creates a synthetic 20-ticket benchmark, a two-stage classification/scoring
canvas, four cells (minimal/rubric prompt × clean/noisy input), and 15 whole-dataset
replicates per cell. It publishes the experiment without launching any runs.
Built-in metrics are Cost, Duration, and Total tokens. Custom metrics are
`accuracy`, `misclassified_count`, and `passed` (at least 17/20 correct).

The answer key is only available to the scoring stage. That agent executes the
supplied Python scoring function with the classifier's predictions, then reports
the printed JSON; custom measurements project fields from its final output.
This handoff is model-mediated: inspect a GUI Test Run to confirm the scorer
executed the supplied code and returned its stdout faithfully. Runtime totals
include both agents. Results may be constant, especially with capable models;
statistically significant differences are not guaranteed.

Use `--dry-run` to inspect the definition without credentials or network access,
`--replicates N` to change the replication, or `--name` to create another copy.
An existing active experiment with the same name is refused. Resource IDs are
printed as creation progresses so an interrupted setup can be inspected in the GUI.
After running the experiment, open **Results → Statistics → Analyze all metrics**.

For a refreshable account session instead of an API key:

```python
client = AsareeClient(base_url="http://localhost:8000")
client.auth.login(email="researcher@example.com", password="secure-password")

profile = client.auth.get_profile()
client.auth.refresh()  # Rotates both the access and refresh tokens in-place.
client.auth.logout()
```

MCP server credentials are write-only: responses report `server.authentication`
metadata but never the stored values.

```python
server = client.tools.create_server(
    name="search", transport="http", url="https://mcp.example/mcp",
    headers={"Authorization": "Bearer ..."},
)
client.tools.update_server(server.id, headers=None)  # None clears; omit to keep.

# OAuth: open the URL in any browser; ASAREE stores the tokens on callback.
authorization = client.tools.begin_oauth(server.id, scope="tools.read")
print(authorization.authorization_url)
client.tools.clear_credentials(server.id, revoke=True)
```

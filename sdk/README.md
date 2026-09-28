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

For a refreshable account session instead of an API key:

```python
client = AsareeClient(base_url="http://localhost:8000")
client.auth.login(email="researcher@example.com", password="secure-password")

profile = client.auth.get_profile()
client.auth.refresh()  # Rotates both the access and refresh tokens in-place.
client.auth.logout()
```

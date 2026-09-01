# asaree-client

A trimmed, synchronous SDK for ASAREE. It covers exactly the resources a
driver notebook needs to run a factorial experiment end to end — agents,
runs, experiments/cells/replicates, datasets, and MCP tool passthrough — not a full
mirror of every ASAREE endpoint.

Deliberately not a copy of `ares_client`: ASAREE's runs execute inline
(`POST /runs` returns only once the run is terminal), so `runs.wait()` here
is a trivial re-fetch, not a poll loop. And the notebook's old
`client.runs.update(run_id, metadata=...)` calls have no equivalent — that
data now belongs on a `FactorialReplicateResult` row, written via
`client.experiments.upsert_replicate(...)`.

## Auth bootstrap

ASAREE has no static server-wide API key; each user is provisioned once and
issues their own token:

```bash
curl -X POST $ASAREE_BASE_URL/api/users -d '{"email": "...", "password": "..."}'
curl -X POST $ASAREE_BASE_URL/api/users/{user_id}/tokens -d '{"password": "..."}'
```

Every ASAREE route lives under `/api` (except `/health`) — the client
already knows this and prepends it to every request; you only need it
yourself for the one-time bootstrap above, made directly with curl.

This is a one-time setup step, not something the SDK does — set the
resulting token as `ASAREE_API_KEY` (sent as `X-API-Key`) for everything
after that.

## Usage

```python
from asaree_client import AsareeClient

client = AsareeClient(base_url="http://localhost:8000", api_key="...")

agent = client.agents.create(name="scorer", goal="...", model_config_data={"model": "claude-sonnet-5"})
experiment = client.experiments.create(name="tier-x-effort", factors=[
    {"name": "tier", "levels": ["baseline", "critic"]},
    {"name": "effort", "levels": ["low", "high"]},
])
replicates = client.experiments.generate_design(experiment.id)

for replicate in replicates:
    run = client.runs.start(agent.id, "...", metadata={"workspace_id": replicate.replicate_label})
    client.experiments.upsert_replicate(
        experiment.id, replicate.replicate_label,
        run_id=run.id, metric_values={"roc_auc": 0.91},
    )

results = client.experiments.analyze(
    experiment.id,
    condition_factors=["tier"],
    positive_levels={"tier": "critic"},
    reference_condition={"tier": "baseline"},
    primary_metric="roc_auc",
)
```

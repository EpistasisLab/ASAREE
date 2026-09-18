# Built-in metrics are evaluated; custom metrics are reported

ASAREE distinguishes two measurement paths.

Built-in metrics are owned by ASAREE. Their runtime producer reads the immutable attempt snapshot during finalization, computes supported numeric or Boolean observations, and supplies the values used for aggregation, comparison, and ranking.

Custom metrics are owned by the experiment author. A custom declaration points to an Agent, or to a Script/MCP tool directly connected to that Agent. ASAREE does not execute that source as a post-run evaluator. It captures the Agent's completed final output or the result of the last matching tool call already recorded in the Agent's execution trace. The last call is used whether it succeeded or failed.

## Consequences

- Users are responsible for prompts, scripts, MCP tools, and Agent behavior that produce the value they want to record.
- Custom values are opaque JSON. ASAREE does not coerce, validate, aggregate, rank, or choose a primary custom metric.
- If the Agent never produces the configured output or call, the observation is `unavailable` and its CSV cell is empty.
- Test Runs, per-node Play, production Results, and CSV export all consume the same persisted observation shape.
- Every observation retains producer provenance and a status. A measured JSON `null` remains distinguishable from an unavailable observation through that status and is exported as the literal `null`.
- Script and MCP configuration is validated for source identity, enabled state, and direct Agent wiring. Evaluator input mappings, result paths, deterministic execution, and side-effect acknowledgements are not part of custom measurement.
- Historical `deterministic_evaluator` bindings are normalized at the persistence boundary to reported bindings; no runtime adapter exists for them.

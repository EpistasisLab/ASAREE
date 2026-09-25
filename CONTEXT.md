# ASAREE

ASAREE runs factorial experiments over agent protocols: a user declares factors,
metrics, and an agent protocol graph on a canvas; the harness executes every
factor-combination cell and compares the recorded outcomes.

## Language

### Experiments

**Experiment**:
A factorial study comparing agent protocol conditions. Owns the design declaration, the protocol canvas, and all runs.
_Avoid_: Project, study, trial

**Factor**:
A declared experimental variable with discrete levels, bound to at least one canvas field before cells can run.
_Avoid_: Parameter, variable

**Cell**:
One unique factor combination together with all its planned replicates.
_Avoid_: Condition, treatment (accepted in prose, never in code)

**Replicate**:
One run of one cell. Counts of pending/run/scored are replicate counts, never cell counts.

**Primary metric**:
The optional declared metric Results uses to rank cells. An editable experiment may have none; when declared, it must be unique.
_Avoid_: Main metric, target metric

**Design revision**:
An immutable snapshot of the generated design. The current design is the one revision with `superseded_at IS NULL`.
_Avoid_: Design version

### Metrics

**Measurement plan**:
The experiment's declared set of metrics, their producers, and the bindings that provide each producer's inputs.
_Avoid_: Metric list, evaluation config

**Metric**:
A scalar experimental outcome that can be observed per replicate and aggregated across a cell.
_Avoid_: Measurement, score, result field

**Built-in metric**:
A metric whose definition and producer ASAREE supplies as one supported capability.
_Avoid_: Catalog metric, preset metric

**Custom metric**:
An experiment-owned metric whose definition or producer configuration is supplied by the user.
_Avoid_: User metric, freeform metric

**Metric producer**:
The declared mechanism that observes a metric from a completed replicate. A producer may emit several metrics and evaluation artifacts from one evaluation.
_Avoid_: Calculator, metric tool, extraction rule

**Runtime producer**:
A metric producer that observes execution facts recorded by the harness, such as cost, tokens, duration, or tool calls.
_Avoid_: Telemetry metric, process metric

**Deterministic evaluator**:
A metric producer that applies a reproducible evaluation procedure to declared run outputs and reference inputs.
_Avoid_: Score agent, metric script

**Model judge**:
A metric producer that applies a declared rubric to run output using a selected language model.
_Avoid_: Critic, evaluator agent

**Reported metric**:
A display/export-only custom metric containing either a configured Agent's completed
final output or the opaque result of its last matching Script or MCP tool call. ASAREE
never invokes the source to produce it, validates no result type, and leaves the value
absent when the Agent did not complete or call the configured tool.
_Avoid_: Agent metric, parsed metric

**Metric observation**:
One producer's outcome for one metric on one replicate, including whether it was measured, unavailable, failed, timed out, cancelled, or not applicable.
_Avoid_: Metric value, score record

**Evaluation artifact**:
A structured diagnostic such as a confusion matrix, calibration curve, or per-class report that accompanies observations but is not ranked or aggregated as a metric.
_Avoid_: Array metric, report metric

**Legacy value**:
A historical raw value outside the canonical aggregatable metric contract whose original producer was not recorded. It stays visible with `legacy.unknown` provenance but is not ranked; ASAREE never infers its producer from a display name, prompt, or tool call.
_Avoid_: Inferred observation, promoted score

**Context metric**:
A metric an Agent inspector includes in that agent's system prompt as evaluation guidance; it never exposes a future observation.
_Avoid_: Prompt metric

### Protocols

**Model**:
The configured language model an Agent or Critic Gate uses to produce or review output.
_Avoid_: AI, LLM (when naming the protocol role)

**Model connection**:
The required relationship assigning exactly one Model to an Agent or Critic Gate.
_Avoid_: AI connection, LLM connection

### Runs

**Protocol canvas**:
The draft editable graph. Production runs never read it directly.
_Avoid_: Workflow, pipeline

**Published revision**:
The immutable protocol snapshot a run executes; created by publishing the canvas.
_Avoid_: Protocol version

**Gated pair**:
An agent node with its critic gate. `approved` and `revisions_used` are recorded on the worker's node-run.
_Avoid_: Reviewer, supervisor

**Attempt**:
One ProtocolRun for a replicate; later attempts supersede earlier ones but every attempt's own facts stay immutable.

# Existing-tool comparators for ASAREE

Research date: 2026-09-25. This is a primary-source scan prompted by the review
comment that ASAREE is "a potentially useful, openly available analytical
sandbox whose practical value is plausible, but whose advantage over existing
tools has not been demonstrated." Tools released after the manuscript's
comparison cut-off should be labeled as such rather than presented as omissions
from the original submission.

## What ASAREE is claiming

The repository describes ASAREE as a workbench for running LLM agents as
designed experiments: users visually assemble a protocol, bind factors to node
configuration, materialize a full factorial design, run replicates, and compare
recorded metrics. It also versions datasets and production protocol snapshots
and exposes tools through MCP ([project README](../../README.md)). The public
use case is an agent sequence for tabular biomedical ML, varied in a 2 × 2 × 2
model/effort/critic design with ten replicates
([use-case README](README.md)).

That description touches four established product/research categories. The
reviewer may mean any of them; the first two are the most direct.

## 1. Evaluation and experiment platforms — closest functional comparators

### Arize Phoenix (highest-priority comparator)

Phoenix is open source and explicitly positions itself as a platform for
"experimentation, evaluation, and troubleshooting" of AI/LLM applications. It
supports versioned datasets, side-by-side experiments, deterministic and
LLM-judge evaluators, and repeated runs; its client API exposes a `repetitions`
argument and stores experiment/evaluation results for comparison
([official overview](https://arize.com/docs/phoenix/),
[dataset concepts](https://arize.com/docs/phoenix/learn/datasets-and-experiments/datasets-concepts),
[experiment API](https://arize-phoenix.readthedocs.io/projects/client/api/experiments.html)).

Why the reviewer may have it in mind: this is the clearest existing open-source
answer to "systematically run a stochastic agent repeatedly against versioned
data and compare scores." ASAREE needs to demonstrate more than the generic
ability to store datasets, traces, repetitions, and scores. Its plausible
distinction is the first-class *factorial design over fields inside a visual
multi-agent protocol*, including factor binding, generated cells/replicates,
immutable executable protocol revisions, and biomedical data-workspace lineage.

### MLflow GenAI evaluation

MLflow evaluates tool-using agents from datasets or traces, records the result
as an experiment run, and supplies agent-specific scorers such as tool-call
correctness and efficiency. It also supports deterministic code scorers and LLM
judges; classic MLflow separately evaluates classification and regression models
([agent evaluation](https://mlflow.org/docs/latest/genai/eval-monitor/running-evaluation/agents/),
[scorers](https://mlflow.org/docs/latest/genai/eval-monitor/scorers/),
[classic model evaluation](https://mlflow.org/docs/latest/ml/evaluation)).

Why it matters: ASAREE's use case produces ordinary supervised-ML outcomes as
well as agent runtime outcomes. A convincing comparison should show what ASAREE
adds beyond an MLflow-tracked loop over model/agent configurations.

### LangSmith, Braintrust, W&B Weave, and Promptfoo

- LangSmith supports offline benchmarks over datasets, comparisons among
  application versions, code and model-judge evaluators, pairwise/summary
  evaluators, and agent examples that verify expected tool calls
  ([official evaluation docs](https://docs.langchain.com/langsmith/evaluation-types)).
- Braintrust defines an evaluation as data + task + scores and stores offline
  evaluations as experiments. Its datasets are versioned and can be populated
  from production, staging, evaluations, or manual examples
  ([experiments](https://www.braintrust.dev/docs/guides/experiments),
  [datasets](https://www.braintrust.dev/docs/guides/datasets)).
- W&B Weave logs datasets, model outputs, per-example scores and summary
  metrics, then compares multiple evaluations in its UI
  ([official EvaluationLogger guide](https://weave-docs.wandb.ai/guides/evaluation/evaluation_logger)).
- Promptfoo is an open-source CLI/library that crosses prompts/models with test
  cases and evaluates them through deterministic assertions, custom code, or
  model-graded assertions; it explicitly covers agent quality and trajectory
  goal success
  ([getting started](https://www.promptfoo.dev/docs/getting-started/),
  [assertions and metrics](https://www.promptfoo.dev/docs/configuration/expected-outputs/)).

These are direct comparators for the evaluation/result-management layer, but
less direct for ASAREE's visual protocol construction and factorial-design
semantics. They are still likely to be named by an agent-evaluation reviewer.

### Inspect AI

The UK AI Security Institute's open-source Inspect framework models evaluations
as datasets, solvers/agents, tools, sandboxes, and scorers; it supports
multi-agent primitives, arbitrary external agents, and several isolated
execution backends ([official documentation](https://inspect.aisi.org.uk/)).

Inspect is a direct comparator if ASAREE is framed as an *agent evaluation
harness*. It is more benchmark/code oriented than ASAREE's GUI and biomedical
workflow, but its mature sandboxing and task/scorer abstraction set a baseline
for claims about reproducibility and safe execution.

## 2. Visual agent workflow builders — closest interface comparators

### AutoGen Studio

AutoGen Studio offers a drag-and-drop/JSON interface for teams, agents, tools,
models, and termination conditions, plus a playground for running, inspecting,
and debugging multi-agent sessions. Its paper explicitly describes it as a
no-code tool for prototyping, debugging, and evaluating multi-agent workflows
([official docs](https://microsoft.github.io/autogen/0.7.1/user-guide/autogenstudio-user-guide/index.html),
[Microsoft Research publication](https://www.microsoft.com/en-us/research/publication/autogen-studio-a-no-code-developer-tool-for-building-and-debugging-multi-agent-systems/)).

This is probably the first tool a reviewer will cite against the protocol
canvas. ASAREE should not claim novelty merely for drag-and-drop multi-agent
composition; the defensible comparison is whether a visual workflow can be
turned into a controlled factorial experiment with replicated, statistically
comparable outcomes.

### Flowise and Langflow

Flowise is an open-source visual platform for single- and multi-agent workflows,
with models, branching/loops, MCP, traces, datasets, evaluators, and evaluation
runs. Its packaged evaluation feature is documented as Cloud/Enterprise
functionality ([overview](https://docs.flowiseai.com/),
[evaluations](https://docs.flowiseai.com/using-flowise/evaluations)). Langflow's
visual editor connects prompts, models, data, agents, MCP servers, and tools;
flows can be run in a playground, exported as JSON, served through APIs, or
exposed as MCP tools
([visual editor](https://docs.langflow.org/concepts-overview),
[agents](https://docs.langflow.org/components-agents)).

These are direct comparators for practical visual authoring and MCP integration.
Neither cited documentation foregrounds designed experiments over internal node
fields; that potential gap should be demonstrated with a feature matrix and a
worked head-to-head task rather than asserted.

## 3. Biomedical/data-science agents — task-level comparators

### BioMedAgent, Agentomics, Biomni, AIDE, MLAgentBench, and STELLA

BioMedAgent is a particularly important task-level comparator. It is a
self-evolving multi-agent framework that accepts natural-language biomedical
analysis tasks, learns to use and chain bioinformatics tools, and covers
cross-omics analysis, ML modeling, and pathology image segmentation. Its
authors report a 77% success rate on the 327-task BioMed-AQA benchmark and
external evaluation on BixBench
([Nature Biomedical Engineering article](https://doi.org/10.1038/s41551-026-01634-6),
[official repository](https://github.com/BOBQWERA/BioMedAgent)). It postdates
ASAREE v0.2.0, but is now one of the clearest practical biomedical-agent
comparators and should be acknowledged in a current revision.

Agentomics is an end-to-end biomedical ML agent system with validation
checkpoints, containerized execution, multiple model providers, and support for
biomedical foundation models. Its Bioinformatics paper benchmarks it across 20
datasets against human solutions and four agent systems: AIDE, MLAgentBench,
STELLA, and Biomni
([Bioinformatics article](https://academic.oup.com/bioinformatics/article/42/Supplement_1/btag250/8726289)).
The paper characterizes AIDE and MLAgentBench as generalist ML coding agents,
STELLA as a biomedical multi-agent system with manager/developer/critic/tool
agents, and Biomni as a general-purpose biomedical agent with an expert-vetted
tool environment.

These systems do not have ASAREE's same purpose: they seek a strong ML solution,
whereas ASAREE measures the effects of agent/protocol choices. Nevertheless,
they are important practical baselines for the myocardial/spinal-style use
case. If ASAREE claims usefulness for producing biomedical classifiers, a
reviewer can reasonably ask how its output quality, success rate, wall time, and
cost compare with at least one autonomous ML agent and a non-agent baseline.

### BRAD and Coala

BRAD is an open agentic bioinformatics system that connects LLMs to literature,
databases, custom software, and user data, records detailed interaction logs,
and demonstrates an automated biomarker/enrichment workflow
([Bioinformatics article](https://academic.oup.com/bioinformatics/article/41/5/btaf159/8125018)).
Coala converts CWL-described command-line tools into MCP tools and executes them
in containers, targeting reproducible local bioinformatics analysis
([Bioinformatics article](https://academic.oup.com/bioinformatics/article/42/9/btag641/8771239)).

They are adjacent rather than direct: BRAD overlaps in agentic biomedical
analysis and provenance, while Coala overlaps in MCP tool integration and
reproducible execution. Both weaken broad claims that ASAREE uniquely makes
bioinformatics tools accessible to LLM agents.

## 4. Conventional analytics, workflow, and AutoML systems — necessary baselines

- Galaxy is an open web platform explicitly built for accessible,
  reproducible, transparent computational biomedical research; it records the
  information needed to repeat complete analyses
  ([Galaxy project](https://galaxyproject.org/galaxy-project/)).
- KNIME is an open-source visual workflow system for data access,
  transformation, analysis, modeling, and visualization
  ([official documentation](https://docs.knime.com/ap/latest/)).
- Orange is open-source visual programming for machine learning and data
  visualization, with canvas-connected widgets and no-code workflows
  ([official site](https://orangedatamining.com/)).
- AutoGluon trains and ranks model families from tabular data and exposes a
  model leaderboard; its paper describes highly accurate tabular AutoML from a
  single line of Python
  ([official tutorial](https://auto.gluon.ai/stable/tutorials/tabular/tabular-quick-start.html),
  [paper](https://arxiv.org/abs/2003.06505)). The Agentomics paper also names
  auto-sklearn and H2O alongside AutoGluon as established AutoML frameworks
  ([Bioinformatics article](https://academic.oup.com/bioinformatics/article/42/Supplement_1/btag250/8726289)).

These are not agent-experiment platforms. They matter because "analytical
sandbox" and the paper's visual tabular-ML use case overlap with capabilities
biomedical users already recognize. The practical advantage should therefore
be stated narrowly: ASAREE is for causal/comparative study of stochastic agent
protocol configurations, not simply visual analytics, workflow reproducibility,
or automated classifier search.

## Likely reviewer shortlist

If space permits only a compact comparison, prioritize:

1. **Phoenix** — strongest open-source experiment/evaluation overlap, including
   versioned datasets and repetitions.
2. **AutoGen Studio** — strongest visual multi-agent-workflow overlap.
3. **MLflow** — strongest bridge between agent evaluation and conventional ML
   experiment tracking/evaluation.
4. **BioMedAgent, Agentomics, and Biomni** — strongest task-level overlap for
   autonomous biomedical analysis/ML; AIDE and MLAgentBench are useful
   generalist agent baselines.
5. **Galaxy or KNIME** — the established biomedical/visual analytical workflow
   baseline.

Flowise/Langflow, LangSmith, Braintrust, Weave, Promptfoo, Inspect, BRAD, Coala,
Orange, and AutoGluon belong in a broader related-work table or supplement.

## What would actually answer the criticism

A feature inventory alone will probably not satisfy "advantage ... has not been
demonstrated." A focused revision should:

1. Add a capability table whose rows distinguish visual multi-agent authoring,
   arbitrary internal-field factors, full-factorial cell generation,
   repetitions, statistical comparison, immutable executable revisions,
   versioned biomedical datasets, deterministic + model-judge metrics, MCP,
   self-hosting, and run-level cost/time capture.
2. Recreate one ASAREE experiment in the nearest feasible baseline—preferably
   Phoenix or MLflow around an equivalent agent workflow—and report setup/code
   burden, completeness of provenance, and whether the factorial comparison can
   be expressed natively or only through bespoke orchestration.
3. Add a non-agent practical baseline such as AutoGluon for predictive quality,
   time, and cost. This prevents evidence that one agent configuration beats
   another from being mistaken for evidence that the sandbox improves the
   biomedical analysis.
4. Frame the advantage as **designed experimentation on agent protocols**, not
   as visual workflow construction, generic LLM evaluation, AutoML, or a new
   bioinformatics agent. Existing tools already substantiate all four broader
   categories.

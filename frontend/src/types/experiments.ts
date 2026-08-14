export interface DesignFactor {
  name: string
  levels: unknown[]
}

export interface DesignMetric {
  name: string
  primary: boolean
  direction: 'maximize' | 'minimize'
}

// "sequential" (default when design_spec.coordination_strategy is absent --
// today's exact existing DAG-handoff behavior) and "critic_gate" (promotes
// the existing gated-pair mechanism to an explicit declaration) are real.
// The rest are named placeholders for ARES's own coordination-category
// patterns, pending a later ARES -> agentic-core migration -- selectable and
// saveable, but services.protocol_execution rejects a run attempted with one
// of these active. See COORDINATION_STRATEGY_CATALOG for display metadata.
export type CoordinationStrategySlug =
  | 'sequential'
  | 'critic_gate'
  | 'supervisor_architecture'
  | 'swarm_architecture'
  | 'task_bidding'
  | 'supervision_tree_with_guarded_capabilities'
  | 'event_driven_reactivity'
  | 'multi_agent_planning'

export interface CoordinationStrategyConfig {
  slug: CoordinationStrategySlug
  params?: Record<string, unknown>
}

export const COORDINATION_STRATEGY_CATALOG: {
  slug: CoordinationStrategySlug
  label: string
  description: string
  implemented: boolean
}[] = [
  {
    slug: 'sequential',
    label: 'Sequential (default)',
    description: "Today's pipeline handoff -- each agent's output becomes the next agent's input, in edge order.",
    implemented: true,
  },
  {
    slug: 'critic_gate',
    label: 'Critic Gate',
    description: 'A reviewer agent approves or requests revisions at a fixed point in the sequential pipeline.',
    implemented: true,
  },
  {
    slug: 'supervisor_architecture',
    label: 'Supervisor',
    description: 'One coordinator delegates sub-tasks to worker agents and aggregates their results.',
    implemented: false,
  },
  {
    slug: 'swarm_architecture',
    label: 'Swarm',
    description: 'Agents self-organize around a shared task board -- no fixed coordinator.',
    implemented: false,
  },
  {
    slug: 'task_bidding',
    label: 'Task Bidding',
    description: 'Agents competitively bid for tasks; the best-scoring bid is awarded the work.',
    implemented: false,
  },
  {
    slug: 'supervision_tree_with_guarded_capabilities',
    label: 'Supervision Tree',
    description: 'A hierarchical tree of agents with capability-scoped subtrees and structured failure recovery.',
    implemented: false,
  },
  {
    slug: 'event_driven_reactivity',
    label: 'Event-Driven',
    description: 'Agents react to published events on shared topics instead of a fixed plan.',
    implemented: false,
  },
  {
    slug: 'multi_agent_planning',
    label: 'Multi-Agent Planning',
    description: 'Multiple planner agents propose in parallel; a coordinator merges them into one plan for workers.',
    implemented: false,
  },
]

export interface DesignSpec {
  factors?: DesignFactor[]
  // Copies per factor-level combination (default 1 when absent).
  replicates?: number
  // Shuffles generated cells' execution order when set (never affects which
  // combinations/replicates are generated).
  randomization_seed?: number | null
  // Declared up front, unlike the Results tab's purely-inferred metric keys
  // -- lets the Design tab show "Metrics" before any cell has run.
  metrics?: DesignMetric[]
  coordination_strategy?: CoordinationStrategyConfig
  [key: string]: unknown
}

export interface Experiment {
  id: string
  name: string
  description: string | null
  hypothesis: string | null
  design_type: string
  task_brief: Record<string, unknown> | null
  design_spec: DesignSpec | null
  dataset_id: string | null
  created_at: string
  archived_at: string | null
}

export interface Cell {
  id: string
  cell_label: string
  run_id: string | null
  workspace_id: string | null
  factor_values: Record<string, unknown> | null
  metric_values: Record<string, unknown> | null
  artifacts: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

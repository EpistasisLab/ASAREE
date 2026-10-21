import type { MeasurementPlan } from '@/types/experiments'
import type { NodeRunState, OutputContractField, ProtocolGraph, ProtocolRun } from '@/types/protocols'

// One field picked out of a producer's JSON output -- the backend's
// `config.projections[outputKey]` (services/derived_metrics.py). `path` is an
// exact dotted key (result keys may themselves contain dots, e.g.
// `metrics_at_0.5`), and `length` records a list/object's size instead of it.
// A metric with no projection records the producer's whole output.
export interface FieldProjection {
  path: string
  transform?: 'length'
}

export interface MetricField {
  path: string
  // Path segments, kept so a default metric name can use the leaf key
  // without re-splitting a path whose keys contain dots.
  segments: string[]
  countable: boolean
  // 'declared' = from an Output Parser contract; 'observed' = from a run.
  origin: 'declared' | 'observed'
}

type Binding = MeasurementPlan['producers'][number]

export function bindingProjection(binding: Binding | undefined, metricId: string | undefined): FieldProjection | undefined {
  if (!binding || !metricId) return undefined
  const outputKey = Object.entries(binding.outputs).find(([, id]) => id === metricId)?.[0]
  const projections = binding.config.projections
  const projection = outputKey && projections && typeof projections === 'object'
    ? (projections as Record<string, unknown>)[outputKey]
    : undefined
  if (!projection || typeof projection !== 'object') return undefined
  const { path, transform } = projection as { path?: unknown; transform?: unknown }
  if (typeof path !== 'string' || !path) return undefined
  return transform === 'length' ? { path, transform } : { path }
}

export function projectionConfig(projection: FieldProjection | undefined): Record<string, unknown> {
  return projection ? { projections: { value: projection } } : {}
}

export function projectionLabel(projection: FieldProjection | undefined): string {
  if (!projection) return 'whole output'
  return projection.transform === 'length' ? `${projection.path} (count)` : projection.path
}

const COUNTABLE_CONTRACT_TYPES = new Set(['list', 'dict', 'array', 'object'])

function contractFields(fields: OutputContractField[] | undefined): MetricField[] {
  return (fields ?? []).filter((field) => field.name.trim()).map((field) => ({
    path: field.name.trim(),
    segments: [field.name.trim()],
    countable: COUNTABLE_CONTRACT_TYPES.has(field.type),
    origin: 'declared',
  }))
}

// The Agent's declared output shape: its enabled wired Output Parser, else
// the legacy contract stored on the Agent itself.
export function declaredAgentFields(graph: ProtocolGraph | undefined, agentNodeId: string): MetricField[] {
  if (!graph) return []
  const parser = graph.edges
    .filter((edge) => edge.target === agentNodeId && edge.targetHandle === 'output_parser')
    .map((edge) => graph.nodes.find((node) => node.id === edge.source))
    .find((node) => node?.type === 'output_parser')
  if (parser) {
    const config = 'config' in parser.data ? parser.data.config : undefined
    if (config && 'enabled' in config && config.enabled === false) return []
    return contractFields(config && 'output_contract' in config ? config.output_contract?.fields : undefined)
  }
  const agent = graph.nodes.find((node) => node.id === agentNodeId)
  const config = agent && 'config' in agent.data ? agent.data.config : undefined
  return contractFields(config && 'output_contract' in config ? config.output_contract?.fields : undefined)
}

function flatten(value: unknown, segments: string[], into: MetricField[]) {
  if (segments.length) {
    const countable = Array.isArray(value) || (value !== null && typeof value === 'object')
    into.push({ path: segments.join('.'), segments, countable, origin: 'observed' })
  }
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    for (const [key, child] of Object.entries(value)) flatten(child, [...segments, key], into)
  }
}

export function observedFields(payload: unknown): MetricField[] {
  const fields: MetricField[] = []
  if (payload && typeof payload === 'object' && !Array.isArray(payload)) flatten(payload, [], fields)
  return fields
}

// The node's typed output from the most recent run that completed it.
export function latestNodePayload(runs: ProtocolRun[] | undefined, nodeId: string): Record<string, unknown> | undefined {
  const sorted = [...(runs ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at))
  for (const run of sorted) {
    const state: NodeRunState | undefined = run.node_runs?.[nodeId]
    if (state?.status === 'completed' && state.payload && typeof state.payload === 'object') return state.payload
  }
  return undefined
}

export function mergeFields(...lists: MetricField[][]): MetricField[] {
  const byPath = new Map<string, MetricField>()
  for (const field of lists.flat()) {
    const existing = byPath.get(field.path)
    byPath.set(field.path, existing ? { ...existing, countable: existing.countable || field.countable } : field)
  }
  return [...byPath.values()]
}

function identifier(value: string) {
  return value.replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'field'
}

// A default metric name for each picked field: its leaf key, widened with
// parent keys only where two picks (or an existing metric) would collide.
export function defaultFieldNames(
  picks: { field: Pick<MetricField, 'segments'>; projection: FieldProjection }[],
  taken: Iterable<string>,
): string[] {
  const used = new Set([...taken].map((name) => name.toLocaleLowerCase()))
  const candidate = (segments: string[], depth: number, count: boolean) =>
    identifier(segments.slice(-depth).join('_')) + (count ? '_count' : '')
  const depths = picks.map(() => 1)
  for (let changed = true; changed;) {
    changed = false
    const names = picks.map((pick, index) => candidate(pick.field.segments, depths[index], pick.projection.transform === 'length'))
    names.forEach((name, index) => {
      const clash = used.has(name.toLocaleLowerCase()) || names.some((other, j) => j !== index && other === name)
      if (clash && depths[index] < picks[index].field.segments.length) {
        depths[index] += 1
        changed = true
      }
    })
  }
  const result: string[] = []
  picks.forEach((pick, index) => {
    const base = candidate(pick.field.segments, depths[index], pick.projection.transform === 'length')
    let name = base
    for (let suffix = 2; used.has(name.toLocaleLowerCase()); suffix += 1) name = `${base}_${suffix}`
    used.add(name.toLocaleLowerCase())
    result.push(name)
  })
  return result
}

export function segmentsForPath(path: string, fields: MetricField[]): string[] {
  return fields.find((field) => field.path === path)?.segments ?? path.split('.')
}

import { reconcileKnowledgeFactor, KNOWLEDGE_FACTOR_PATH } from './knowledgeFactors'
import { reconcileDatasetFactor, DATASET_FACTOR_PATH } from './datasetFactors'
import type { DesignFactor, DesignSpec } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'
import { reconcileSkillFactor, SKILL_FACTOR_PATH } from './skillFactors'

export interface FactorBindingDiscrepancy {
  nodeId: string
  nodeLabel: string
  fieldPath: string
  factorName: string
  reason: 'factor is no longer declared' | 'field no longer exists on the canvas' | 'published value does not match the first (canvas baseline) level'
}

const MISSING = Symbol('missing factor-bound field')

export function pathValue(root: Record<string, unknown>, dottedPath: string): unknown | typeof MISSING {
  let value: unknown = root
  for (const part of dottedPath.split('.')) {
    if (typeof value !== 'object' || value === null || !(part in value)) return MISSING
    value = (value as Record<string, unknown>)[part]
  }
  return value
}

function valuesEqual(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

function isBooleanFactor(factor: DesignFactor): boolean {
  return factor.level_type === 'boolean' || (
    factor.level_type === undefined &&
    factor.levels.length === 2 &&
    factor.levels.every((level) => typeof level === 'boolean')
  )
}

function withPathValue(root: Record<string, unknown>, dottedPath: string, nextValue: unknown): Record<string, unknown> {
  const parts = dottedPath.split('.')
  const next = { ...root }
  let target = next
  let source: Record<string, unknown> = root
  for (const part of parts.slice(0, -1)) {
    const sourceChild = source[part]
    const nextChild = typeof sourceChild === 'object' && sourceChild !== null
      ? { ...(sourceChild as Record<string, unknown>) }
      : {}
    target[part] = nextChild
    target = nextChild
    source = typeof sourceChild === 'object' && sourceChild !== null
      ? sourceChild as Record<string, unknown>
      : {}
  }
  target[parts.at(-1)!] = nextValue
  return next
}

/**
 * Keep a factor's current canvas value in the baseline (first) slot. A value
 * matching an alternate level is promoted; any other value *replaces* the old
 * baseline. Bound fields save on every keystroke, so keeping the old baseline
 * as an extra level would turn each pause mid-edit into a new treatment.
 */
export function factorWithCanvasBaseline(factor: DesignFactor, currentValue: unknown): DesignFactor {
  if ((factor.level_type === 'skill_selection' || factor.level_type === 'skill_toggle' || factor.level_type === 'dataset_selection' || factor.level_type === 'dataset_toggle' || factor.level_type === 'knowledge_selection' || factor.level_type === 'knowledge_toggle')) return factor
  if (isBooleanFactor(factor) || valuesEqual(factor.levels[0], currentValue)) return factor

  const currentIndex = factor.levels.findIndex((level) => valuesEqual(level, currentValue))
  if (currentIndex < 0) {
    return { ...factor, levels: [currentValue, ...factor.levels.slice(1)] }
  }
  const keptIndexes = factor.levels.flatMap((_, index) => index === currentIndex ? [] : [index])
  const levels = [currentValue, ...keptIndexes.map((index) => factor.levels[index])]
  const labels = factor.level_labels
    ? [factor.level_labels[currentIndex] ?? 'Baseline', ...keptIndexes.map((index) => factor.level_labels?.[index] ?? '')]
    : undefined
  return { ...factor, levels, ...(labels ? { level_labels: labels } : {}) }
}

/**
 * Reconcile stored factor baselines from the live canvas. A shared factor is
 * only reconciled when every bound field currently agrees; otherwise the
 * validator reports the ambiguity and the user must split the factor.
 */
export function reconcileFactorBaselines(designSpec: DesignSpec | null | undefined, graph: ProtocolGraph | undefined): DesignFactor[] {
  const factors = designSpec?.factors ?? []
  const valuesByFactor = new Map<string, unknown[]>()
  for (const node of graph?.nodes ?? []) {
    const data = node.data as unknown as Record<string, unknown>
    for (const [fieldPath, rawFactorName] of Object.entries((data.factor_bindings ?? {}) as Record<string, string>)) {
      const value = pathValue(data, fieldPath)
      if (value === MISSING) continue
      const name = rawFactorName.trim()
      valuesByFactor.set(name, [...(valuesByFactor.get(name) ?? []), value])
    }
  }

  return factors.map((factor) => {
    if ((factor.level_type === 'skill_selection' || factor.level_type === 'skill_toggle') && graph) {
      const owner = graph.nodes.find((node) => node.data.factor_bindings?.[SKILL_FACTOR_PATH] === factor.name)
      return owner ? reconcileSkillFactor(factor, graph, owner.id) : factor
    }
    if ((factor.level_type === 'dataset_selection' || factor.level_type === 'dataset_toggle') && graph) {
      const owner = graph.nodes.find((node) => node.data.factor_bindings?.[DATASET_FACTOR_PATH] === factor.name)
      return owner ? reconcileDatasetFactor(factor, graph, owner.id) : factor
    }
    if ((factor.level_type === 'knowledge_selection' || factor.level_type === 'knowledge_toggle') && graph) {
      const owner = graph.nodes.find((node) => node.data.factor_bindings?.[KNOWLEDGE_FACTOR_PATH] === factor.name)
      return owner ? reconcileKnowledgeFactor(factor, graph, owner.id) : factor
    }
    const values = valuesByFactor.get(factor.name.trim()) ?? []
    if (values.length === 0 || values.some((value) => !valuesEqual(value, values[0]))) return factor
    return factorWithCanvasBaseline(factor, values[0])
  })
}

/** Mirror a changed factor baseline back into every field bound to it. */
export function graphWithFactorBaseline(graph: ProtocolGraph, factorName: string, baseline: unknown): ProtocolGraph {
  return {
    ...graph,
    nodes: graph.nodes.map((node) => {
      const data = node.data as unknown as Record<string, unknown>
      const paths = Object.entries((data.factor_bindings ?? {}) as Record<string, string>)
        .filter(([, name]) => name === factorName)
        .map(([path]) => path)
      if (paths.length === 0) return node
      const nextData = paths.reduce((next, path) => withPathValue(next, path, baseline), data)
      return { ...node, data: nextData as typeof node.data }
    }),
  }
}

/** Declared factors without a live canvas field to vary are not runnable. */
export function unboundFactorNames(designSpec: DesignSpec | null, graph: ProtocolGraph | undefined): string[] {
  const declared = designSpec?.factors?.map((factor) => factor.name.trim()).filter(Boolean) ?? []
  const bound = new Set(
    (graph?.nodes ?? []).flatMap((node) => Object.values(node.data.factor_bindings ?? {})),
  )
  return declared.filter((name) => !bound.has(name))
}

/** Factor bindings that would make a cell replace what the canvas currently shows. */
export function factorBindingDiscrepancies(
  designSpec: DesignSpec | null | undefined,
  graph: ProtocolGraph | undefined,
): FactorBindingDiscrepancy[] {
  const factors = new Map((designSpec?.factors ?? []).map((factor) => [factor.name.trim(), factor]))
  const discrepancies: FactorBindingDiscrepancy[] = []

  for (const node of graph?.nodes ?? []) {
    const data = node.data as unknown as Record<string, unknown>
    const nodeLabel = typeof data.label === 'string' && data.label ? data.label : node.type ?? node.id
    const bindings = (data.factor_bindings ?? {}) as Record<string, string>
    for (const [fieldPath, rawFactorName] of Object.entries(bindings)) {
      const factorName = rawFactorName.trim()
      const factor = factors.get(factorName)
      const value = pathValue(data, fieldPath)
      const reason = !factor
        ? 'factor is no longer declared'
        : value === MISSING
          ? 'field no longer exists on the canvas'
          : (factor.level_type === 'skill_selection' || factor.level_type === 'skill_toggle' || factor.level_type === 'dataset_selection' || factor.level_type === 'dataset_toggle' || factor.level_type === 'knowledge_selection' || factor.level_type === 'knowledge_toggle') || isBooleanFactor(factor) || valuesEqual(value, factor.levels[0])
            ? null
            : 'published value does not match the first (canvas baseline) level'
      if (reason) discrepancies.push({ nodeId: node.id, nodeLabel, fieldPath, factorName, reason })
    }
  }
  return discrepancies
}

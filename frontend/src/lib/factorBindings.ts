import type { DesignSpec } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'

export interface FactorBindingDiscrepancy {
  nodeId: string
  nodeLabel: string
  fieldPath: string
  factorName: string
  reason: 'factor is no longer declared' | 'field no longer exists on the canvas' | 'published value does not match any declared level'
}

const MISSING = Symbol('missing factor-bound field')

function pathValue(root: Record<string, unknown>, dottedPath: string): unknown | typeof MISSING {
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
          : factor.levels.some((level) => valuesEqual(value, level))
            ? null
            : 'published value does not match any declared level'
      if (reason) discrepancies.push({ nodeId: node.id, nodeLabel, fieldPath, factorName, reason })
    }
  }
  return discrepancies
}

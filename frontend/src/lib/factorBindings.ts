import type { DesignSpec } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'

/** Declared factors without a live canvas field to vary are not runnable. */
export function unboundFactorNames(designSpec: DesignSpec | null, graph: ProtocolGraph | undefined): string[] {
  const declared = designSpec?.factors?.map((factor) => factor.name.trim()).filter(Boolean) ?? []
  const bound = new Set(
    (graph?.nodes ?? []).flatMap((node) => Object.values(node.data.factor_bindings ?? {})),
  )
  return declared.filter((name) => !bound.has(name))
}

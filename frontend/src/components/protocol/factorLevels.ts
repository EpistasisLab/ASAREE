import type { DesignFactor } from '@/types/experiments'

export type LevelType = 'string' | 'text' | 'number' | 'boolean'

export const LEVEL_TYPE_LABELS: Record<LevelType, string> = {
  string: 'String',
  text: 'Long text',
  number: 'Number',
  boolean: 'Boolean',
}

export function levelTypeOf(factor: DesignFactor): LevelType {
  return factor.level_type ?? 'string'
}

export function parseLevelValue(raw: string, type: LevelType): unknown {
  return type === 'number' ? Number(raw) : raw
}

// A boolean factor's levels are always exactly [true, false] -- there's
// nothing to type in, so switching to it replaces whatever was there.
// Every other type starts with two blank rows, matching this app's existing
// "start with a couple of empty slots" convention (FactorBindableField's
// own popover).
export function defaultLevelsForType(type: LevelType): unknown[] {
  return type === 'boolean' ? [true, false] : ['', '']
}

// A factor bound to "System prompt" on Agent A and a factor bound to
// "System prompt" on Agent B are never the same thing -- they don't need to
// share levels, and one node's field being bound must never silently
// overwrite another's just because they happened to get the same default
// label. Scoping the name to its owning node (by label, not id -- ids make
// unreadable factor names) is what actually prevents that collision; the
// numeric suffix only kicks in for the rarer case of two nodes sharing the
// exact same current label (e.g. two never-renamed "Agent" nodes).
export function computeFactorName(nodeLabel: string, fieldLabel: string, existingNames: string[]): string {
  const base = `${nodeLabel}: ${fieldLabel}`
  if (!existingNames.includes(base)) return base
  let suffix = 2
  while (existingNames.includes(`${base} (${suffix})`)) suffix++
  return `${base} (${suffix})`
}

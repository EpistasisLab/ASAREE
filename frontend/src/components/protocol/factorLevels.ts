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

import { describe, expect, it } from 'vitest'
import { defaultFactorLevelLabels, factorLevelLabels } from './factorLevels'

describe('factor level labels', () => {
  it('defaults boolean treatments to their values', () => {
    expect(defaultFactorLevelLabels('Critic enabled', 2, 'boolean')).toEqual(['false', 'true'])
    expect(factorLevelLabels({ name: 'Critic enabled', levels: [false, true] })).toEqual([
      'false',
      'true',
    ])
    expect(factorLevelLabels({ name: 'Legacy order', levels: [true, false] })).toEqual(['true', 'false'])
  })

  it('keeps numbered defaults for other factor types', () => {
    expect(defaultFactorLevelLabels('Azure Foundry:Model', 2, 'string')).toEqual(['level1', 'level2'])
  })
})

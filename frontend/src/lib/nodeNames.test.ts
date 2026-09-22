import { describe, expect, it } from 'vitest'
import { nodeDisplayNames } from './nodeNames'

describe('nodeDisplayNames', () => {
  it('prefers the label the user set', () => {
    const names = nodeDisplayNames([{ id: 'n1', type: 'agent', data: { label: 'Analyst' } }])
    expect(names.get('n1')).toBe('Analyst')
  })

  it('falls back to the placeholder the unlabelled node shows on its card', () => {
    const names = nodeDisplayNames([
      { id: 'n1', type: 'output_parser', data: { label: '' } },
      { id: 'n2', type: 'llm_anthropic', data: {} },
    ])
    expect(names.get('n1')).toBe('Output Parser')
    expect(names.get('n2')).toBe('Anthropic')
  })

  it('numbers nodes that would otherwise share a name', () => {
    const names = nodeDisplayNames([
      { id: 'a', type: 'script', data: {} },
      { id: 'b', type: 'script', data: {} },
      { id: 'c', type: 'script', data: { label: 'Scorer' } },
    ])
    expect(names.get('a')).toBe('Script 1')
    expect(names.get('b')).toBe('Script 2')
    expect(names.get('c')).toBe('Scorer')
  })

  it('words an unknown node type rather than leaving a uuid', () => {
    const names = nodeDisplayNames([{ id: 'n1', type: 'web_search', data: {} }])
    expect(names.get('n1')).toBe('Web Search')
  })

  it('omits a node it cannot name, so callers keep their id fallback', () => {
    const names = nodeDisplayNames([{ id: 'n1', data: {} }])
    expect(names.has('n1')).toBe(false)
  })
})

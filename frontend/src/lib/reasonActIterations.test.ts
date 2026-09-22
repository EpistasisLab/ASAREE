import { describe, expect, it } from 'vitest'
import { isUnderIterated, raiseForTruncation, suggestedMaxIterations } from './reasonActIterations'
import type { ProtocolGraph, ProtocolNode } from '@/types/protocols'

function node(id: string, type: string, config: Record<string, unknown> = {}): ProtocolNode {
  return { id, type, position: { x: 0, y: 0 }, data: { label: id, config } } as unknown as ProtocolNode
}

function graph(nodes: ProtocolNode[], edges: ProtocolGraph['edges']): ProtocolGraph {
  return { nodes, edges }
}

const patternEdge = {
  id: 'e-pattern',
  source: 'pattern-1',
  target: 'agent-1',
  sourceHandle: null,
  targetHandle: 'architectural_pattern',
}

function wire(id: string, source: string, targetHandle: string) {
  return { id, source, target: 'agent-1', sourceHandle: null, targetHandle }
}

describe('suggestedMaxIterations', () => {
  it('is null when the pattern drives no agent', () => {
    const g = graph([node('pattern-1', 'pattern_reason_act'), node('agent-1', 'agent')], [])
    expect(suggestedMaxIterations(g, 'pattern-1')).toBeNull()
  })

  it('sizes the run that exposed the problem: 4 scripts plus a dataset and a skill', () => {
    const g = graph(
      [
        node('pattern-1', 'pattern_reason_act'),
        node('agent-1', 'agent'),
        node('s1', 'script'),
        node('s2', 'script'),
        node('s3', 'script'),
        node('s4', 'script'),
        node('d1', 'dataset'),
        node('k1', 'skill'),
      ],
      [
        patternEdge,
        wire('e1', 's1', 'tool'),
        wire('e2', 's2', 'tool'),
        wire('e3', 's3', 'tool'),
        wire('e4', 's4', 'tool'),
        wire('e5', 'd1', 'dataset'),
        wire('e6', 'k1', 'skill'),
      ],
    )
    // 4 + 4*5 + 2*2 = 28, rounded up to 30 -- the value that run needed and
    // its configured 15 did not give it.
    expect(suggestedMaxIterations(g, 'pattern-1')).toBe(30)
  })

  it('floors a bare agent at 10 rather than suggesting a number too small to matter', () => {
    const g = graph([node('pattern-1', 'pattern_reason_act'), node('agent-1', 'agent')], [patternEdge])
    expect(suggestedMaxIterations(g, 'pattern-1')).toBe(10)
  })

  it('ignores a disabled connector, which costs the loop nothing', () => {
    const g = graph(
      [
        node('pattern-1', 'pattern_reason_act'),
        node('agent-1', 'agent'),
        node('s1', 'script'),
        node('s2', 'script', { enabled: false }),
      ],
      [patternEdge, wire('e1', 's1', 'tool'), wire('e2', 's2', 'tool')],
    )
    // 4 + 5 = 9, floored to 10; the disabled second script would have made it 15.
    expect(suggestedMaxIterations(g, 'pattern-1')).toBe(10)
  })

  it('caps at the catalog schema maximum', () => {
    const scripts = Array.from({ length: 30 }, (_, i) => node(`s${i}`, 'script'))
    const g = graph(
      [node('pattern-1', 'pattern_reason_act'), node('agent-1', 'agent'), ...scripts],
      [patternEdge, ...scripts.map((s, i) => wire(`e${i}`, s.id, 'tool'))],
    )
    expect(suggestedMaxIterations(g, 'pattern-1')).toBe(100)
  })
})

describe('isUnderIterated', () => {
  it('says nothing when the wiring implies no suggestion', () => {
    expect(isUnderIterated(5, null)).toBe(false)
    expect(isUnderIterated(null, null)).toBe(false)
  })

  it('flags a cap below the suggestion, and an unset one', () => {
    expect(isUnderIterated(15, 30)).toBe(true)
    expect(isUnderIterated(null, 30)).toBe(true)
  })

  it('leaves a cap at or above the suggestion alone -- too high costs nothing', () => {
    expect(isUnderIterated(30, 30)).toBe(false)
    expect(isUnderIterated(100, 30)).toBe(false)
  })
})

describe('raiseForTruncation', () => {
  it('leaves the wiring estimate alone when no run has been truncated', () => {
    expect(raiseForTruncation(30, null)).toBe(30)
    expect(raiseForTruncation(30, undefined)).toBe(30)
  })

  it('pushes past a cap a real run already died at', () => {
    // 40 * 1.5 = 60, which beats the wiring's 30.
    expect(raiseForTruncation(30, 40)).toBe(60)
  })

  it('never climbs down from the wiring estimate', () => {
    // 15 * 1.5 = 23 -> 25, below the 30 the wiring already asked for.
    expect(raiseForTruncation(30, 15)).toBe(30)
  })

  it('still answers when the pattern drives no agent to estimate from', () => {
    expect(raiseForTruncation(null, 20)).toBe(30)
  })

  it('stays inside the catalog schema maximum', () => {
    expect(raiseForTruncation(100, 90)).toBe(100)
  })
})

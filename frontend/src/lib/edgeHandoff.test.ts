import { describe, expect, it } from 'vitest'
import type { Edge, Node } from '@xyflow/react'
import { describeHandoff, handoffPeers, handoffSenderFields, toDisplayPrompt, toStoredPrompt } from './promptReferences'
import { toPersistedGraph } from './protocolGraph'
import type { ProtocolEdge, ProtocolNode } from '@/types/protocols'

const nodes = [
  { id: 'a', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Analyst', config: {} } },
  {
    id: 'p',
    type: 'output_parser',
    position: { x: 0, y: 0 },
    data: { label: 'Parser', config: { output_contract: { name: 'r', fields: [{ name: 'notes', type: 'string' }, { name: 'recipe', type: 'array' }] } } },
  },
  { id: 'g', type: 'critic_gate', position: { x: 0, y: 0 }, data: { label: 'Gate', config: {} } },
  { id: 'b', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Builder', config: {} } },
] as unknown as ProtocolNode[]
const edges: ProtocolEdge[] = [
  { id: 'pa', source: 'p', target: 'a', sourceHandle: 'output_parser', targetHandle: 'output_parser' },
  { id: 'ag', source: 'a', target: 'g' },
  { id: 'gb', source: 'g', target: 'b', data: { handoff: { mode: 'selected', fields: [{ name: 'recipe', item_keys: ['name'] }] } } },
]

describe('edge handoff', () => {
  it("offers a gate's worker's fields on the gate's outgoing edge", () => {
    expect(handoffSenderFields(nodes, edges, 'g').map((f) => f.name)).toEqual(['notes', 'recipe'])
  })

  it('reports the narrowed handoff on the receiving side', () => {
    const [peer] = handoffPeers(nodes, edges, 'b').receives
    expect(peer.handoff?.mode).toBe('selected')
    expect(describeHandoff(peer.handoff)).toBe('1 field')
    expect(describeHandoff({ mode: 'full' })).toBeNull()
  })

  it('persists the handoff but not display-only edge data', () => {
    const flowEdges = edges.map((e) => ({ ...e, data: { ...e.data, directedFlow: true } })) as Edge[]
    const persisted = toPersistedGraph(nodes as unknown as Node[], flowEdges).edges
    expect(persisted.find((e) => e.id === 'gb')?.data).toEqual({ handoff: edges[2].data!.handoff })
    expect(persisted.find((e) => e.id === 'ag')).not.toHaveProperty('data')
  })
})

describe('item-key field references', () => {
  it('round-trip between the stored and display forms', () => {
    const names = { a: 'Analyst' }
    const stored = 'Steps: {{node:a.recipe[name, op]}}'
    expect(toDisplayPrompt(stored, names)).toBe('Steps: {{Analyst.recipe[name, op]}}')
    expect(toStoredPrompt('Steps: {{Analyst.recipe[name, op]}}', names)).toBe(stored)
  })
})

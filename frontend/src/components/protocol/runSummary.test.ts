import { describe, expect, it } from 'vitest'
import type { Edge, Node } from '@xyflow/react'
import { runAttemptCount, summarizeRun } from './runSummary'

const nodes = [
  { id: 'agent-a', type: 'agent', data: { label: 'Writer' } },
  { id: 'agent-b', type: 'agent', data: { label: 'Reviewer' } },
  { id: 'tool-a', type: 'mcp_tool', data: { label: 'Scores', config: { server_id: 'server-1', server_name: 'Quality server', tool_names: ['score'], enabled: true } } },
] as unknown as Node[]

const edges = [
  { id: 'tool-agent-a', source: 'tool-a', target: 'agent-a', targetHandle: 'tool' },
] as Edge[]

describe('run summary', () => {
  it('reports connected execution resources and the selected replicate count', () => {
    const scope = { type: 'selected-cells', cellCount: 2, replicateCount: 8, pendingReplicateCount: 3, rerunReplicateCount: 2 } as const
    const summary = summarizeRun(nodes, edges, scope)

    expect(runAttemptCount(scope)).toBe(5)
    expect(summary.toolServers).toEqual(['Quality server'])
  })

  it('only includes resources attached to a node-scoped run', () => {
    const summary = summarizeRun(nodes, edges, { type: 'node', nodeId: 'agent-b', label: 'Reviewer' })
    expect(summary.toolServers).toEqual([])
  })
})

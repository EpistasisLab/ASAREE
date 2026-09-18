import { describe, expect, it } from 'vitest'
import { upsertAgentOutputMetric } from './agentOutputMetrics'
import { upsertMcpToolMetric } from './mcpToolMetrics'
import { localMetricReadinessPreview } from './measurementPlan'
import { upsertPythonScriptMetric } from './pythonScriptMetrics'
import type { DesignMetric } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'

const metric: DesignMetric = {
  id: 'quality',
  name: 'Quality',
  kind: 'custom',
  valueType: 'opaque',
  direction: 'neutral',
  aggregation: 'none',
  primary: false,
}

const graph = {
  nodes: [
    { id: 'agent', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Judge' } },
    { id: 'script', type: 'script', position: { x: 0, y: 0 }, data: { label: 'Scorer', config: { code: 'print(1)', enabled: true } } },
    { id: 'mcp', type: 'mcp_tool', position: { x: 0, y: 0 }, data: { label: 'Tools', config: { server_id: 'server', tool_names: ['score'], enabled: true } } },
  ],
  edges: [
    { id: 'script-agent', source: 'script', target: 'agent', targetHandle: 'tool' },
    { id: 'mcp-agent', source: 'mcp', target: 'agent', targetHandle: 'tool' },
  ],
} as unknown as ProtocolGraph

describe('reported custom metric plans', () => {
  it('stores Agent output as an opaque, display-only metric', () => {
    const plan = upsertAgentOutputMetric(null, metric, 'agent')

    expect(plan.metrics[0]).toMatchObject({ value_type: 'opaque', direction: 'neutral', aggregation: 'none', primary: false })
    expect(plan.producers[0]).toMatchObject({ producer_id: 'asaree.agent_output', kind: 'reported', config: { agent_node_id: 'agent' } })
    expect(plan.inputs).toEqual([])
    expect(localMetricReadinessPreview(metric, plan, graph).ready).toBe(true)
  })

  it('stores Script and MCP sources without evaluator inputs or result schemas', () => {
    const scriptPlan = upsertPythonScriptMetric(null, metric, { agentNodeId: 'agent', scriptNodeId: 'script' })
    const mcpPlan = upsertMcpToolMetric(null, metric, {
      key: 'source', agentNodeId: 'agent', mcpNodeId: 'mcp', serverId: 'server', toolNames: ['score'], label: 'Judge → Tools',
    }, 'score')

    expect(scriptPlan.producers[0]).toMatchObject({ kind: 'reported', artifacts: [], config: { agent_node_id: 'agent', script_node_id: 'script' } })
    expect(mcpPlan.producers[0]).toMatchObject({ kind: 'reported', artifacts: [], config: { agent_node_id: 'agent', mcp_node_id: 'mcp', server_id: 'server', tool_name: 'score' } })
    expect(scriptPlan.inputs).toEqual([])
    expect(mcpPlan.inputs).toEqual([])
    expect(localMetricReadinessPreview(metric, scriptPlan, graph).ready).toBe(true)
    expect(localMetricReadinessPreview(metric, mcpPlan, graph).ready).toBe(true)
  })
})

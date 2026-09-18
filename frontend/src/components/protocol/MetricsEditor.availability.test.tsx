import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { experimentsApi } from '@/api/client'
import { METRIC_CATALOG } from '@/lib/metricCatalog'
import type { DesignMetric, MeasurementPlan } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'
import { MetricsEditor } from './MetricsEditor'

function renderEditor(graph: ProtocolGraph) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MetricsEditor
        experimentId="experiment-1"
        metrics={[]}
        measurementPlan={null}
        graph={graph}
        onChange={vi.fn()}
      />
    </QueryClientProvider>,
  )
}

describe('built-in metric canvas availability', () => {
  beforeEach(() => {
    vi.spyOn(experimentsApi, 'getMeasurementCapabilities').mockResolvedValue({
      outputs: { 'asaree.runtime': METRIC_CATALOG.map((entry) => entry.key) },
    })
    vi.spyOn(experimentsApi, 'validateMeasurementPlan').mockResolvedValue({ valid: true, issues: [] })
  })

  it('disables critic and tool metrics when the canvas cannot produce them', async () => {
    const user = userEvent.setup()
    const graph = {
      nodes: [
        { id: 'writer', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Writer', active: true, config: {} } },
        { id: 'empty-tools', type: 'mcp_tool', position: { x: 0, y: 0 }, data: { label: 'Empty tools', config: { server_id: 'server-1', tool_names: [], enabled: true } } },
        { id: 'disabled-script', type: 'script', position: { x: 0, y: 0 }, data: { label: 'Disabled script', config: { code: 'print(1)', enabled: false } } },
      ],
      edges: [
        { id: 'tools-writer', source: 'empty-tools', target: 'writer', targetHandle: 'tool' },
        { id: 'script-writer', source: 'disabled-script', target: 'writer', targetHandle: 'tool' },
      ],
    } as unknown as ProtocolGraph
    renderEditor(graph)

    await user.click(screen.getByRole('button', { name: 'Add metrics' }))
    await waitFor(() => expect(screen.getByText('Built-in metrics loaded.')).toBeInTheDocument())

    for (const name of ['Critic approvals', 'Critic rejections']) {
      const checkbox = screen.getByRole('checkbox', { name: new RegExp(`^${name}`) })
      expect(checkbox).toHaveAttribute('aria-disabled', 'true')
      expect(checkbox).toHaveAccessibleDescription('Add a Critic Gate to the canvas to use this metric.')
    }
    for (const name of ['Tool calls', 'Tool error rate']) {
      const checkbox = screen.getByRole('checkbox', { name: new RegExp(`^${name}`) })
      expect(checkbox).toHaveAttribute('aria-disabled', 'true')
      expect(checkbox).toHaveAccessibleDescription('Connect an enabled, configured tool to an active Agent to use this metric.')
    }
  })

  it('enables critic and tool metrics when valid canvas nodes are present', async () => {
    const user = userEvent.setup()
    const graph = {
      nodes: [
        { id: 'writer', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Writer', active: true, config: {} } },
        { id: 'tools', type: 'mcp_tool', position: { x: 0, y: 0 }, data: { label: 'Tools', config: { server_id: 'server-1', tool_names: ['search'], enabled: true } } },
        { id: 'critic', type: 'critic_gate', position: { x: 0, y: 0 }, data: { label: 'Critic', config: { enabled: true } } },
      ],
      edges: [{ id: 'tools-writer', source: 'tools', target: 'writer', targetHandle: 'tool' }],
    } as unknown as ProtocolGraph
    renderEditor(graph)

    await user.click(screen.getByRole('button', { name: 'Add metrics' }))
    await waitFor(() => expect(screen.getByText('Built-in metrics loaded.')).toBeInTheDocument())

    for (const name of ['Critic approvals', 'Critic rejections', 'Tool calls', 'Tool error rate']) {
      expect(screen.getByRole('checkbox', { name: new RegExp(`^${name}`) })).not.toHaveAttribute('aria-disabled', 'true')
    }
  })

  it('unselects a built-in metric when removing its required canvas node makes it unavailable', async () => {
    const user = userEvent.setup()
    const costMetric: DesignMetric = {
      id: 'metric-cost',
      catalogKey: 'cost_usd',
      name: 'Cost',
      description: 'Estimated provider cost for the run.',
      kind: 'runtime',
      valueType: 'number',
      direction: 'minimize',
      aggregation: 'sum',
      primary: false,
      unit: 'USD',
    }
    const measurementPlan: MeasurementPlan = {
      metrics: [{ id: costMetric.id!, name: costMetric.name, value_type: 'number', direction: 'minimize', aggregation: 'sum', primary: false }],
      producers: [{ id: 'runtime', producer_id: 'asaree.runtime', kind: 'runtime', outputs: { cost_usd: costMetric.id! }, artifacts: [], config: {} }],
      inputs: [],
    }
    const graphWithTool = {
      nodes: [
        { id: 'writer', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Writer', active: true, config: {} } },
        { id: 'tools', type: 'mcp_tool', position: { x: 0, y: 0 }, data: { label: 'Tools', config: { server_id: 'server-1', tool_names: ['search'], enabled: true } } },
      ],
      edges: [{ id: 'tools-writer', source: 'tools', target: 'writer', targetHandle: 'tool' }],
    } as unknown as ProtocolGraph
    const saved = vi.fn()

    function Harness() {
      const [graph, setGraph] = useState<ProtocolGraph>(graphWithTool)
      const [metrics, setMetrics] = useState<DesignMetric[]>([costMetric])
      const [plan, setPlan] = useState<MeasurementPlan | null>(measurementPlan)
      return <>
        <button type="button" onClick={() => setGraph({ nodes: [], edges: [] } as ProtocolGraph)}>Remove tool node</button>
        <MetricsEditor
          experimentId="experiment-1"
          metrics={metrics}
          measurementPlan={plan}
          graph={graph}
          onChange={(nextMetrics, nextPlan) => {
            saved(nextMetrics, nextPlan)
            setMetrics(nextMetrics)
            setPlan(nextPlan)
          }}
        />
      </>
    }

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>)
    await user.click(screen.getByRole('button', { name: 'Manage metrics' }))
    await waitFor(() => expect(screen.getByText('Built-in metrics loaded.')).toBeInTheDocument())

    const toolCalls = screen.getByRole('checkbox', { name: /^Tool calls/ })
    await user.click(toolCalls)
    expect(toolCalls).toBeChecked()
    await waitFor(() => expect(saved).toHaveBeenCalledTimes(1))
    const selectedPlan = saved.mock.lastCall?.[1] as MeasurementPlan | undefined
    expect(selectedPlan?.producers[0]?.outputs).toHaveProperty('tool_calls')

    fireEvent.click(screen.getByRole('button', { name: 'Remove tool node', hidden: true }))

    await waitFor(() => expect(toolCalls).toHaveAttribute('aria-disabled', 'true'))
    expect(toolCalls).not.toBeChecked()
    await waitFor(() => expect(saved).toHaveBeenCalledTimes(2))
    const unselectedPlan = saved.mock.lastCall?.[1] as MeasurementPlan | undefined
    expect(unselectedPlan?.producers[0]?.outputs).not.toHaveProperty('tool_calls')
  })
})

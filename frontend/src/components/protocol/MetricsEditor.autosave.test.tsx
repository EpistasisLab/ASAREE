import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { experimentsApi } from '@/api/client'
import { METRIC_CATALOG } from '@/lib/metricCatalog'
import type { DesignMetric, MeasurementPlan } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'
import { MetricsEditor } from './MetricsEditor'

const EMPTY_GRAPH = { nodes: [], edges: [] } as ProtocolGraph

function renderEditor(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe('MetricsEditor autosave', () => {
  beforeEach(() => {
    vi.spyOn(experimentsApi, 'getMeasurementCapabilities').mockResolvedValue({
      outputs: {
        'asaree.runtime': METRIC_CATALOG.filter((entry) => entry.kind === 'runtime').map((entry) => entry.key),
      },
    })
  })

  it('selects every producible built-in by default and disables contextual metrics without eligible nodes', async () => {
    const user = userEvent.setup()
    renderEditor(<MetricsEditor experimentId="experiment-1" metrics={[]} measurementPlan={null} graph={EMPTY_GRAPH} onChange={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: 'Add metrics' }))
    const dialog = await screen.findByRole('dialog', { name: 'Manage metrics' })
    const contextual = new Set(['tool_calls', 'tool_error_rate', 'critic_approvals', 'critic_rejections'])
    for (const entry of METRIC_CATALOG.filter((candidate) => candidate.kind === 'runtime')) {
      const checkbox = within(dialog).getByRole('checkbox', { name: new RegExp(`^${entry.name}`) })
      if (contextual.has(entry.key)) expect(checkbox).not.toBeChecked()
      else expect(checkbox).toBeChecked()
    }
    expect(within(dialog).getByRole('checkbox', { name: /^Tool calls/ })).toHaveAttribute('aria-disabled', 'true')
    expect(within(dialog).getByRole('checkbox', { name: /^Tool error rate/ })).toHaveAttribute('aria-disabled', 'true')
    expect(within(dialog).getByRole('checkbox', { name: /^Critic approvals/ })).toHaveAttribute('aria-disabled', 'true')
    expect(within(dialog).getByRole('checkbox', { name: /^Critic rejections/ })).toHaveAttribute('aria-disabled', 'true')
  })

  it('enables tool metrics when a tool node is on the canvas', async () => {
    const user = userEvent.setup()
    const graph = {
      nodes: [
        { id: 'agent-1', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Agent', active: true } },
        { id: 'tool-1', type: 'mcp_tool', position: { x: 0, y: 0 }, data: { label: 'Tools', config: { server_id: 'server-1', tool_names: ['search'], enabled: true } } },
      ],
      edges: [{ id: 'tool-agent', source: 'tool-1', target: 'agent-1', targetHandle: 'tool' }],
    } as unknown as ProtocolGraph
    renderEditor(<MetricsEditor experimentId="experiment-1" metrics={[]} measurementPlan={null} graph={graph} onChange={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: 'Add metrics' }))
    const dialog = await screen.findByRole('dialog', { name: 'Manage metrics' })
    expect(within(dialog).getByRole('checkbox', { name: /^Tool calls/ })).not.toHaveAttribute('aria-disabled', 'true')
    expect(within(dialog).getByRole('checkbox', { name: /^Tool error rate/ })).not.toHaveAttribute('aria-disabled', 'true')
  })

  it('flushes changed options on close and restores them when reopened', async () => {
    const user = userEvent.setup()
    const saved = vi.fn()

    function Harness() {
      const [metrics, setMetrics] = useState<DesignMetric[]>([])
      const [plan, setPlan] = useState<MeasurementPlan | null>(null)
      return <MetricsEditor
        experimentId="experiment-1"
        metrics={metrics}
        measurementPlan={plan}
        graph={EMPTY_GRAPH}
        onChange={vi.fn()}
        onApplyMetrics={async (nextMetrics, nextPlan) => {
          saved(nextMetrics, nextPlan)
          setMetrics(nextMetrics)
          setPlan(nextPlan)
        }}
      />
    }

    renderEditor(<Harness />)
    await user.click(screen.getByRole('button', { name: 'Add metrics' }))
    const dialog = await screen.findByRole('dialog', { name: 'Manage metrics' })
    expect(within(dialog).getByRole('group', { name: 'Dialog transparency' })).toBeInTheDocument()
    expect(within(dialog).queryByText('Waiting to save…')).not.toBeInTheDocument()
    expect(within(dialog).queryByText('Saving…')).not.toBeInTheDocument()
    expect(within(dialog).queryByText('Saved')).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('checkbox', { name: /^Cost/ }))
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))

    await waitFor(() => expect(saved).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('dialog', { name: 'Manage metrics' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Manage metrics' }))
    expect(await screen.findByRole('checkbox', { name: /^Cost/ })).not.toBeChecked()
  })
})

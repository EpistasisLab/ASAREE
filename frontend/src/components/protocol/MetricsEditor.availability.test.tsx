import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { experimentsApi } from '@/api/client'
import { METRIC_CATALOG } from '@/lib/metricCatalog'
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
})

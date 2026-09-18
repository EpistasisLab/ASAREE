import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { DesignMetric } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'
import { mcpServersApi } from '@/api/client'
import type { CustomMetricSourceContext } from '@/lib/customMetrics'
import { CustomMetricFlow } from './CustomMetricDialogs'

function renderFlow(graph: ProtocolGraph, onSave = vi.fn(), sourceContext?: CustomMetricSourceContext) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const metric: DesignMetric = {
    id: 'metric-1',
    name: '',
    kind: 'custom',
    valueType: 'number',
    direction: 'neutral',
    aggregation: 'mean',
    primary: false,
  }
  render(
    <QueryClientProvider client={client}>
      <CustomMetricFlow
        metric={metric}
        binding={undefined}
        graph={graph}
        sourceContext={sourceContext}
        existingMetrics={[]}
        onDirtyChange={vi.fn()}
        onSave={onSave}
      />
    </QueryClientProvider>,
  )
  return onSave
}

describe('CustomMetricFlow', () => {
  it('captures an Agent final output as a custom metric source', async () => {
    const user = userEvent.setup()
    const graph = {
      nodes: [{ id: 'agent', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Judge' } }],
      edges: [],
    } as unknown as ProtocolGraph
    const onSave = renderFlow(graph)

    await user.click(screen.getByRole('combobox', { name: 'Metric node' }))
    const option = screen.getByRole('option', { name: 'Judge, Agent' })
    expect(option).toHaveTextContent('Judge')
    expect(within(option).getByText('Agent')).toHaveAttribute('data-slot', 'badge')
    expect(option).not.toHaveTextContent('Judge:Agent')
    await user.click(option)
    await user.type(screen.getByRole('textbox', { name: 'Metric name' }), 'Quality')
    await user.click(screen.getByRole('button', { name: 'Add custom metric' }))

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Quality' }),
      { producer: 'agent', agentNodeId: 'agent' },
    )
  })

  it('captures a Python Script call without evaluator configuration', async () => {
    const user = userEvent.setup()
    const graph = {
      nodes: [
        { id: 'agent', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Writer' } },
        { id: 'script', type: 'script', position: { x: 0, y: 0 }, data: { label: 'Scorer', config: { code: 'print(1)' } } },
        { id: 'dataset', type: 'dataset', position: { x: 0, y: 0 }, data: { label: 'Cases' } },
      ],
      edges: [{ id: 'script-agent', source: 'script', target: 'agent', targetHandle: 'tool' }],
    } as unknown as ProtocolGraph
    const onSave = renderFlow(graph, vi.fn(), { producer: 'python', nodeId: 'script' })

    expect(screen.queryByText('Evaluator')).not.toBeInTheDocument()
    expect(screen.queryByText(/Python evaluators are trusted same-user code/)).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Metric type' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Unit')).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: /Result path/ })).not.toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: 'Metric name' }), 'Quality')
    await user.click(screen.getByRole('button', { name: 'Add custom metric' }))

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Quality' }),
      expect.objectContaining({ producer: 'python', sourceKey: JSON.stringify(['agent', 'script']) }),
    )
  })

  it('allows multiple metrics to capture the same MCP tool result', async () => {
    const user = userEvent.setup()
    vi.spyOn(mcpServersApi, 'list').mockResolvedValue([{
      id: 'server-1', name: 'Quality server', transport: 'stdio', command: 'quality', url: null, status: 'connected', error_message: null,
      capabilities: { tools: [{ name: 'score', input_schema: { properties: {} } }, { name: 'explain', input_schema: { properties: {} } }] }, created_at: '',
    }])
    const graph = {
      nodes: [
        { id: 'agent', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Writer' } },
        { id: 'tool', type: 'mcp_tool', position: { x: 0, y: 0 }, data: { label: 'Quality tools', config: { server_id: 'server-1', tool_names: ['score', 'explain'] } } },
      ],
      edges: [{ id: 'tool-agent', source: 'tool', target: 'agent', targetHandle: 'tool' }],
    } as unknown as ProtocolGraph
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><CustomMetricFlow
      metric={{ id: 'metric-b', name: '', kind: 'custom', valueType: 'number', direction: 'neutral', aggregation: 'mean', primary: false }}
      binding={undefined}
      graph={graph}
      existingMetrics={[]}
      onDirtyChange={vi.fn()}
      onSave={vi.fn()}
    /></QueryClientProvider>)

    await user.click(screen.getByRole('combobox', { name: 'Metric node' }))
    const option = screen.getByRole('option', { name: 'Writer:Quality tools, MCP Tool' })
    expect(within(option).getByText('MCP Tool')).toHaveAttribute('data-slot', 'badge')
    await user.click(option)
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'MCP tool' })).toHaveTextContent('score'))
    await user.click(screen.getByRole('combobox', { name: 'MCP tool' }))
    expect(screen.getByRole('option', { name: 'score' })).not.toHaveAttribute('aria-disabled', 'true')
  })

  it('allows multiple metrics to capture the same Python Script result', async () => {
    const user = userEvent.setup()
    const graph = {
      nodes: [
        { id: 'agent', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Writer' } },
        { id: 'script', type: 'script', position: { x: 0, y: 0 }, data: { label: 'Scorer', config: { code: 'print(1)' } } },
      ],
      edges: [{ id: 'script-agent', source: 'script', target: 'agent', targetHandle: 'tool' }],
    } as unknown as ProtocolGraph
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><CustomMetricFlow
      metric={{ id: 'metric-b', name: '', kind: 'custom', valueType: 'number', direction: 'neutral', aggregation: 'mean', primary: false }}
      binding={undefined}
      graph={graph}
      existingMetrics={[]}
      onDirtyChange={vi.fn()}
      onSave={vi.fn()}
    /></QueryClientProvider>)

    await user.click(screen.getByRole('combobox', { name: 'Metric node' }))
    const option = screen.getByRole('option', { name: 'Writer:Scorer, Script' })
    expect(within(option).getByText('Script')).toHaveAttribute('data-slot', 'badge')
    expect(option).not.toHaveAttribute('aria-disabled', 'true')
  })
})

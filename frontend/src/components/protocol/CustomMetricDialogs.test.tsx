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
  it('groups nodes alphabetically by agent and supports collapsing and search', async () => {
    const user = userEvent.setup()
    const graph = {
      nodes: ['Zebra', 'Alpha'].map(label => ({ id: label, type: 'agent', position: { x: 0, y: 0 }, data: { label } })),
      edges: [],
    } as unknown as ProtocolGraph
    renderFlow(graph)
    await user.click(screen.getByRole('combobox', { name: 'Metric node' }))
    const list = screen.getByRole('listbox', { name: 'Metric nodes' })
    const summaries = [...list.querySelectorAll('summary')]
    expect(summaries.map(summary => summary.textContent)).toEqual(['Alpha', 'Runtime metrics for selected nodes', 'Zebra'])
    expect(summaries.every(summary => !summary.parentElement?.hasAttribute('open'))).toBe(true)
    await user.click(summaries[0])
    expect(summaries[0].parentElement).toHaveAttribute('open')
    await user.click(summaries[0])
    expect(summaries[0].parentElement).not.toHaveAttribute('open')
    await user.type(screen.getByRole('textbox', { name: 'Search metric nodes' }), 'Alpha')
    expect(screen.queryByRole('option', { name: 'Zebra, Agent' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: 'Alpha, Agent' }))
    expect(screen.queryByRole('textbox', { name: 'Search metric nodes' })).not.toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Metric node' })).toHaveTextContent('Alpha')
  })

  it('sorts categories and node names alphabetically within each agent', async () => {
    const user = userEvent.setup()
    const graph = {
      nodes: [
        { id: 'agent', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Writer' } },
        ...['Zulu', 'Alpha'].map(label => ({ id: label, type: 'script', position: { x: 0, y: 0 }, data: { label, config: { code: 'print(1)' } } })),
        { id: 'tool', type: 'mcp_tool', position: { x: 0, y: 0 }, data: { label: 'Quality', config: { server_id: 'server-1', tool_names: ['score'] } } },
      ],
      edges: ['Zulu', 'Alpha', 'tool'].map(id => ({ id, source: id, target: 'agent', targetHandle: 'tool' })),
    } as unknown as ProtocolGraph
    renderFlow(graph)
    await user.click(screen.getByRole('combobox', { name: 'Metric node' }))
    const list = screen.getByRole('listbox', { name: 'Metric nodes' })
    const agentGroup = [...list.querySelectorAll('details')].find(group => group.querySelector('summary')?.textContent === 'Writer')!
    expect(agentGroup).toHaveAttribute('open')
    expect([...agentGroup.querySelectorAll('h3')].map(heading => heading.textContent)).toEqual(['Agent', 'Tools'])
    const toolsGroup = within(agentGroup).getByRole('region', { name: 'Writer: Tools' })
    expect([...toolsGroup.querySelectorAll('h4')].map(heading => heading.textContent)).toEqual(['MCP Tools', 'Scripts'])
    expect(toolsGroup.querySelector('h4')).toHaveClass('text-sm', 'text-chart-2')
    expect(within(toolsGroup).getAllByRole('option').every(option => option.parentElement?.classList.contains('ml-3'))).toBe(true)
    expect(within(agentGroup).getAllByRole('option').map(option => option.getAttribute('aria-label')?.split(', ').slice(0, 2).join(', '))).toEqual(['Writer, Agent', 'Writer:Quality, MCP Tool', 'Writer:Alpha, Script', 'Writer:Zulu, Script'])
  })

  it('sums a runtime measure over several chosen nodes', async () => {
    const user = userEvent.setup()
    const graph = {
      nodes: [
        { id: 'agent-dc', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'DC' } },
        { id: 'gate-dc', type: 'critic_gate', position: { x: 0, y: 0 }, data: { label: 'Critic (DC)' } },
        { id: 'gate-fs', type: 'critic_gate', position: { x: 0, y: 0 }, data: { label: 'Critic (FS)' } },
      ],
      edges: [],
    } as unknown as ProtocolGraph
    const onSave = renderFlow(graph)

    await user.click(screen.getByRole('combobox', { name: 'Metric node' }))
    await user.click(screen.getByRole('option', { name: 'Runtime metrics for selected nodes, Node runtime' }))
    expect(screen.getByRole('button', { name: 'Add custom metric' })).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: /^Critic \(FS\)/ }))
    await user.click(screen.getByRole('checkbox', { name: /^Critic \(DC\)/ }))
    await user.click(screen.getByRole('combobox', { name: 'Runtime measure' }))
    await user.click(screen.getByRole('option', { name: /^Turns/ }))

    expect(screen.getByRole('textbox', { name: 'Metric name' })).toHaveValue('Turns · Critic (DC) + Critic (FS)')
    await user.click(screen.getByRole('button', { name: 'Add custom metric' }))
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Turns · Critic (DC) + Critic (FS)' }),
      { producer: 'node_runtime', nodeIds: ['gate-dc', 'gate-fs'], output: 'agent_loop_iterations' },
    )
  })

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
      capabilities: { tools: [{ name: 'score', input_schema: { properties: {} } }, { name: 'explain', input_schema: { properties: {} } }] },
      authentication: { auth_mode: 'none', configured: false, authorization_required: false, static_headers_configured: false, stdio_env_configured: false, stdio_env_names: [] },
      credential_management_allowed: true,
      created_at: '',
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
      metric={{ id: 'metric-b', name: '', kind: 'custom' }}
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
      metric={{ id: 'metric-b', name: '', kind: 'custom' }}
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

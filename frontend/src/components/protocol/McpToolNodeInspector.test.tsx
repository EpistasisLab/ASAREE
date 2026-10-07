import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import type { McpToolNodeData, ProtocolNode } from '@/types/protocols'
import { McpToolNodeInspector } from './McpToolNodeInspector'

it('shows the moved factor message and connector-controlled availability', () => {
  const client = new QueryClient()
  client.setQueryData(['mcp-servers'], [])
  const node: ProtocolNode & { data: McpToolNodeData } = {
    id: 'tool', type: 'mcp_tool', position: { x: 0, y: 0 },
    data: { label: 'Search', config: { server_id: 's', server_name: 'Search', tool_names: ['search'], enabled: false } },
  }
  render(<QueryClientProvider client={client}><McpToolNodeInspector node={node} connectorFactorName="Tools" onChange={vi.fn()} onDelete={vi.fn()} onClose={vi.fn()} /></QueryClientProvider>)
  expect(screen.getByText(/has moved to the node toolbar/)).toHaveTextContent('Hover over the Tool node')
  expect(screen.getByText(/Availability is controlled/)).toHaveTextContent('Tools')
  expect(screen.getByRole('switch')).toHaveAttribute('aria-disabled', 'true')
  expect(screen.getByRole('switch')).toBeChecked()
  client.clear()
})

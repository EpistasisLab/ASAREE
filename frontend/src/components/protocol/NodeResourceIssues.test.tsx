import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { datasetsApi, mcpServersApi, packageImportsApi, protocolsApi } from '@/api/client'
import type { ImportReadiness, PackageImport } from '@/types/packageImports'
import type { Protocol } from '@/types/protocols'
import type { Node } from '@xyflow/react'
import { NodeInspectorResourceIssues, NodeResourceIssuesProvider } from './NodeResourceIssues'
import { withResourceWarnings } from './nodeResourceIssuesContext'
import { protocolGraphQueryKey } from '@/lib/protocolGraph'

afterEach(() => vi.restoreAllMocks())
const ResourceNode = withResourceWarnings(({ id }: { id: string }) => <span>{id}</span>)
function show(node: Node) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}>
    <NodeResourceIssuesProvider experimentId="e" protocolId="p" selectedNodeId={node.id}
      nodes={[node]} onGraphChange={vi.fn()}>
      <ResourceNode id={node.id} />
      <NodeInspectorResourceIssues />
    </NodeResourceIssuesProvider>
  </QueryClientProvider>)
  return client
}

it('uses ordinary registry validation for legacy JSON nodes and clears warnings after repair', async () => {
  vi.spyOn(packageImportsApi, 'list').mockResolvedValue([])
  vi.spyOn(datasetsApi, 'list').mockResolvedValue([])
  const client = show({ id: 'dataset', type: 'dataset', position: { x: 0, y: 0 },
    data: { config: { dataset_id: 'missing' } } })
  expect(await screen.findByText('Dataset is no longer registered')).toBeInTheDocument()
  act(() => client.setQueryData(['datasets'], [{ id: 'missing', raw_path: '/destination/raw.csv' }]))
  await waitFor(() => expect(screen.queryByText('Dataset is no longer registered')).not.toBeInTheDocument())
})

it('leaves a custom connection unselected and repairs it only in its node inspector', async () => {
  vi.spyOn(packageImportsApi, 'list').mockResolvedValue([{ id: 'import', status: 'completed' } as PackageImport])
  vi.spyOn(mcpServersApi, 'list').mockResolvedValue([])
  const readiness: ImportReadiness = { ready: false,
    issues: [{ node_id: 'tool', resource_id: 'source-tool', message: 'Choose a compatible connection' }],
    mcp: [{ source_id: 'source-tool', name: 'Research tools', selected_id: null,
      candidates: [{ id: 'destination', name: 'My tools' }] }] }
  vi.spyOn(packageImportsApi, 'readiness').mockResolvedValue(readiness)
  const select = vi.spyOn(packageImportsApi, 'selectConnection').mockResolvedValue({ ...readiness, ready: true, issues: [] })
  const update = vi.spyOn(protocolsApi, 'update').mockResolvedValue({} as Protocol)
  vi.spyOn(protocolsApi, 'get').mockResolvedValue({ graph: { nodes: [], edges: [] } } as unknown as Protocol)
  const node = { id: 'tool', type: 'mcp_tool', position: { x: 0, y: 0 },
    data: { config: { server_id: 'placeholder', tool_names: ['score'] } } }
  const client = show(node)
  client.setQueryData(protocolGraphQueryKey('p'), { nodes: [node], edges: [] })
  const picker = await screen.findByRole('combobox')
  expect(picker).toHaveValue('')
  expect(select).not.toHaveBeenCalled()
  fireEvent.change(picker, { target: { value: 'destination' } })
  await waitFor(() => expect(select).toHaveBeenCalledWith('import', 'mcp', 'source-tool', 'destination'))
  expect(update).toHaveBeenCalledWith('p', { graph: expect.objectContaining({ nodes: [node] }) })
})

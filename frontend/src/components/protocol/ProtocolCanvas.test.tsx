import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactFlowProvider } from '@xyflow/react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { experimentsApi, protocolsApi } from '@/api/client'
import { protocolGraphQueryKey } from '@/lib/protocolGraph'
import { defaultAgentNodeData, defaultScriptNodeData, type ProtocolGraph } from '@/types/protocols'
import { ProtocolCanvas } from './ProtocolCanvas'

vi.mock('./PythonCodeEditor', () => ({
  PythonCodeEditor: ({ value }: { value: string }) => <textarea aria-label="Python code" value={value} readOnly />,
}))

function renderCanvas(graph: ProtocolGraph, experimentId: string | null = null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ReactFlowProvider>
          <div style={{ width: 1000, height: 800 }}>
            <ProtocolCanvas
              protocolId="protocol-1"
              experimentId={experimentId}
              initialGraph={graph}
              hasUnpublishedChanges={false}
              publishedRevision={null}
            />
          </div>
        </ReactFlowProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { client, ...result }
}

describe('ProtocolCanvas connector adds', () => {
  afterEach(() => vi.unstubAllGlobals())

  beforeEach(() => {
    vi.stubGlobal('DOMMatrixReadOnly', class {
      m22 = 1
    })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 1000,
      bottom: 800,
      width: 1000,
      height: 800,
      toJSON: () => ({}),
    })
    vi.stubGlobal('ResizeObserver', class {
      private callback: ResizeObserverCallback
      constructor(callback: ResizeObserverCallback) {
        this.callback = callback
      }
      observe(target: Element) {
        this.callback([{ target, contentRect: target.getBoundingClientRect() } as ResizeObserverEntry], this as unknown as ResizeObserver)
      }
      unobserve() {}
      disconnect() {}
    })
    vi.spyOn(protocolsApi, 'listRuns').mockResolvedValue([])
    vi.spyOn(protocolsApi, 'update').mockImplementation(async (_id, { graph }) => ({
      id: 'protocol-1',
      name: 'Protocol',
      description: null,
      experiment_id: null,
      graph: graph!,
      published_revision_id: null,
      published_revision: null,
      has_unpublished_changes: true,
      created_at: '',
      updated_at: '',
    }))
  })

  it('keeps an output parser wired after adding it from the agent inspector and closing its inspector', async () => {
    const user = userEvent.setup()
    const agentData = defaultAgentNodeData('Writer')
    agentData.config.require_output_parser = true
    const { client } = renderCanvas({
      nodes: [{ id: 'agent-1', type: 'agent', position: { x: 100, y: 100 }, data: agentData }],
      edges: [],
    })

    fireEvent.doubleClick(await screen.findByText('Writer'))
    await user.click(await screen.findByRole('button', { name: 'Connect an Output Parser' }))
    await user.click(await screen.findByRole('button', { name: /^Output Parser Defines/ }))
    await user.click(await screen.findByRole('button', { name: 'Close' }))

    await waitFor(() => {
      const graph = client.getQueryData<ProtocolGraph>(protocolGraphQueryKey('protocol-1'))
      const parser = graph?.nodes.find((node) => node.type === 'output_parser')
      expect(parser).toBeDefined()
      expect(graph?.edges).toContainEqual(expect.objectContaining({
        source: parser!.id,
        sourceHandle: 'output_parser',
        target: 'agent-1',
        targetHandle: 'output_parser',
      }))
    })

    const parserHandle = document.querySelector('[data-nodeid="agent-1"][data-handleid="output_parser"]')
    expect(parserHandle).toBeInTheDocument()
    expect(parserHandle).not.toHaveClass('!opacity-0')
  })

  it('keeps the parser endpoint registered while its unused affordance is hidden', async () => {
    const agentData = defaultAgentNodeData('Writer')
    renderCanvas({
      nodes: [{ id: 'agent-1', type: 'agent', position: { x: 100, y: 100 }, data: agentData }],
      edges: [],
    })

    const parserHandle = await waitFor(() => {
      const handle = document.querySelector('[data-nodeid="agent-1"][data-handleid="output_parser"]')
      expect(handle).toBeInTheDocument()
      return handle
    })
    expect(parserHandle).toHaveClass('!pointer-events-none', '!opacity-0')
    expect(screen.queryByText('Parser')).not.toBeInTheDocument()
  })

  it('adds a connector-only Sub-Agent from an Agent', async () => {
    const user = userEvent.setup()
    const { client } = renderCanvas({
      nodes: [{ id: 'agent-1', type: 'agent', position: { x: 100, y: 100 }, data: defaultAgentNodeData('Planner') }],
      edges: [],
    })

    fireEvent.click(await screen.findByTitle('Add Sub-Agents'))
    await user.click(await screen.findByRole('button', { name: /^Sub-Agent A delegated worker/ }))

    await waitFor(() => {
      const graph = client.getQueryData<ProtocolGraph>(protocolGraphQueryKey('protocol-1'))
      const child = graph?.nodes.find((node) => node.type === 'sub_agent')
      const patternEdge = graph?.edges.find(
        (edge) => edge.target === child?.id && edge.targetHandle === 'architectural_pattern',
      )
      const pattern = graph?.nodes.find((node) => node.id === patternEdge?.source)
      expect(child).toBeDefined()
      expect(graph?.edges).toContainEqual(expect.objectContaining({
        source: child!.id,
        sourceHandle: 'sub_agents',
        target: 'agent-1',
        targetHandle: 'sub_agents',
      }))
      expect(child!.position.x).toBe(100)
      expect(child!.position.y).toBeGreaterThanOrEqual(400)
      expect(pattern).toBeDefined()
      expect(pattern!.position.x).toBeGreaterThanOrEqual(child!.position.x - 50)
      expect(pattern!.position.x).toBeLessThanOrEqual(child!.position.x + 50)
      expect(pattern!.position.y).toBeGreaterThan(100)
      expect(pattern!.position.y).toBeLessThan(child!.position.y)
    })

    expect(screen.getByText('Parent')).toBeInTheDocument()
    expect(screen.getAllByText('Sub-Agent').length).toBeGreaterThan(0)
    expect(screen.queryByText('Input')).not.toBeInTheDocument()
    expect(screen.getByText('Output')).toBeInTheDocument()
    expect(screen.queryByText('Nothing downstream — this is the final output.')).not.toBeInTheDocument()
  })

  it('does not expose custom metric controls in the Script inspector', async () => {
    const scriptData = defaultScriptNodeData('Score script')
    scriptData.config.code = 'def evaluate(output): return 1'
    vi.spyOn(experimentsApi, 'get').mockResolvedValue({
      id: 'experiment-1',
      name: 'Experiment',
      description: null,
      hypothesis: null,
      design_type: 'factorial',
      task_brief: null,
      design_spec: {
        factors: [],
        metrics: [{ id: 'metric-1', name: 'Clarity', kind: 'custom', valueType: 'number', primary: false, direction: 'maximize', aggregation: 'mean' }],
      },
      measurement_plan: {
        metrics: [{ id: 'metric-1', name: 'Clarity', value_type: 'number', direction: 'maximize', aggregation: 'mean', primary: false }],
        producers: [{
          id: 'python-metric-1',
          producer_id: 'asaree.python_script',
          kind: 'reported',
          outputs: { value: 'metric-1' },
          artifacts: ['details'],
          config: { agent_node_id: 'agent-1', script_node_id: 'script-1' },
        }],
        inputs: [],
      },
      dataset_ids: [],
      dataset_id: null,
      locked_at: null,
      locked_protocol_revision_id: null,
      locked_design_spec: null,
      locked_measurement_plan: null,
      created_at: '',
      updated_at: '',
      archived_at: null,
    })
    renderCanvas({
      nodes: [
        { id: 'agent-1', type: 'agent', position: { x: 100, y: 100 }, data: defaultAgentNodeData('Writer') },
        { id: 'script-1', type: 'script', position: { x: 20, y: 100 }, data: scriptData },
      ],
      edges: [{ id: 'edge-1', source: 'script-1', sourceHandle: 'tool', target: 'agent-1', targetHandle: 'tool' }],
    }, 'experiment-1')

    expect(await screen.findByTitle('1 metric produced by this node')).toBeInTheDocument()
    fireEvent.doubleClick(await screen.findByText('Score script'))
    expect(screen.queryByText('Custom metric evaluator')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Metric: Clarity' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Create (another )?metric/ })).not.toBeInTheDocument()
  })

  it('opens a bound factor from the Script inspector', async () => {
    const user = userEvent.setup()
    const scriptData = defaultScriptNodeData('Score script')
    scriptData.factor_bindings = { config: 'Scoring variants' }
    vi.spyOn(experimentsApi, 'get').mockResolvedValue({
      id: 'experiment-1', name: 'Experiment', description: null, hypothesis: null, design_type: 'factorial', task_brief: null,
      design_spec: {
        factors: [{ name: 'Scoring variants', levels: [scriptData.config], level_type: 'script_config' }],
        metrics: [],
      },
      measurement_plan: null, dataset_ids: [], dataset_id: null, locked_at: null,
      locked_protocol_revision_id: null, locked_design_spec: null, locked_measurement_plan: null,
      created_at: '', updated_at: '', archived_at: null,
    })
    renderCanvas({
      nodes: [{ id: 'script-1', type: 'script', position: { x: 20, y: 100 }, data: scriptData }],
      edges: [],
    }, 'experiment-1')

    fireEvent.doubleClick(await screen.findByText('Score script'))
    const factorLink = await screen.findByText('Factor: Scoring variants')
    expect(factorLink.closest('button')).not.toBeNull()
    await user.click(factorLink)

    expect(await screen.findByRole('heading', { name: 'Scoring variants' })).toBeInTheDocument()
    expect(screen.getByText('Factor name')).toBeInTheDocument()
  })

  it('binds the Agent inspector header action directly to Active', async () => {
    const user = userEvent.setup()
    vi.spyOn(experimentsApi, 'get').mockResolvedValue({
      id: 'experiment-1', name: 'Experiment', description: null, hypothesis: null, design_type: 'factorial', task_brief: null,
      design_spec: { factors: [], metrics: [] }, measurement_plan: null, dataset_ids: [], dataset_id: null,
      locked_at: null, locked_protocol_revision_id: null, locked_design_spec: null, locked_measurement_plan: null,
      created_at: '', updated_at: '', archived_at: null,
    })
    renderCanvas({
      nodes: [{ id: 'agent-1', type: 'agent', position: { x: 100, y: 100 }, data: defaultAgentNodeData('Writer') }],
      edges: [],
    }, 'experiment-1')

    fireEvent.doubleClick(await screen.findByText('Writer'))
    await user.click(screen.getAllByRole('button', { name: 'Make experimental factor' })[0])

    expect(await screen.findByText('Writer:Active')).toBeInTheDocument()
    expect(screen.getByText('Levels: true, false')).toBeInTheDocument()
    expect(screen.queryByText('Bind to a field on the canvas')).not.toBeInTheDocument()
  })

})

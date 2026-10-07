import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactFlowProvider } from '@xyflow/react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { datasetsApi, experimentsApi, mcpServersApi, protocolsApi, skillsApi } from '@/api/client'
import { protocolGraphQueryKey } from '@/lib/protocolGraph'
import type { Experiment } from '@/types/experiments'
import {
  defaultAgentNodeData,
  defaultAnthropicModelNodeData,
  defaultCriticGateNodeData,
  defaultDatasetNodeData,
  defaultMemoryNodeData,
  defaultOutputParserNodeData,
  defaultReasonActPatternNodeData,
  defaultScriptNodeData,
  type ProtocolGraph,
  type ProtocolRun,
  type TestRun,
} from '@/types/protocols'
import { ProtocolCanvas } from './ProtocolCanvas'

vi.mock('./PythonCodeEditor', () => ({
  PythonCodeEditor: ({ value }: { value: string }) => <textarea aria-label="Python code" value={value} readOnly />,
}))

function renderCanvas(graph: ProtocolGraph, experimentId: string | null = null, publishedRevision: number | null = null) {
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
              hasUnpublishedChanges={publishedRevision !== null}
              publishedRevision={publishedRevision}
            />
          </div>
        </ReactFlowProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { client, ...result }
}

function protocolRun(overrides: Partial<ProtocolRun> = {}): ProtocolRun {
  return {
    id: 'production-run',
    protocol_id: 'protocol-1',
    status: 'running',
    node_runs: {},
    conversation: null,
    error: null,
    replicate_label: 'replicate-1',
    replicate_result_id: 'replicate-result-1',
    factor_values: {},
    design_revision_id: 'design-revision-1',
    protocol_revision_id: 'protocol-revision-1',
    target_node_id: null,
    cancel_requested_at: null,
    created_at: '2026-10-01T12:00:00Z',
    updated_at: '2026-10-01T12:00:00Z',
    observations: [],
    artifacts: [],
    ...overrides,
  }
}

function testRun(overrides: Partial<TestRun> = {}): TestRun {
  return {
    id: 'test-run',
    protocol_id: 'protocol-1',
    status: 'completed',
    error: null,
    protocol_revision_id: 'protocol-revision-1',
    created_at: '2026-09-30T12:00:00Z',
    updated_at: '2026-09-30T12:01:00Z',
    observations: [],
    artifacts: [],
    conversation: null,
    tested_published_revision: null,
    freshness: { out_of_date: false, reasons: [] },
    resources: {
      task: { duration_seconds: null, cost_usd: null },
      evaluation: { duration_seconds: null, cost_usd: null },
      total: { duration_seconds: null, cost_usd: null },
    },
    execution_summary: {
      node_runs: {},
      started_at: null,
      completed_at: '2026-09-30T12:01:00Z',
      cancel_requested_at: null,
    },
    ...overrides,
  }
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

  it.each(['sub_agent_selection', 'sub_agent_toggle'] as const)('creates a %s Sub-Agent factor from the node hover menu and preserves connected nodes', async (mode) => {
    const experiment = {
      id: 'experiment-1', name: 'Experiment', description: null, hypothesis: null, design_type: 'factorial', task_brief: null,
      design_spec: { factors: [], metrics: [] }, measurement_plan: null, dataset_ids: [], dataset_id: null,
      locked_at: null, locked_protocol_revision_id: null, locked_design_spec: null, locked_measurement_plan: null,
      created_at: '', updated_at: '', archived_at: null,
    }
    vi.spyOn(experimentsApi, 'get').mockResolvedValue(experiment)
    const save = vi.spyOn(experimentsApi, 'update').mockResolvedValue(experiment)
    vi.spyOn(skillsApi, 'list').mockResolvedValue([])
    const graph: ProtocolGraph = {
      nodes: [
        { id: 'agent', type: 'agent', position: { x: 100, y: 200 }, data: defaultAgentNodeData('Writer') },
        ...['a', 'b'].map((id) => ({ id, type: 'sub_agent', position: { x: 100, y: 0 }, data: { ...defaultAgentNodeData(`Worker ${id}`), active: false } })),
      ],
      edges: ['a', 'b'].map((id) => ({ id, source: id, target: 'agent', targetHandle: 'sub_agents' })),
    }
    const { client } = renderCanvas(graph, 'experiment-1')
    const makeFactor = document.querySelector<HTMLButtonElement>('[data-testid="rf__node-a"] button[aria-label="Make experimental factor"]')
    expect(makeFactor).not.toBeNull()
    fireEvent.click(makeFactor!)
    await screen.findByRole('dialog')
    fireEvent.click(screen.getByRole('button', { name: mode === 'sub_agent_toggle' ? 'All agent sub-agents on/off' : 'Sub-Agent levels' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
    await waitFor(() => expect(save).toHaveBeenCalledWith('experiment-1', expect.objectContaining({ design_spec: expect.objectContaining({ factors: [{ name: 'Writer:Sub-Agents', level_type: mode, levels: mode === 'sub_agent_toggle' ? [['a', 'b'], []] : [['a'], ['b']], level_labels: mode === 'sub_agent_toggle' ? ['All enabled', 'All disabled'] : ['Worker a', 'Worker b'] }] }) })))
    await waitFor(() => {
      const persisted = client.getQueryData<ProtocolGraph>(protocolGraphQueryKey('protocol-1'))
      expect(persisted?.nodes.find((node) => node.id === 'agent')?.data.factor_bindings).toEqual({ sub_agent_selection: 'Writer:Sub-Agents' })
      expect(persisted?.nodes.find((node) => node.id === 'agent')?.data.sub_agent_factor_mode).toBe(mode)
      expect(persisted?.nodes.filter((node) => node.type === 'sub_agent')).toHaveLength(2)
    })
  })

  it.each(['skill_selection', 'skill_toggle'] as const)('creates a %s skill factor from the node hover menu and preserves connected nodes', async (mode) => {
    const experiment = {
      id: 'experiment-1', name: 'Experiment', description: null, hypothesis: null, design_type: 'factorial', task_brief: null,
      design_spec: { factors: [], metrics: [] }, measurement_plan: null, dataset_ids: [], dataset_id: null,
      locked_at: null, locked_protocol_revision_id: null, locked_design_spec: null, locked_measurement_plan: null,
      created_at: '', updated_at: '', archived_at: null,
    }
    vi.spyOn(experimentsApi, 'get').mockResolvedValue(experiment)
    const save = vi.spyOn(experimentsApi, 'update').mockResolvedValue(experiment)
    vi.spyOn(skillsApi, 'list').mockResolvedValue([])
    const graph: ProtocolGraph = {
      nodes: [
        { id: 'agent', type: 'agent', position: { x: 100, y: 200 }, data: defaultAgentNodeData('Writer') },
        ...['a', 'b'].map((id) => ({ id, type: 'skill', position: { x: 100, y: 0 }, data: { label: `Skill ${id}`, config: { skill_id: id, skill_name: `Skill ${id}` } } })),
      ],
      edges: ['a', 'b'].map((id) => ({ id, source: id, target: 'agent', targetHandle: 'skill' })),
    }
    const { client } = renderCanvas(graph, 'experiment-1')
    const makeFactor = document.querySelector<HTMLButtonElement>('[data-testid="rf__node-a"] button[aria-label="Make experimental factor"]')
    expect(makeFactor).not.toBeNull()
    fireEvent.click(makeFactor!)
    await screen.findByRole('dialog')
    fireEvent.click(screen.getByRole('button', { name: mode === 'skill_toggle' ? 'All skills on/off' : 'Skill levels' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
    await waitFor(() => expect(save).toHaveBeenCalledWith('experiment-1', expect.objectContaining({ design_spec: expect.objectContaining({ factors: [{ name: 'Writer:Skills', level_type: mode, levels: mode === 'skill_toggle' ? [['a', 'b'], []] : [['a'], ['b']], level_labels: mode === 'skill_toggle' ? ['All enabled', 'All disabled'] : ['Skill a', 'Skill b'] }] }) })))
    await waitFor(() => {
      const persisted = client.getQueryData<ProtocolGraph>(protocolGraphQueryKey('protocol-1'))
      expect(persisted?.nodes.find((node) => node.id === 'agent')?.data.factor_bindings).toEqual({ skill_selection: 'Writer:Skills' })
      expect(persisted?.nodes.find((node) => node.id === 'agent')?.data.skill_factor_mode).toBe(mode)
      expect(persisted?.nodes.filter((node) => node.type === 'skill')).toHaveLength(2)
    })
  })

  it.each(['tool_selection', 'tool_toggle'] as const)('creates a %s tool factor from the node hover menu and preserves connected nodes', async (mode) => {
    const experiment = {
      id: 'experiment-1', name: 'Experiment', description: null, hypothesis: null, design_type: 'factorial', task_brief: null,
      design_spec: { factors: [], metrics: [] }, measurement_plan: null, dataset_ids: [], dataset_id: null,
      locked_at: null, locked_protocol_revision_id: null, locked_design_spec: null, locked_measurement_plan: null,
      created_at: '', updated_at: '', archived_at: null,
    }
    vi.spyOn(experimentsApi, 'get').mockResolvedValue(experiment)
    const save = vi.spyOn(experimentsApi, 'update').mockResolvedValue(experiment)
    vi.spyOn(mcpServersApi, 'list').mockResolvedValue([])
    const graph: ProtocolGraph = {
      nodes: [
        { id: 'agent', type: 'agent', position: { x: 100, y: 200 }, data: defaultAgentNodeData('Writer') },
        ...['a', 'b'].map((id) => ({ id, type: 'mcp_tool', position: { x: 100, y: 0 }, data: { label: `Tool ${id}`, config: { server_id: id, server_name: `Server ${id}`, tool_names: ['run'] } } })),
      ],
      edges: ['a', 'b'].map((id) => ({ id, source: id, target: 'agent', targetHandle: 'tool' })),
    }
    const { client } = renderCanvas(graph, 'experiment-1')
    const makeFactor = document.querySelector<HTMLButtonElement>('[data-testid="rf__node-a"] button[aria-label="Make experimental factor"]')
    expect(makeFactor).not.toBeNull()
    fireEvent.click(makeFactor!)
    await screen.findByRole('dialog')
    fireEvent.click(screen.getByRole('button', { name: mode === 'tool_toggle' ? 'All agent tools on/off' : 'Tool levels' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
    await waitFor(() => expect(save).toHaveBeenCalledWith('experiment-1', expect.objectContaining({ design_spec: expect.objectContaining({ factors: [{ name: 'Writer:Tools', level_type: mode, levels: mode === 'tool_toggle' ? [['a', 'b'], []] : [['a'], ['b']], level_labels: mode === 'tool_toggle' ? ['All enabled', 'All disabled'] : ['Tool a', 'Tool b'] }] }) })))
    await waitFor(() => {
      const persisted = client.getQueryData<ProtocolGraph>(protocolGraphQueryKey('protocol-1'))
      expect(persisted?.nodes.find((node) => node.id === 'agent')?.data.factor_bindings).toEqual({ tool_selection: 'Writer:Tools' })
      expect(persisted?.nodes.find((node) => node.id === 'agent')?.data.tool_factor_mode).toBe(mode)
      expect(persisted?.nodes.filter((node) => node.type === 'mcp_tool')).toHaveLength(2)
    })
  })

  it.each(['script_selection', 'script_toggle'] as const)('creates a %s script factor from the node hover menu and preserves connected nodes', async (mode) => {
    const experiment = {
      id: 'experiment-1', name: 'Experiment', description: null, hypothesis: null, design_type: 'factorial', task_brief: null,
      design_spec: { factors: [], metrics: [] }, measurement_plan: null, dataset_ids: [], dataset_id: null,
      locked_at: null, locked_protocol_revision_id: null, locked_design_spec: null, locked_measurement_plan: null,
      created_at: '', updated_at: '', archived_at: null,
    }
    vi.spyOn(experimentsApi, 'get').mockResolvedValue(experiment)
    const save = vi.spyOn(experimentsApi, 'update').mockResolvedValue(experiment)
    const graph: ProtocolGraph = {
      nodes: [
        { id: 'agent', type: 'agent', position: { x: 100, y: 200 }, data: defaultAgentNodeData('Writer') },
        ...['a', 'b'].map((id) => ({ id, type: 'script', position: { x: 100, y: 0 }, data: { label: `Script ${id}`, config: { name: `Script ${id}`, language: 'python', code: 'print(1)'  } } })),
      ],
      edges: ['a', 'b'].map((id) => ({ id, source: id, target: 'agent', targetHandle: 'tool' })),
    }
    const { client } = renderCanvas(graph, 'experiment-1')
    const makeFactor = document.querySelector<HTMLButtonElement>('[data-testid="rf__node-a"] button[aria-label="Make experimental factor"]')
    expect(makeFactor).not.toBeNull()
    fireEvent.click(makeFactor!)
    await screen.findByRole('dialog')
    fireEvent.click(screen.getByRole('button', { name: mode === 'script_toggle' ? 'All agent scripts on/off' : 'Script levels' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
    await waitFor(() => expect(save).toHaveBeenCalledWith('experiment-1', expect.objectContaining({ design_spec: expect.objectContaining({ factors: [{ name: 'Writer:Scripts', level_type: mode, levels: mode === 'script_toggle' ? [['a', 'b'], []] : [['a'], ['b']], level_labels: mode === 'script_toggle' ? ['All enabled', 'All disabled'] : ['Script a', 'Script b'] }] }) })))
    await waitFor(() => {
      const persisted = client.getQueryData<ProtocolGraph>(protocolGraphQueryKey('protocol-1'))
      expect(persisted?.nodes.find((node) => node.id === 'agent')?.data.factor_bindings).toEqual({ script_selection: 'Writer:Scripts' })
      expect(persisted?.nodes.find((node) => node.id === 'agent')?.data.script_factor_mode).toBe(mode)
      expect(persisted?.nodes.filter((node) => node.type === 'script')).toHaveLength(2)
    })
  })

  it('creates and removes a whole-pattern factor from the Pattern toolbar, clearing the Agent override', async () => {
    let experiment: Experiment = {
      id: 'experiment-1', name: 'Experiment', description: null, hypothesis: null, design_type: 'factorial', task_brief: null,
      design_spec: { factors: [], metrics: [] }, measurement_plan: null, dataset_ids: [], dataset_id: null,
      locked_at: null, locked_protocol_revision_id: null, locked_design_spec: null, locked_measurement_plan: null,
      created_at: '', updated_at: '', archived_at: null,
    }
    vi.spyOn(experimentsApi, 'get').mockImplementation(async () => experiment)
    const save = vi.spyOn(experimentsApi, 'update').mockImplementation(async (_id, patch) => {
      experiment = { ...experiment, ...patch }
      return experiment
    })
    const graph: ProtocolGraph = {
      nodes: [
        { id: 'agent', type: 'agent', position: { x: 100, y: 200 }, data: defaultAgentNodeData('Writer') },
        { id: 'pattern', type: 'pattern_reason_act', position: { x: 100, y: 0 }, data: defaultReasonActPatternNodeData() },
      ],
      edges: [{ id: 'pattern-edge', source: 'pattern', target: 'agent', targetHandle: 'architectural_pattern' }],
    }
    const { client } = renderCanvas(graph, 'experiment-1')
    const toolbar = () => document.querySelector<HTMLButtonElement>('[data-testid="rf__node-pattern"] button[aria-label="Make experimental factor"]')!
    fireEvent.click(toolbar())
    await screen.findByRole('dialog', { name: 'Pattern factor' })
    fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
    await waitFor(() => expect(save).toHaveBeenCalledWith('experiment-1', expect.objectContaining({ design_spec: expect.objectContaining({ factors: [expect.objectContaining({ name: 'Writer:Reason + Act:Pattern levels', level_type: 'pattern' })] }) })))
    await waitFor(() => {
      const persisted = client.getQueryData<ProtocolGraph>(protocolGraphQueryKey('protocol-1'))!
      expect(persisted.nodes.find((node) => node.id === 'agent')?.data.factor_bindings).toEqual({ pattern_override: 'Writer:Reason + Act:Pattern levels' })
      expect(persisted.nodes.find((node) => node.id === 'agent')?.data.pattern_override).toEqual({ execution_pattern: 'reason_act', pattern_params: { reason_act: graph.nodes[1].data.config } })
      expect(persisted.nodes.find((node) => node.id === 'pattern')?.data.factor_bindings).toBeUndefined()
      expect(persisted.edges).toHaveLength(1)
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    fireEvent.click(toolbar())
    fireEvent.click(await screen.findByRole('button', { name: 'Remove factor' }))
    await waitFor(() => {
      const persisted = client.getQueryData<ProtocolGraph>(protocolGraphQueryKey('protocol-1'))!
      expect(persisted.nodes.find((node) => node.id === 'agent')?.data.factor_bindings).toEqual({})
      expect(persisted.nodes.find((node) => node.id === 'agent')?.data.pattern_override).toBeUndefined()
      expect(experiment.design_spec?.factors).toEqual([])
    })
  })

  it.each(['dataset_selection', 'dataset_toggle'] as const)('creates a %s dataset factor from the node hover menu and preserves connected nodes', async (mode) => {
    const experiment = {
      id: 'experiment-1', name: 'Experiment', description: null, hypothesis: null, design_type: 'factorial', task_brief: null,
      design_spec: { factors: [], metrics: [] }, measurement_plan: null, dataset_ids: [], dataset_id: null,
      locked_at: null, locked_protocol_revision_id: null, locked_design_spec: null, locked_measurement_plan: null,
      created_at: '', updated_at: '', archived_at: null,
    }
    vi.spyOn(experimentsApi, 'get').mockResolvedValue(experiment)
    const save = vi.spyOn(experimentsApi, 'update').mockResolvedValue(experiment)
    vi.spyOn(datasetsApi, 'list').mockResolvedValue([])
    const graph: ProtocolGraph = {
      nodes: [
        { id: 'agent', type: 'agent', position: { x: 100, y: 200 }, data: defaultAgentNodeData('Writer') },
        ...['a', 'b'].map((id) => ({ id, type: 'dataset', position: { x: 100, y: 0 }, data: { label: `Dataset ${id}`, config: { dataset_id: id, dataset_name: `Dataset ${id}` } } })),
      ],
      edges: ['a', 'b'].map((id) => ({ id, source: id, target: 'agent', targetHandle: 'dataset' })),
    }
    const { client } = renderCanvas(graph, 'experiment-1')
    const makeFactor = document.querySelector<HTMLButtonElement>('[data-testid="rf__node-a"] button[aria-label="Make experimental factor"]')
    expect(makeFactor).not.toBeNull()
    fireEvent.click(makeFactor!)
    await screen.findByRole('dialog')
    fireEvent.click(screen.getByRole('button', { name: mode === 'dataset_toggle' ? 'All datasets on/off' : 'Dataset levels' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
    await waitFor(() => expect(save).toHaveBeenCalledWith('experiment-1', expect.objectContaining({ design_spec: expect.objectContaining({ factors: [{ name: 'Writer:Datasets', level_type: mode, levels: mode === 'dataset_toggle' ? [['a', 'b'], []] : [['a'], ['b']], level_labels: mode === 'dataset_toggle' ? ['All enabled', 'All disabled'] : ['Dataset a', 'Dataset b'] }] }) })))
    await waitFor(() => {
      const persisted = client.getQueryData<ProtocolGraph>(protocolGraphQueryKey('protocol-1'))
      expect(persisted?.nodes.find((node) => node.id === 'agent')?.data.factor_bindings).toEqual({ dataset_selection: 'Writer:Datasets' })
      expect(persisted?.nodes.find((node) => node.id === 'agent')?.data.dataset_factor_mode).toBe(mode)
      expect(persisted?.nodes.filter((node) => node.type === 'dataset')).toHaveLength(2)
    })
  })

  it.each(['dataset', 'resource', 'tool'])('removes a Dataset factor on %s without enabling unrelated Tools or Scripts', async (handle) => {
    let experiment: Experiment = {
      id: 'experiment-1', name: 'Experiment', description: null, hypothesis: null, design_type: 'factorial', task_brief: null,
      design_spec: { factors: [{ name: 'Datasets', level_type: 'dataset_toggle', levels: [['dataset-id'], []], level_labels: ['All enabled', 'All disabled'] }], metrics: [] },
      measurement_plan: null, dataset_ids: ['dataset-id'], dataset_id: 'dataset-id',
      locked_at: null, locked_protocol_revision_id: null, locked_design_spec: null, locked_measurement_plan: null,
      created_at: '', updated_at: '', archived_at: null,
    }
    vi.spyOn(experimentsApi, 'get').mockImplementation(async () => experiment)
    vi.spyOn(experimentsApi, 'update').mockImplementation(async (_id, patch) => {
      experiment = { ...experiment, ...patch }
      return experiment
    })
    vi.spyOn(datasetsApi, 'list').mockResolvedValue([])
    vi.spyOn(mcpServersApi, 'list').mockResolvedValue([])
    const graph: ProtocolGraph = {
      nodes: [
        { id: 'agent', type: 'agent', position: { x: 100, y: 200 }, data: { ...defaultAgentNodeData('Writer'), dataset_selection: ['dataset-id'], dataset_factor_mode: 'dataset_toggle', factor_bindings: { dataset_selection: 'Datasets' } } },
        { id: 'dataset', type: 'dataset', position: { x: 100, y: 0 }, data: { ...defaultDatasetNodeData(), config: { dataset_id: 'dataset-id', dataset_name: 'Dataset', enabled: false } } },
        { id: 'tool', type: 'mcp_tool', position: { x: 300, y: 0 }, data: { label: 'Tool', config: { server_id: 'server', server_name: 'Server', tool_names: ['run'], enabled: false } } },
        { id: 'script', type: 'script', position: { x: 500, y: 0 }, data: { ...defaultScriptNodeData(), config: { name: 'Script', language: 'python', code: 'print(1)', enabled: false } } },
      ],
      edges: [
        { id: 'dataset-edge', source: 'dataset', target: 'agent', targetHandle: handle },
        ...['tool', 'script'].map((id) => ({ id: `${id}-edge`, source: id, target: 'agent', targetHandle: 'tool' })),
      ],
    }
    const { client } = renderCanvas(graph, 'experiment-1')
    await waitFor(() => expect(client.getQueryData<Experiment>(['experiments', 'experiment-1'])?.design_spec?.factors?.[0]?.name).toBe('Datasets'))
    fireEvent.click(document.querySelector<HTMLButtonElement>('[data-testid="rf__node-dataset"] button[aria-label="Make experimental factor"]')!)
    fireEvent.click(await screen.findByRole('button', { name: 'Remove factor' }))
    await waitFor(() => {
      const persisted = client.getQueryData<ProtocolGraph>(protocolGraphQueryKey('protocol-1'))!
      expect(persisted.nodes.find((node) => node.id === 'agent')?.data.factor_bindings).toEqual({})
      expect(persisted.nodes.find((node) => node.id === 'agent')?.data.dataset_selection).toBeUndefined()
      expect(persisted.nodes.find((node) => node.id === 'agent')?.data.dataset_factor_mode).toBeUndefined()
      expect(persisted.nodes.find((node) => node.id === 'dataset')?.data.config).toMatchObject({ enabled: true })
      for (const id of ['tool', 'script']) {
        expect(persisted.nodes.find((node) => node.id === id)?.data.config).toEqual(graph.nodes.find((node) => node.id === id)?.data.config)
      }
      expect(experiment.design_spec?.factors).toEqual([])
    })
  })

  it.each([null, 1])('selects a draft row for Publish & Test Run with published version %s', async publishedRevision => {
    const dataset = defaultDatasetNodeData()
    dataset.config.dataset_id = '11111111-1111-4111-8111-111111111111'
    const graph: ProtocolGraph = {
      nodes: [
        { id: 'agent-1', type: 'agent', position: { x: 100, y: 100 }, data: defaultAgentNodeData('Writer') },
        { id: 'dataset-1', type: 'dataset', position: { x: 100, y: 0 }, data: dataset },
      ],
      edges: [{ id: 'dataset-agent', source: 'dataset-1', sourceHandle: 'dataset', target: 'agent-1', targetHandle: 'dataset', data: { dataset_input: { mode: 'per_row', columns: ['question'] } } }],
    }
    const published = { id: 'protocol-1', published_revision_id: 'revision-1', published_revision: 1, graph }
    vi.spyOn(protocolsApi, 'get').mockResolvedValue({ ...published, published_revision_id: publishedRevision ? 'older-revision' : null, published_revision: publishedRevision } as Awaited<ReturnType<typeof protocolsApi.get>>)
    vi.spyOn(protocolsApi, 'publish').mockResolvedValue(published as Awaited<ReturnType<typeof protocolsApi.publish>>)
    vi.spyOn(protocolsApi, 'getRevision').mockImplementation(async (_id, revisionId) => ({ id: revisionId, graph: revisionId === 'older-revision' ? { ...graph, edges: graph.edges.map(edge => ({ ...edge, data: {} })) } : graph } as Awaited<ReturnType<typeof protocolsApi.getRevision>>))
    vi.spyOn(datasetsApi, 'getRowSchema').mockResolvedValue({ dataset_id: 'dataset-1', raw_sha256: 'hash', row_count: 8, columns: ['question'] })
    const start = vi.spyOn(protocolsApi, 'testRun').mockResolvedValue(testRun())
    vi.spyOn(protocolsApi, 'getRun').mockResolvedValue(protocolRun({ status: 'completed' }))
    renderCanvas(graph, null, publishedRevision)
    fireEvent.click(screen.getByRole('button', { name: 'Test Run' }))
    const row = await screen.findByRole('spinbutton', { name: 'Source row' })
    await waitFor(() => expect(row).toBeEnabled())
    fireEvent.change(row, { target: { value: '5' } })
    fireEvent.change(row, { target: { value: '9' } })
    expect(screen.getByRole('button', { name: 'Publish & Test Run' })).toBeDisabled()
    expect(start).not.toHaveBeenCalled()
    fireEvent.change(row, { target: { value: '5' } })
    expect(screen.getByRole('button', { name: 'Publish & Test Run' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Publish & Test Run' }))
    await waitFor(() => expect(start).toHaveBeenCalledWith('protocol-1', { row_index: 4 }))
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

  it('makes occupied single-capacity connector handles non-connectable', async () => {
    const agentData = defaultAgentNodeData('Writer')
    agentData.config.require_output_parser = true
    renderCanvas({
      nodes: [
        { id: 'agent-1', type: 'agent', position: { x: 100, y: 100 }, data: agentData },
        { id: 'gate-1', type: 'critic_gate', position: { x: 400, y: 100 }, data: defaultCriticGateNodeData() },
        { id: 'model-1', type: 'model_anthropic', position: { x: 0, y: 0 }, data: defaultAnthropicModelNodeData() },
        { id: 'memory-1', type: 'memory', position: { x: 0, y: 0 }, data: defaultMemoryNodeData() },
        { id: 'pattern-1', type: 'pattern_reason_act', position: { x: 0, y: 0 }, data: defaultReasonActPatternNodeData() },
        { id: 'parser-1', type: 'output_parser', position: { x: 0, y: 0 }, data: defaultOutputParserNodeData() },
      ],
      edges: [
        { id: 'model-agent', source: 'model-1', sourceHandle: 'model', target: 'agent-1', targetHandle: 'model' },
        { id: 'model-gate', source: 'model-1', sourceHandle: 'model', target: 'gate-1', targetHandle: 'model' },
        { id: 'memory-agent', source: 'memory-1', sourceHandle: 'memory', target: 'agent-1', targetHandle: 'memory' },
        { id: 'pattern-agent', source: 'pattern-1', sourceHandle: 'architectural_pattern', target: 'agent-1', targetHandle: 'architectural_pattern' },
        { id: 'parser-agent', source: 'parser-1', sourceHandle: 'output_parser', target: 'agent-1', targetHandle: 'output_parser' },
      ],
    })

    await screen.findByText('Writer')
    for (const handleId of ['model', 'memory', 'architectural_pattern', 'output_parser']) {
      const handle = document.querySelector(`[data-nodeid="agent-1"][data-handleid="${handleId}"]`)
      expect(handle).toBeInTheDocument()
      expect(handle).not.toHaveClass('connectable')
    }
    expect(document.querySelector('[data-nodeid="gate-1"][data-handleid="model"]')).not.toHaveClass('connectable')
    expect(document.querySelector('[data-nodeid="model-1"][data-handleid="model"]')).toHaveClass('connectable')
  })

  it('draws Tool Step main-flow arrows under Sequential and Critic Gate strategies', async () => {
    const graph: ProtocolGraph = {
      nodes: [
        { id: 'agent-1', type: 'agent', position: { x: 100, y: 100 }, data: defaultAgentNodeData('Writer') },
        {
          id: 'tool-step-1',
          type: 'tool_step',
          position: { x: 400, y: 100 },
          data: { label: 'Fetch', config: { tool_name: '', arguments: {}, hash_checks: {}, timeout_seconds: null } },
        },
        { id: 'agent-2', type: 'agent', position: { x: 700, y: 100 }, data: defaultAgentNodeData('Reviewer') },
      ],
      edges: [
        { id: 'into-tool', source: 'agent-1', target: 'tool-step-1' },
        { id: 'out-of-tool', source: 'tool-step-1', target: 'agent-2' },
      ],
    }

    const sequential = renderCanvas(graph)
    await waitFor(() => expect(sequential.container.querySelector('.react-flow__arrowhead')).toBeInTheDocument())
    sequential.unmount()

    vi.spyOn(experimentsApi, 'get').mockResolvedValue({
      id: 'experiment-1', name: 'Experiment', description: null, hypothesis: null, design_type: 'factorial', task_brief: null,
      design_spec: { factors: [], metrics: [], coordination_strategy: { slug: 'critic_gate', params: {} } },
      measurement_plan: null, dataset_ids: [], dataset_id: null, locked_at: null,
      locked_protocol_revision_id: null, locked_design_spec: null, locked_measurement_plan: null,
      created_at: '', updated_at: '', archived_at: null,
    })
    const critic = renderCanvas(graph, 'experiment-1')
    await waitFor(() => expect(critic.container.querySelector('.react-flow__arrowhead')).toBeInTheDocument())
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

  it.each([false, true])('keeps factor declarations when a bound Skill node is deleted (shared: %s)', async (shared) => {
    const factorName = 'Skill enabled'
    const experiment: Experiment = {
      id: 'experiment-1', name: 'Experiment', description: null, hypothesis: null, design_type: 'factorial', task_brief: null,
      design_spec: { factors: [{ name: factorName, levels: [true, false], level_type: 'boolean' }], metrics: [] },
      measurement_plan: null, dataset_ids: [], dataset_id: null, locked_at: null,
      locked_protocol_revision_id: null, locked_design_spec: null, locked_measurement_plan: null,
      created_at: '', updated_at: '', archived_at: null,
    }
    vi.spyOn(experimentsApi, 'get').mockResolvedValue(experiment)
    const save = vi.spyOn(experimentsApi, 'update').mockResolvedValue(experiment)
    vi.spyOn(skillsApi, 'list').mockResolvedValue([])
    const graph: ProtocolGraph = {
      nodes: (shared ? ['a', 'b'] : ['a']).map((id) => ({
        id, type: 'skill', position: { x: id === 'a' ? 100 : 300, y: 100 },
        data: {
          label: `Skill ${id}`, config: { skill_id: id, skill_name: `Skill ${id}`, enabled: true },
          factor_bindings: { 'config.enabled': factorName },
        },
      })),
      edges: [],
    }
    const { client } = renderCanvas(graph, experiment.id)
    fireEvent.doubleClick(await screen.findByText('Skill a'))
    fireEvent.click(await screen.findByRole('button', { name: 'Delete node' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }))

    await waitFor(() => {
      const persisted = client.getQueryData<ProtocolGraph>(protocolGraphQueryKey('protocol-1'))
      expect(persisted?.nodes.some((node) => node.id === 'a')).toBe(false)
    })
    expect(save).not.toHaveBeenCalled()
    expect(client.getQueryData<Experiment>(['experiments', experiment.id])?.design_spec?.factors).toEqual(experiment.design_spec?.factors)
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
    expect(screen.getByText('Levels: false, true')).toBeInTheDocument()
    expect(screen.queryByText('Bind to a field on the canvas')).not.toBeInTheDocument()
  })

  it('keeps the Test Run label when a production replicate is running', async () => {
    const run = protocolRun()
    vi.mocked(protocolsApi.listRuns).mockResolvedValue([run])
    vi.spyOn(protocolsApi, 'getRun').mockResolvedValue(run)

    renderCanvas({ nodes: [], edges: [] })

    await screen.findByRole('button', { name: 'Stop' })
    expect(screen.getByRole('button', { name: 'Test Run' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Test Run running…' })).not.toBeInTheDocument()
  })

  it('stops the active production replicate instead of a completed Test Run', async () => {
    const user = userEvent.setup()
    const run = protocolRun()
    vi.mocked(protocolsApi.listRuns).mockResolvedValue([run])
    vi.spyOn(protocolsApi, 'getRun').mockResolvedValue(run)
    vi.spyOn(protocolsApi, 'getLatestTestRun').mockResolvedValue(testRun())
    const cancelRun = vi.spyOn(protocolsApi, 'cancelRun').mockResolvedValue({
      ...run,
      cancel_requested_at: '2026-10-01T12:00:30Z',
    })
    vi.spyOn(experimentsApi, 'get').mockResolvedValue({
      id: 'experiment-1', name: 'Experiment', description: null, hypothesis: null, design_type: 'factorial', task_brief: null,
      design_spec: { factors: [], metrics: [] }, measurement_plan: null, dataset_ids: [], dataset_id: null,
      locked_at: null, locked_protocol_revision_id: null, locked_design_spec: null, locked_measurement_plan: null,
      created_at: '', updated_at: '', archived_at: null,
    })

    renderCanvas({ nodes: [], edges: [] }, 'experiment-1')

    await user.click(await screen.findByRole('button', { name: 'Stop' }))
    expect(cancelRun).toHaveBeenCalledWith('protocol-1', 'production-run')
  })

})

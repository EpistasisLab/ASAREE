import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, experimentsApi } from '@/api/client'
import { protocolGraphQueryKey } from '@/lib/protocolGraph'
import type { Experiment } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'
import { DesignTab } from './DesignTab'
import type { ProtocolCanvasHandle } from './ProtocolCanvas'

const experiment: Experiment = {
  id: 'experiment-1',
  name: 'Metrics demo — built-ins',
  description: null,
  hypothesis: null,
  design_type: 'factorial',
  task_brief: null,
  design_spec: {
    factors: [
      {
        name: 'Answer style',
        levels: ['A long prompt'],
        level_labels: ['concise'],
        level_type: 'text',
      },
    ],
    metrics: [],
    replicates: 1,
    coordination_strategy: { slug: 'sequential', params: {} },
  },
  measurement_plan: null,
  dataset_ids: [],
  dataset_id: null,
  locked_at: null,
  locked_protocol_revision_id: null,
  locked_design_spec: null,
  locked_measurement_plan: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  archived_at: null,
}

describe('DesignTab design generation', () => {
  it.each([
    ['Sub-Agent levels', 'sub_agent', 'sub_agent_selection'],
    ['All agent sub-agents on/off', 'sub_agent', 'sub_agent_toggle'],
    ['This sub-agent on/off', 'sub_agent', 'boolean'],
    ['Skill levels', 'skill', 'skill_selection'],
    ['All skills on/off', 'skill', 'skill_toggle'],
    ['This skill on/off', 'skill', 'boolean'],
    ['Dataset levels', 'dataset', 'dataset_selection'],
    ['All datasets on/off', 'dataset', 'dataset_toggle'],
    ['This dataset on/off', 'dataset', 'boolean'],
  ] as const)('routes %s from New factor to the matching connector dialog', async (label, kind, mode) => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    client.setQueryData(protocolGraphQueryKey('protocol-1'), {
      nodes: [
        { id: 'agent', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Writer', config: {} } },
        { id: 'skill', type: 'skill', position: { x: 0, y: 0 }, data: { label: 'Summarize', config: { skill_id: 'a' } } },
        { id: 'sub_agent', type: 'sub_agent', position: { x: 0, y: 0 }, data: { label: 'Worker', config: {} } },
        { id: 'dataset', type: 'dataset', position: { x: 0, y: 0 }, data: { label: 'Cohort', config: { dataset_id: 'd' } } },
      ],
      edges: ['skill', 'dataset', 'sub_agent'].map(source => ({ id: source, source, target: 'agent', targetHandle: source === 'sub_agent' ? 'sub_agents' : source })),
    })
    vi.spyOn(experimentsApi, 'getMeasurementCapabilities').mockResolvedValue({ outputs: {} })
    vi.spyOn(experimentsApi, 'validateMeasurementPlan').mockResolvedValue({ valid: true, issues: [] })
    const openConnectorFactor = vi.fn()
    const handle: ProtocolCanvasHandle = {
      openConnectorFactor, bindFactor: vi.fn(), removeFactorBindings: vi.fn(),
      renameFactorBindings: vi.fn(), setFactorBaseline: vi.fn(),
    }
    render(<QueryClientProvider client={client}><DesignTab experiment={experiment} protocolId="protocol-1" canvasRef={{ current: handle }} onDesignUpdatePendingChange={vi.fn()} /></QueryClientProvider>)
    await user.click(screen.getByRole('button', { name: 'Add factor' }))
    await user.type(screen.getByPlaceholderText('Search fields…'), label)
    await user.click(screen.getByRole('button', { name: new RegExp(label) }))
    expect(openConnectorFactor).toHaveBeenCalledWith(expect.objectContaining({ connectorFactor: expect.objectContaining({ kind, nodeId: kind, mode }) }))
    expect(screen.queryByPlaceholderText('Search fields…')).not.toBeInTheDocument()
    client.clear()
  })

  it('shows the API error when applying cells fails', async () => {
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    client.setQueryData(protocolGraphQueryKey('protocol-1'), {
      nodes: [
        {
          id: 'agent-1',
          type: 'agent',
          position: { x: 0, y: 0 },
          data: { label: 'Writer', active: true, config: {}, factor_bindings: { prompt: 'Answer style' } },
        },
      ],
      edges: [],
    } as unknown as ProtocolGraph)
    vi.spyOn(experimentsApi, 'getDesignImpact').mockResolvedValue({
      has_generated_design: false,
      regeneration_required: true,
      regeneration_reasons: ['no_design_generated'],
      current_cell_count: 0,
      proposed_cell_count: 1,
      added_cell_count: 1,
      retained_cell_count: 0,
      removed_cell_count: 0,
      current_replicate_count: 0,
      proposed_replicate_count: 1,
      added_replicate_count: 1,
      retained_replicate_count: 0,
      removed_replicate_count: 0,
    })
    vi.spyOn(experimentsApi, 'getMeasurementCapabilities').mockResolvedValue({ outputs: {} })
    vi.spyOn(experimentsApi, 'validateMeasurementPlan').mockResolvedValue({ valid: true, issues: [] })
    vi.spyOn(experimentsApi, 'generateDesign').mockRejectedValue(
      new ApiError(500, 'Cell generation failed.'),
    )

    render(
      <QueryClientProvider client={client}>
        <DesignTab
          experiment={experiment}
          protocolId="protocol-1"
          canvasRef={{ current: null }}
          onDesignUpdatePendingChange={vi.fn()}
        />
      </QueryClientProvider>,
    )

    await user.click(await screen.findByRole('button', { name: 'Apply changes to cells' }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(
        'Could not generate cells: Cell generation failed.',
      )
    })
  })
})

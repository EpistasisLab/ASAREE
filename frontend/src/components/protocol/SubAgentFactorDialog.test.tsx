import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import type { ProtocolGraph } from '@/types/protocols'
import { SubAgentFactorDialog } from './SubAgentFactorDialog'

const graph = {
  nodes: [
    { id: 'parent', type: 'agent', data: { label: 'Writer' } },
    { id: 'a', type: 'sub_agent', data: { label: 'Researcher', active: false, factor_bindings: { 'config.prompt': 'Prompt' } } },
    { id: 'b', type: 'sub_agent', data: { label: 'Reviewer', active: false } },
  ],
  edges: ['a', 'b'].map((source) => ({ source, target: 'parent', targetHandle: 'sub_agents' })),
} as unknown as ProtocolGraph

it.each(['Sub-Agent levels', 'All agent sub-agents on/off'])('creates %s with disabled workers while allowing prompt factors', async (mode) => {
  const save = vi.fn().mockResolvedValue(undefined)
  render(<SubAgentFactorDialog subAgentNodeId="a" graph={graph} factors={[]} onClose={vi.fn()} onSave={save} onRemove={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: mode }))
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({
    level_type: mode === 'Sub-Agent levels' ? 'sub_agent_selection' : 'sub_agent_toggle',
    levels: mode === 'Sub-Agent levels' ? [['a'], ['b']] : [['a', 'b'], []],
  }), 'parent', undefined))
})

it('blocks parent factors only for individual on/off bindings', () => {
  const conflicting = { ...graph, nodes: graph.nodes.map((node) => node.id === 'a' ? { ...node, data: { ...node.data, factor_bindings: { active: 'Enabled' } } } : node) }
  render(<SubAgentFactorDialog subAgentNodeId="a" graph={conflicting} factors={[{ name: 'Enabled', level_type: 'boolean', levels: [false, true] }]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: 'Sub-Agent levels' }))
  expect(screen.getByRole('status')).toHaveTextContent('Enabled')
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'This sub-agent on/off' }))
  expect(screen.getByRole('button', { name: 'Remove factor' })).toBeEnabled()
})

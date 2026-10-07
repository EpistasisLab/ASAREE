import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import type { ProtocolGraph } from '@/types/protocols'
import { SkillFactorDialog } from './SkillFactorDialog'

const graph = {
  nodes: [
    { id: 'agent', type: 'agent', data: { label: 'Writer' } },
    { id: 'skill', type: 'skill', data: { label: 'Summarize', config: { skill_id: 'a' }, factor_bindings: { 'config.enabled': 'Skill enabled' } } },
  ],
  edges: [{ source: 'skill', target: 'agent', targetHandle: 'skill' }],
} as unknown as ProtocolGraph

it('requires explicit confirmation before replacing individual factors with a group factor', async () => {
  const save = vi.fn().mockResolvedValue(undefined)
  render(<SkillFactorDialog skillNodeId="skill" graph={graph} factors={[{ name: 'Skill enabled', level_type: 'boolean', levels: [false, true] }]} onClose={vi.fn()} onSave={save} onRemove={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: 'All agent skills on/off' }))
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
  expect(screen.getByText(/removes these factor declarations/)).toHaveTextContent('Skill enabled')
  fireEvent.click(screen.getByRole('checkbox', { name: 'Replace these factors' }))
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith({ name: 'Writer:Skills', level_type: 'skill_toggle', levels: [['a'], []], level_labels: ['All enabled', 'All disabled'] }, 'agent', ['Skill enabled']))
})

it('only offers individual factors for disconnected skills', () => {
  render(<SkillFactorDialog skillNodeId="skill" graph={{ ...graph, edges: [] }} factors={[]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.getByRole('button', { name: 'Skill levels' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'All agent skills on/off' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'This skill on/off' })).toHaveAttribute('aria-pressed', 'true')
})

it('opens the existing group factor when a shared skill has multiple connected agents', () => {
  const sharedGraph = {
    ...graph,
    nodes: [
      { ...graph.nodes[0], data: { label: 'First agent' } },
      { id: 'second', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Second agent', skill_selection: ['a'], skill_factor_mode: 'skill_toggle', factor_bindings: { skill_selection: 'Group' } } },
      { ...graph.nodes[1], data: { label: 'Summarize', config: { skill_id: 'a' } } },
    ],
    edges: [...graph.edges, { id: 'second-edge', source: 'skill', target: 'second', targetHandle: 'skill' }],
  } as ProtocolGraph
  render(<SkillFactorDialog skillNodeId="skill" graph={sharedGraph} factors={[{ name: 'Group', level_type: 'skill_toggle', levels: [['a'], []], level_labels: ['All enabled', 'All disabled'] }]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.getByRole('combobox')).toHaveTextContent('Second agent')
  expect(screen.getByRole('button', { name: 'All agent skills on/off' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByLabelText('Factor name')).toHaveValue('Group')
})

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

it('keeps the dialog open after removal and permits choosing another factor type', async () => {
  const remove = vi.fn().mockResolvedValue(undefined)
  const close = vi.fn()
  const { rerender } = render(<SkillFactorDialog skillNodeId="skill" graph={graph} factors={[{ name: 'Skill enabled', level_type: 'boolean', levels: [false, true] }]} onClose={close} onSave={vi.fn()} onRemove={remove} />)
  fireEvent.click(screen.getByRole('button', { name: 'Remove factor' }))
  await waitFor(() => expect(remove).toHaveBeenCalledWith('Skill enabled'))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled())
  expect(close).not.toHaveBeenCalled()
  const updated = { ...graph, nodes: graph.nodes.map((node) => ({ ...node, data: { ...node.data, factor_bindings: {} } })) }
  rerender(<SkillFactorDialog skillNodeId="skill" graph={updated} factors={[]} onClose={close} onSave={vi.fn()} onRemove={remove} />)
  expect(screen.queryByRole('button', { name: 'Remove factor' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Skill levels' }))
  expect(screen.getByLabelText('Factor name')).toBeEnabled()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it.each(['Skill levels', 'All skills on/off'])('explains why %s conflicts with individual bindings without offering replacement', (mode) => {
  const save = vi.fn().mockResolvedValue(undefined)
  render(<SkillFactorDialog skillNodeId="skill" graph={graph} factors={[{ name: 'Skill enabled', level_type: 'boolean', levels: [false, true] }]} onClose={vi.fn()} onSave={save} onRemove={vi.fn()} />)
  const option = screen.getByRole('button', { name: mode })
  expect(option).toHaveClass('bg-secondary', 'text-secondary-foreground', 'border-dashed')
  expect(option).not.toHaveClass('opacity-50', 'text-muted-foreground')
  fireEvent.click(option)
  expect(option).toHaveClass('bg-primary', 'text-primary-foreground')
  expect(option).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
  expect(screen.getByRole('status')).toHaveTextContent('Skill enabled')
  expect(screen.getByRole('status')).toHaveTextContent('remove those bindings in each Skill’s factor dialog first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  expect(save).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'This skill on/off' }))
  expect(screen.getByLabelText('Factor name')).toHaveValue('Skill enabled')
  expect(screen.getByRole('button', { name: 'Remove factor' })).toBeEnabled()
})

it('only offers individual factors for disconnected skills', () => {
  render(<SkillFactorDialog skillNodeId="skill" graph={{ ...graph, edges: [] }} factors={[]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.getByRole('button', { name: 'This skill on/off' })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(screen.getByRole('button', { name: 'Skill levels' }))
  expect(screen.getByRole('status')).toHaveTextContent('Connect this Skill to an Agent first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
})

it.each(['This skill on/off', 'Skill levels'])('blocks %s when an Agent already has an all-skills factor', (mode) => {
  const grouped = {
    ...graph,
    nodes: [
      { ...graph.nodes[0], data: { label: 'Writer', factor_bindings: { skill_selection: 'Group' } } },
      { ...graph.nodes[1], data: { label: 'Summarize', config: { skill_id: 'a' } } },
    ],
  } as ProtocolGraph
  render(<SkillFactorDialog skillNodeId="skill" graph={grouped} factors={[{ name: 'Group', level_type: 'skill_toggle', levels: [['a'], []], level_labels: ['All enabled', 'All disabled'] }]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: mode }))
  expect(screen.getByRole('status')).toHaveTextContent('Group')
  expect(screen.getByRole('status')).toHaveTextContent('remove it first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
})

it('saves edits to an existing individual factor without removing other declarations', async () => {
  const save = vi.fn().mockResolvedValue(undefined)
  render(<SkillFactorDialog skillNodeId="skill" graph={graph} factors={[{ name: 'Skill enabled', level_type: 'boolean', levels: [false, true] }]} onClose={vi.fn()} onSave={save} onRemove={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('Factor name'), { target: { value: 'Renamed' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith({ name: 'Renamed', level_type: 'boolean', levels: [false, true], level_labels: ['Disabled', 'Enabled'] }, 'skill', 'Skill enabled'))
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
  expect(screen.getByRole('button', { name: 'All skills on/off' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByLabelText('Factor name')).toHaveValue('Group')
})

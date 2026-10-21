import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import type { ProtocolGraph } from '@/types/protocols'
import { PersonaFactorDialog } from './PersonaFactorDialog'

const graph = {
  nodes: [
    { id: 'agent', type: 'agent', data: { label: 'Writer' } },
    { id: 'persona', type: 'persona', data: { label: 'Summarize', config: { persona_id: 'a' }, factor_bindings: { 'config.enabled': 'Persona enabled' } } },
  ],
  edges: [{ source: 'persona', target: 'agent', targetHandle: 'persona' }],
} as unknown as ProtocolGraph

it('keeps the dialog open after removal and permits choosing another factor type', async () => {
  const remove = vi.fn().mockResolvedValue(undefined)
  const close = vi.fn()
  const { rerender } = render(<PersonaFactorDialog personaNodeId="persona" graph={graph} factors={[{ name: 'Persona enabled', level_type: 'boolean', levels: [false, true] }]} onClose={close} onSave={vi.fn()} onRemove={remove} />)
  fireEvent.click(screen.getByRole('button', { name: 'Remove factor' }))
  await waitFor(() => expect(remove).toHaveBeenCalledWith('Persona enabled'))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled())
  expect(close).not.toHaveBeenCalled()
  const updated = { ...graph, nodes: graph.nodes.map((node) => ({ ...node, data: { ...node.data, factor_bindings: {} } })) }
  rerender(<PersonaFactorDialog personaNodeId="persona" graph={updated} factors={[]} onClose={close} onSave={vi.fn()} onRemove={remove} />)
  expect(screen.queryByRole('button', { name: 'Remove factor' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Persona levels' }))
  expect(screen.getByLabelText('Factor name')).toBeEnabled()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it.each(['Persona levels', 'All personas on/off'])('explains why %s conflicts with individual bindings without offering replacement', (mode) => {
  const save = vi.fn().mockResolvedValue(undefined)
  render(<PersonaFactorDialog personaNodeId="persona" graph={graph} factors={[{ name: 'Persona enabled', level_type: 'boolean', levels: [false, true] }]} onClose={vi.fn()} onSave={save} onRemove={vi.fn()} />)
  const option = screen.getByRole('button', { name: mode })
  expect(option).toHaveClass('bg-secondary', 'text-secondary-foreground', 'border-dashed')
  expect(option).not.toHaveClass('opacity-50', 'text-muted-foreground')
  fireEvent.click(option)
  expect(option).toHaveClass('bg-primary', 'text-primary-foreground')
  expect(option).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
  expect(screen.getByRole('status')).toHaveTextContent('Persona enabled')
  expect(screen.getByRole('status')).toHaveTextContent('remove those bindings in each Persona’s factor dialog first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  expect(save).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'This persona on/off' }))
  expect(screen.getByLabelText('Factor name')).toHaveValue('Persona enabled')
  expect(screen.getByRole('button', { name: 'Remove factor' })).toBeEnabled()
})

it('only offers individual factors for disconnected personas', () => {
  render(<PersonaFactorDialog personaNodeId="persona" graph={{ ...graph, edges: [] }} factors={[]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.getByRole('button', { name: 'This persona on/off' })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(screen.getByRole('button', { name: 'Persona levels' }))
  expect(screen.getByRole('status')).toHaveTextContent('Connect this Persona to an Agent first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
})

it.each(['This persona on/off', 'Persona levels'])('blocks %s when an Agent already has an all-personas factor', (mode) => {
  const grouped = {
    ...graph,
    nodes: [
      { ...graph.nodes[0], data: { label: 'Writer', factor_bindings: { persona_selection: 'Group' } } },
      { ...graph.nodes[1], data: { label: 'Summarize', config: { persona_id: 'a' } } },
    ],
  } as ProtocolGraph
  render(<PersonaFactorDialog personaNodeId="persona" graph={grouped} factors={[{ name: 'Group', level_type: 'persona_toggle', levels: [['a'], []], level_labels: ['All enabled', 'All disabled'] }]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: mode }))
  expect(screen.getByRole('status')).toHaveTextContent('Group')
  expect(screen.getByRole('status')).toHaveTextContent('remove it first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
})

it('saves edits to an existing individual factor without removing other declarations', async () => {
  const save = vi.fn().mockResolvedValue(undefined)
  render(<PersonaFactorDialog personaNodeId="persona" graph={graph} factors={[{ name: 'Persona enabled', level_type: 'boolean', levels: [false, true] }]} onClose={vi.fn()} onSave={save} onRemove={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('Factor name'), { target: { value: 'Renamed' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith({ name: 'Renamed', level_type: 'boolean', levels: [false, true], level_labels: ['Disabled', 'Enabled'] }, 'persona', 'Persona enabled'))
})

it('opens the existing group factor when a shared persona has multiple connected agents', () => {
  const sharedGraph = {
    ...graph,
    nodes: [
      { ...graph.nodes[0], data: { label: 'First agent' } },
      { id: 'second', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Second agent', persona_selection: ['a'], persona_factor_mode: 'persona_toggle', factor_bindings: { persona_selection: 'Group' } } },
      { ...graph.nodes[1], data: { label: 'Summarize', config: { persona_id: 'a' } } },
    ],
    edges: [...graph.edges, { id: 'second-edge', source: 'persona', target: 'second', targetHandle: 'persona' }],
  } as ProtocolGraph
  render(<PersonaFactorDialog personaNodeId="persona" graph={sharedGraph} factors={[{ name: 'Group', level_type: 'persona_toggle', levels: [['a'], []], level_labels: ['All enabled', 'All disabled'] }]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  expect(screen.getByText('Applies to: First agent, Second agent')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'All personas on/off' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByLabelText('Factor name')).toHaveValue('Group')
})

it('blocks an incomplete shared group while keeping its factor removable', async () => {
  const remove = vi.fn().mockResolvedValue(undefined)
  const sharedGraph = {
    nodes: [
      ...['one', 'two'].map((id) => ({ id, type: 'agent', data: { label: id, persona_selection: ['a'], persona_factor_mode: 'persona_selection', factor_bindings: { persona_selection: 'Shared' } } })),
      ...['a', 'b'].map((id) => ({ id, type: 'persona', data: { label: id, config: { persona_id: id } } })),
    ],
    edges: [{ source: 'a', target: 'one', targetHandle: 'persona' }, { source: 'a', target: 'two', targetHandle: 'persona' }, { source: 'b', target: 'one', targetHandle: 'persona' }],
  } as unknown as ProtocolGraph
  render(<PersonaFactorDialog personaNodeId="b" graph={sharedGraph} factors={[{ name: 'Shared', level_type: 'persona_selection', levels: [['a'], ['b']] }]} onClose={vi.fn()} onSave={vi.fn()} onRemove={remove} />)
  expect(screen.getByRole('status')).toHaveTextContent('exactly the same Agents')
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Remove factor' }))
  await waitFor(() => expect(remove).toHaveBeenCalledWith('Shared'))
})

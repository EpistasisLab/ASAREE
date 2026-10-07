import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import type { ProtocolGraph } from '@/types/protocols'
import { KnowledgeFactorDialog } from './KnowledgeFactorDialog'

const graph = {
  nodes: [
    { id: 'agent', type: 'agent', data: { label: 'Writer' } },
    { id: 'knowledge', type: 'okf_bundle', data: { label: 'Summarize', config: { bundle_id: 'a' }, factor_bindings: { 'config.enabled': 'Knowledge enabled' } } },
  ],
  edges: [{ source: 'knowledge', target: 'agent', targetHandle: 'knowledge' }],
} as unknown as ProtocolGraph

it.each(['Knowledge levels', 'All knowledge on/off'])('explains why %s conflicts with individual bindings without offering replacement', (mode) => {
  const save = vi.fn().mockResolvedValue(undefined)
  render(<KnowledgeFactorDialog knowledgeNodeId="knowledge" graph={graph} factors={[{ name: 'Knowledge enabled', level_type: 'boolean', levels: [false, true] }]} onClose={vi.fn()} onSave={save} onRemove={vi.fn()} />)
  const option = screen.getByRole('button', { name: mode })
  expect(option).toHaveClass('bg-secondary', 'text-secondary-foreground', 'border-dashed')
  expect(option).not.toHaveClass('opacity-50', 'text-muted-foreground')
  fireEvent.click(option)
  expect(option).toHaveClass('bg-primary', 'text-primary-foreground')
  expect(option).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
  expect(screen.getByRole('status')).toHaveTextContent('Knowledge enabled')
  expect(screen.getByRole('status')).toHaveTextContent('remove those bindings in each Knowledge node’s factor dialog first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  expect(save).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'This knowledge on/off' }))
  expect(screen.getByLabelText('Factor name')).toHaveValue('Knowledge enabled')
  expect(screen.getByRole('button', { name: 'Remove factor' })).toBeEnabled()
})

it('only offers individual factors for disconnected knowledge', () => {
  render(<KnowledgeFactorDialog knowledgeNodeId="knowledge" graph={{ ...graph, edges: [] }} factors={[]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.getByRole('button', { name: 'This knowledge on/off' })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(screen.getByRole('button', { name: 'Knowledge levels' }))
  expect(screen.getByRole('status')).toHaveTextContent('Connect this Knowledge to an Agent first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
})

it.each(['This knowledge on/off', 'Knowledge levels'])('blocks %s when an Agent already has an all-knowledge factor', (mode) => {
  const grouped = {
    ...graph,
    nodes: [
      { ...graph.nodes[0], data: { label: 'Writer', factor_bindings: { knowledge_selection: 'Group' } } },
      { ...graph.nodes[1], data: { label: 'Summarize', config: { bundle_id: 'a' } } },
    ],
  } as ProtocolGraph
  render(<KnowledgeFactorDialog knowledgeNodeId="knowledge" graph={grouped} factors={[{ name: 'Group', level_type: 'knowledge_toggle', levels: [['a'], []], level_labels: ['All enabled', 'All disabled'] }]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: mode }))
  expect(screen.getByRole('status')).toHaveTextContent('Group')
  expect(screen.getByRole('status')).toHaveTextContent('remove it first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
})

it('saves edits to an existing individual factor without removing other declarations', async () => {
  const save = vi.fn().mockResolvedValue(undefined)
  render(<KnowledgeFactorDialog knowledgeNodeId="knowledge" graph={graph} factors={[{ name: 'Knowledge enabled', level_type: 'boolean', levels: [false, true] }]} onClose={vi.fn()} onSave={save} onRemove={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('Factor name'), { target: { value: 'Renamed' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith({ name: 'Renamed', level_type: 'boolean', levels: [false, true], level_labels: ['Disabled', 'Enabled'] }, 'knowledge', 'Knowledge enabled'))
})

it('opens the existing group factor when a shared knowledge has multiple connected agents', () => {
  const sharedGraph = {
    ...graph,
    nodes: [
      { ...graph.nodes[0], data: { label: 'First agent' } },
      { id: 'second', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Second agent', knowledge_selection: ['a'], knowledge_factor_mode: 'knowledge_toggle', factor_bindings: { knowledge_selection: 'Group' } } },
      { ...graph.nodes[1], data: { label: 'Summarize', config: { bundle_id: 'a' } } },
    ],
    edges: [...graph.edges, { id: 'second-edge', source: 'knowledge', target: 'second', targetHandle: 'knowledge' }],
  } as ProtocolGraph
  render(<KnowledgeFactorDialog knowledgeNodeId="knowledge" graph={sharedGraph} factors={[{ name: 'Group', level_type: 'knowledge_toggle', levels: [['a'], []], level_labels: ['All enabled', 'All disabled'] }]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.getByRole('combobox')).toHaveTextContent('Second agent')
  expect(screen.getByRole('button', { name: 'All knowledge on/off' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByLabelText('Factor name')).toHaveValue('Group')
})

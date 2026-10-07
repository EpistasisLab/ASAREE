import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import type { ProtocolGraph } from '@/types/protocols'
import { ScriptFactorDialog } from './ScriptFactorDialog'

const graph = {
  nodes: [
    { id: 'agent', type: 'agent', data: { label: 'Writer' } },
    { id: 'script', type: 'script', data: { label: 'Summarize', config: { name: 'Scoring', language: 'python', code: 'print(1)' }, factor_bindings: { 'config.enabled': 'Script enabled' } } },
  ],
  edges: [{ source: 'script', target: 'agent', targetHandle: 'tool' }],
} as unknown as ProtocolGraph

it('keeps the dialog open after removal and permits choosing another factor type', async () => {
  const remove = vi.fn().mockResolvedValue(undefined)
  const close = vi.fn()
  const { rerender } = render(<ScriptFactorDialog scriptNodeId="script" graph={graph} factors={[{ name: 'Script enabled', level_type: 'boolean', levels: [false, true] }]} onClose={close} onSave={vi.fn()} onRemove={remove} />)
  fireEvent.click(screen.getByRole('button', { name: 'Remove factor' }))
  await waitFor(() => expect(remove).toHaveBeenCalledWith('Script enabled'))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled())
  expect(close).not.toHaveBeenCalled()
  const updated = { ...graph, nodes: graph.nodes.map((node) => ({ ...node, data: { ...node.data, factor_bindings: {} } })) }
  rerender(<ScriptFactorDialog scriptNodeId="script" graph={updated} factors={[]} onClose={close} onSave={vi.fn()} onRemove={remove} />)
  expect(screen.queryByRole('button', { name: 'Remove factor' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Script levels' }))
  expect(screen.getByLabelText('Factor name')).toBeEnabled()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it.each(['Script levels', 'All agent scripts on/off'])('explains why %s conflicts with individual bindings without offering replacement', (mode) => {
  const save = vi.fn().mockResolvedValue(undefined)
  render(<ScriptFactorDialog scriptNodeId="script" graph={graph} factors={[{ name: 'Script enabled', level_type: 'boolean', levels: [false, true] }]} onClose={vi.fn()} onSave={save} onRemove={vi.fn()} />)
  const option = screen.getByRole('button', { name: mode })
  expect(option).toHaveClass('bg-secondary', 'text-secondary-foreground', 'border-dashed')
  expect(option).not.toHaveClass('opacity-50', 'text-muted-foreground')
  fireEvent.click(option)
  expect(option).toHaveClass('bg-primary', 'text-primary-foreground')
  expect(option).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
  expect(screen.getByRole('status')).toHaveTextContent('Script enabled')
  expect(screen.getByRole('status')).toHaveTextContent('remove those bindings in each Script’s factor dialog first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  expect(save).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'This script on/off' }))
  expect(screen.getByLabelText('Factor name')).toHaveValue('Script enabled')
  expect(screen.getByRole('button', { name: 'Remove factor' })).toBeEnabled()
})

it('only offers individual factors for disconnected scripts', () => {
  render(<ScriptFactorDialog scriptNodeId="script" graph={{ ...graph, edges: [] }} factors={[]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.getByRole('button', { name: 'This script on/off' })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(screen.getByRole('button', { name: 'Script levels' }))
  expect(screen.getByRole('status')).toHaveTextContent('Connect this Script to an Agent first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
})

it('blocks off treatments for a Tool Step that requires script_code', () => {
  const required = { ...graph, nodes: [
    { id: 'step', type: 'tool_step', data: { label: 'Fit model', config: { arguments: { code: { source: 'script_code' } } } } },
    { ...graph.nodes[1], data: { ...graph.nodes[1].data, factor_bindings: {} } },
  ], edges: [{ source: 'script', target: 'step', targetHandle: 'tool' }] } as unknown as ProtocolGraph
  render(<ScriptFactorDialog scriptNodeId="script" graph={required} factors={[]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.getByRole('status')).toHaveTextContent('cannot include off')
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
})

it.each(['This script on/off', 'Script levels'])('blocks %s when an Agent already has an all-scripts factor', (mode) => {
  const grouped = {
    ...graph,
    nodes: [
      { ...graph.nodes[0], data: { label: 'Writer', factor_bindings: { script_selection: 'Group' } } },
      { ...graph.nodes[1], data: { label: 'Summarize', config: { name: 'Scoring', language: 'python', code: 'print(1)' } } },
    ],
  } as ProtocolGraph
  render(<ScriptFactorDialog scriptNodeId="script" graph={grouped} factors={[{ name: 'Group', level_type: 'script_toggle', levels: [['script'], []], level_labels: ['All enabled', 'All disabled'] }]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: mode }))
  expect(screen.getByRole('status')).toHaveTextContent('Group')
  expect(screen.getByRole('status')).toHaveTextContent('remove it first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
})

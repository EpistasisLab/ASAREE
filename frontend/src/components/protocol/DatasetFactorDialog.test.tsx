import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import type { ProtocolGraph } from '@/types/protocols'
import { DatasetFactorDialog } from './DatasetFactorDialog'

const graph = {
  nodes: [
    { id: 'agent', type: 'agent', data: { label: 'Writer' } },
    { id: 'dataset', type: 'dataset', data: { label: 'Summarize', config: { dataset_id: 'a' }, factor_bindings: { 'config.enabled': 'Dataset enabled' } } },
  ],
  edges: [{ source: 'dataset', target: 'agent', targetHandle: 'dataset' }],
} as unknown as ProtocolGraph

it.each(['Dataset levels', 'All datasets on/off'])('explains why %s conflicts with individual bindings without offering replacement', (mode) => {
  const save = vi.fn().mockResolvedValue(undefined)
  render(<DatasetFactorDialog datasetNodeId="dataset" graph={graph} factors={[{ name: 'Dataset enabled', level_type: 'boolean', levels: [false, true] }]} onClose={vi.fn()} onSave={save} onRemove={vi.fn()} />)
  const option = screen.getByRole('button', { name: mode })
  expect(option).toHaveClass('bg-secondary', 'text-secondary-foreground', 'border-dashed')
  expect(option).not.toHaveClass('opacity-50', 'text-muted-foreground')
  fireEvent.click(option)
  expect(option).toHaveClass('bg-primary', 'text-primary-foreground')
  expect(option).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
  expect(screen.getByRole('status')).toHaveTextContent('Dataset enabled')
  expect(screen.getByRole('status')).toHaveTextContent('remove those bindings in each Dataset’s factor dialog first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  expect(save).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'This dataset on/off' }))
  expect(screen.getByLabelText('Factor name')).toHaveValue('Dataset enabled')
  expect(screen.getByRole('button', { name: 'Remove factor' })).toBeEnabled()
})

it('only offers individual factors for disconnected datasets', () => {
  render(<DatasetFactorDialog datasetNodeId="dataset" graph={{ ...graph, edges: [] }} factors={[]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.getByRole('button', { name: 'This dataset on/off' })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(screen.getByRole('button', { name: 'Dataset levels' }))
  expect(screen.getByRole('status')).toHaveTextContent('Connect this Dataset to an Agent first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
})

it.each(['This dataset on/off', 'Dataset levels'])('blocks %s when an Agent already has an all-datasets factor', (mode) => {
  const grouped = {
    ...graph,
    nodes: [
      { ...graph.nodes[0], data: { label: 'Writer', factor_bindings: { dataset_selection: 'Group' } } },
      { ...graph.nodes[1], data: { label: 'Summarize', config: { dataset_id: 'a' } } },
    ],
  } as ProtocolGraph
  render(<DatasetFactorDialog datasetNodeId="dataset" graph={grouped} factors={[{ name: 'Group', level_type: 'dataset_toggle', levels: [['a'], []], level_labels: ['All enabled', 'All disabled'] }]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: mode }))
  expect(screen.getByRole('status')).toHaveTextContent('Group')
  expect(screen.getByRole('status')).toHaveTextContent('remove it first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
})

it('saves edits to an existing individual factor without removing other declarations', async () => {
  const save = vi.fn().mockResolvedValue(undefined)
  render(<DatasetFactorDialog datasetNodeId="dataset" graph={graph} factors={[{ name: 'Dataset enabled', level_type: 'boolean', levels: [false, true] }]} onClose={vi.fn()} onSave={save} onRemove={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('Factor name'), { target: { value: 'Renamed' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith({ name: 'Renamed', level_type: 'boolean', levels: [false, true], level_labels: ['Disabled', 'Enabled'] }, 'dataset', 'Dataset enabled'))
})

it('opens the existing group factor when a shared dataset has multiple connected agents', () => {
  const sharedGraph = {
    ...graph,
    nodes: [
      { ...graph.nodes[0], data: { label: 'First agent' } },
      { id: 'second', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Second agent', dataset_selection: ['a'], dataset_factor_mode: 'dataset_toggle', factor_bindings: { dataset_selection: 'Group' } } },
      { ...graph.nodes[1], data: { label: 'Summarize', config: { dataset_id: 'a' } } },
    ],
    edges: [...graph.edges, { id: 'second-edge', source: 'dataset', target: 'second', targetHandle: 'dataset' }],
  } as ProtocolGraph
  render(<DatasetFactorDialog datasetNodeId="dataset" graph={sharedGraph} factors={[{ name: 'Group', level_type: 'dataset_toggle', levels: [['a'], []], level_labels: ['All enabled', 'All disabled'] }]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.getByRole('combobox')).toHaveTextContent('Second agent')
  expect(screen.getByRole('button', { name: 'All datasets on/off' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByLabelText('Factor name')).toHaveValue('Group')
})

it('offers removal of an older configuration factor before creating a connector factor', async () => {
  const remove = vi.fn().mockResolvedValue(undefined)
  const legacyGraph = { ...graph, nodes: [graph.nodes[0], { ...graph.nodes[1], data: { ...graph.nodes[1].data, factor_bindings: { config: 'Old dataset' } } }] }
  render(<DatasetFactorDialog datasetNodeId="dataset" graph={legacyGraph} factors={[{ name: 'Old dataset', level_type: 'dataset_config', levels: [{ dataset_id: 'a' }] }]} onClose={vi.fn()} onSave={vi.fn()} onRemove={remove} />)
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Remove configuration factor' }))
  await waitFor(() => expect(remove).toHaveBeenCalledWith('Old dataset'))
})

it('explains why per-row inputs cannot be factorized', () => {
  const rows = { ...graph, edges: [{ ...graph.edges[0], data: { dataset_input: { mode: 'per_row' as const, columns: ['question'] } } }] }
  render(<DatasetFactorDialog datasetNodeId="dataset" graph={rows} factors={[]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.getByRole('status')).toHaveTextContent('Whole dataset')
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
})

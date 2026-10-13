import { useState, type ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { datasetsApi } from '@/api/client'
import type { Dataset } from '@/types/datasets'
import { nodeDataForDataset } from './datasetCatalog'
import { DatasetNodeInspector } from './DatasetNodeInspector'
import { RegisterDatasetDialog } from './RegisterDatasetDialog'

vi.mock('@/components/ui/dialog', () => {
  const Container = ({ children }: { children: ReactNode }) => <div>{children}</div>
  return {
    Dialog: ({ open, children }: { open: boolean; children: ReactNode }) => open ? <div>{children}</div> : null,
    DialogContent: Container, DialogDescription: Container, DialogFooter: Container,
    DialogHeader: Container, DialogTitle: Container,
  }
})
vi.mock('./NodeInspectorDialog', () => ({
  NodeInspectorDialog: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))
vi.mock('./FactorBindableField', () => ({
  FactorBindableField: ({ children }: { children: (trigger: ReactNode) => ReactNode }) => children(null),
}))
vi.mock('./SplitDatasetDialog', () => ({ SplitDatasetDialog: () => null }))

afterEach(() => vi.restoreAllMocks())

it('shows the split action immediately after registration while the list refresh is pending', async () => {
  const dataset: Dataset = {
    id: 'dc6a940a-2de2-4639-b58d-17bd5ac9c045', name: 'New dataset',
    raw_path: '/data/datasets/new/raw.csv', raw_sha256: 'a'.repeat(64),
    train_path: null, test_path: null, train_sha256: null, test_sha256: null,
    split_method: null, split_group_column: null, split_test_size: null, split_seed: null,
    target_column: null, description: null, dictionary_json: null, created_at: null,
  }
  vi.spyOn(datasetsApi, 'create').mockResolvedValue(dataset)
  vi.spyOn(datasetsApi, 'list').mockImplementation(() => new Promise(() => {}))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(['datasets'], [{ ...dataset, id: 'existing', name: 'Existing dataset' }])

  function Handoff() {
    const [created, setCreated] = useState<Dataset | null>(null)
    return created ? (
      <DatasetNodeInspector
        node={{ id: 'node', type: 'dataset', position: { x: 0, y: 0 }, data: nodeDataForDataset(created) }}
        experimentId={null} factorNodeLabel="Dataset"
        onChange={() => {}} onDelete={() => {}} onClose={() => {}}
      />
    ) : <RegisterDatasetDialog open onOpenChange={() => {}} onCreated={setCreated} />
  }

  render(<QueryClientProvider client={client}><Handoff /></QueryClientProvider>)
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: dataset.name } })
  fireEvent.change(document.getElementById('dataset-file')!, {
    target: { files: [new File(['value\n1\n'], 'source.csv', { type: 'text/csv' })] },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Register dataset' }))

  expect(await screen.findByRole('button', { name: 'Split dataset' })).toBeTruthy()
  expect(screen.getByText(/has moved to the node toolbar/)).toHaveTextContent('Hover over the Dataset node')
  expect(client.getQueryData<Dataset[]>(['datasets'])?.map((d) => d.id)).toEqual(['existing', dataset.id])
  client.clear()
})

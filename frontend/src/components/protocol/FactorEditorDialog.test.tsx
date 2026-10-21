import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'
import type { DesignFactor } from '@/types/experiments'
import { FactorEditorDialog } from './FactorEditorDialog'

const factorA: DesignFactor = { name: 'Model parameter', level_type: 'number', levels: [2048, 1024], level_labels: ['high', 'low'] }
const factorB: DesignFactor = { ...factorA, levels: [1024, 2048], level_labels: ['low', 'high'] }

it.each([null, '01-analyst'])('uploads to the intended persona level when both levels share persona_id %s', async (personaId) => {
  const save = vi.fn().mockResolvedValue(undefined)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <FactorEditorDialog open onOpenChange={vi.fn()} onSave={save} factor={{
        name: 'Persona', level_type: 'persona_text', levels: [
          { mode: 'text', persona_text: 'First treatment', persona_id: personaId },
          { mode: 'text', persona_text: 'Second treatment', persona_id: personaId },
        ],
      }} />
    </QueryClientProvider>,
  )
  const inputs = screen.getAllByLabelText<HTMLInputElement>('Upload persona (.md)')
  const firstClick = vi.spyOn(inputs[0], 'click')
  const secondClick = vi.spyOn(inputs[1], 'click')
  fireEvent.click(screen.getAllByRole('button', { name: 'Choose .md file' })[1])
  expect(secondClick).toHaveBeenCalledOnce()
  expect(firstClick).not.toHaveBeenCalled()

  const file = new File(['You are methodical.'], 'methodical.md', { type: 'text/markdown' })
  Object.defineProperty(file, 'text', { value: vi.fn().mockResolvedValue('You are methodical.') })
  fireEvent.change(inputs[1], { target: { files: [file] } })
  await waitFor(() => expect(screen.getByRole('button', { name: 'methodical.md' })).toBeInTheDocument())
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({
    levels: [
      expect.objectContaining({ persona_text: 'First treatment' }),
      expect.objectContaining({ persona_text: 'You are methodical.', persona_file: 'methodical.md', mode: 'file' }),
    ],
  }), undefined))
})

it.each(['tool_selection', 'dataset_selection', 'skill_selection', 'knowledge_selection', 'script_selection', 'sub_agent_selection'] as const)(
  'refreshes untouched %s editors and preserves level-label pairs', async (level_type) => {
    const save = vi.fn().mockResolvedValue(undefined)
    const props = { onSave: save, onOpenChange: vi.fn() }
    const factor = { name: 'Selection', level_type, levels: [['a'], ['b']], level_labels: ['first', 'second'] }
    const refreshed = { ...factor, levels: [['b'], ['a']], level_labels: ['second', 'first'] }
    const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } })
    for (const key of ['datasets', 'skills', 'knowledge-factor-library']) client.setQueryData([key], [])
    const { rerender } = render(<FactorEditorDialog {...props} factor={factor} open={false} />, {
      wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>,
    })
    rerender(<FactorEditorDialog {...props} factor={refreshed} open />)
    expect(screen.getByLabelText('Level 1 label')).toHaveValue('second')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(save).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Level 1 label'), { target: { value: 'renamed' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
    await waitFor(() => expect(save).toHaveBeenCalledExactlyOnceWith({ ...refreshed, level_labels: ['renamed', 'first'] }))
  },
)

it('does not save after a refetch followed by opening and closing without edits', () => {
  const save = vi.fn()
  const props = { onSave: save, onOpenChange: vi.fn() }
  const { rerender } = render(<FactorEditorDialog {...props} factor={factorA} open={false} />)
  rerender(<FactorEditorDialog {...props} factor={factorB} open={false} />)
  rerender(<FactorEditorDialog {...props} factor={factorB} open />)
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  expect(save).not.toHaveBeenCalled()
})

it('refreshes an untouched open dialog without writing stale levels on close', () => {
  const save = vi.fn()
  const props = { onSave: save, onOpenChange: vi.fn(), open: true }
  const { rerender } = render(<FactorEditorDialog {...props} factor={factorA} />)
  rerender(<FactorEditorDialog {...props} factor={factorB} />)
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  expect(save).not.toHaveBeenCalled()
  expect(screen.getByLabelText('Level 1 label')).toHaveValue('low')
})

it('saves one label edit with the refreshed levels still paired correctly', async () => {
  const save = vi.fn().mockResolvedValue(undefined)
  const props = { onSave: save, onOpenChange: vi.fn(), open: true }
  const { rerender } = render(<FactorEditorDialog {...props} factor={factorA} />)
  rerender(<FactorEditorDialog {...props} factor={factorB} />)
  fireEvent.change(screen.getByLabelText('Level 1 label'), { target: { value: 'small' } })
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  await waitFor(() => expect(save).toHaveBeenCalledExactlyOnceWith({ ...factorB, level_labels: ['small', 'high'] }, undefined))
})

it('preserves a pending user edit when a refetch arrives', async () => {
  const save = vi.fn().mockResolvedValue(undefined)
  const props = { onSave: save, onOpenChange: vi.fn(), open: true }
  const { rerender } = render(<FactorEditorDialog {...props} factor={factorA} />)
  fireEvent.change(screen.getByLabelText('Level 1 label'), { target: { value: 'large' } })
  rerender(<FactorEditorDialog {...props} factor={factorB} />)
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  await waitFor(() => expect(save).toHaveBeenCalledExactlyOnceWith({ ...factorA, level_labels: ['large', 'low'] }, undefined))
})

it('reseeds on reopening and never autosaves an untouched refetch', async () => {
  vi.useFakeTimers()
  try {
    const save = vi.fn()
    const props = { onSave: save, onOpenChange: vi.fn() }
    const { rerender } = render(<FactorEditorDialog {...props} factor={factorA} open />)
    rerender(<FactorEditorDialog {...props} factor={factorA} open={false} />)
    rerender(<FactorEditorDialog {...props} factor={factorB} open />)
    await act(() => vi.advanceTimersByTimeAsync(1000))
    expect(screen.getByLabelText('Level 1 label')).toHaveValue('low')
    expect(save).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(save).not.toHaveBeenCalled()
  } finally {
    vi.useRealTimers()
  }
})

it('can save the same edit again after an acknowledged save and external reset', async () => {
  const save = vi.fn().mockResolvedValue(undefined)
  const props = { onSave: save, onOpenChange: vi.fn(), open: true }
  const edited = { ...factorA, level_labels: ['large', 'low'] }
  const { rerender } = render(<FactorEditorDialog {...props} factor={factorA} />)
  fireEvent.change(screen.getByLabelText('Level 1 label'), { target: { value: 'large' } })
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
  rerender(<FactorEditorDialog {...props} factor={edited} />)
  rerender(<FactorEditorDialog {...props} factor={factorA} />)
  expect(screen.getByLabelText('Level 1 label')).toHaveValue('high')
  fireEvent.change(screen.getByLabelText('Level 1 label'), { target: { value: 'large' } })
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2))
})

it('recognizes a normalized save as clean before applying a later external change', async () => {
  const save = vi.fn().mockResolvedValue(undefined)
  const props = { onSave: save, onOpenChange: vi.fn(), open: true }
  const { rerender } = render(<FactorEditorDialog {...props} factor={factorA} />)
  fireEvent.change(screen.getByLabelText('Level 1 label'), { target: { value: ' large ' } })
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
  rerender(<FactorEditorDialog {...props} factor={{ ...factorA, level_labels: ['large', 'low'] }} />)
  rerender(<FactorEditorDialog {...props} factor={factorB} />)
  expect(screen.getByLabelText('Level 1 label')).toHaveValue('low')
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  expect(save).toHaveBeenCalledTimes(1)
})

import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import type { DesignFactor } from '@/types/experiments'
import { ModelFactorGroup } from './ModelFactorGroup'

const { edit, updateInternals } = vi.hoisted(() => ({ edit: vi.fn(), updateInternals: vi.fn() }))
vi.mock('@xyflow/react', () => ({
  Handle: ({ id }: { id: string }) => <div data-testid="handle">{id}</div>,
  Position: { Top: 'top' },
  useUpdateNodeInternals: () => updateInternals,
}))
vi.mock('../ProtocolCanvasContext', () => ({ useProtocolCanvasActions: () => ({ requestModelFactor: edit }) }))
vi.mock('./NodeHoverToolbar', () => ({ NodeHoverToolbar: () => null }))
vi.mock('../useProviderModels', () => ({ useProviderModels: () => ({ models: [], modelsQuery: {} }) }))

const factor: DesignFactor = {
  name: 'Writer:Models', level_type: 'model_config', level_labels: ['Baseline', 'Alternative'],
  levels: [
    { provider: 'openai', model: 'gpt-5', temperature: 0.7, max_tokens: 1000 },
    { provider: 'anthropic', model: 'claude-sonnet', temperature: 1, max_tokens: 2000 },
  ],
}

beforeEach(() => { localStorage.clear(); vi.clearAllMocks() })

it('expands derived configurations with one handle and opens the selected level', () => {
  render(<ModelFactorGroup id="model" factor={factor} />)
  expect(screen.getByText('Model factor · 2 levels')).toBeInTheDocument()
  expect(screen.getByText('OpenAI')).toBeInTheDocument()
  expect(screen.getByText('Anthropic')).toBeInTheDocument()
  expect(screen.queryByText('model=gpt-5')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Expand model levels' }))
  expect(screen.getByText('model=gpt-5')).toBeInTheDocument()
  expect(screen.getByText('model=claude-sonnet')).toBeInTheDocument()
  expect(screen.getByText(/default test selection/)).toBeInTheDocument()
  expect(screen.getAllByTestId('handle')).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: 'Edit model level 2: Alternative' }))
  expect(edit).toHaveBeenCalledWith('model', 1)
  expect(updateInternals).toHaveBeenCalledWith('model')
})

it('remembers expansion per node and keeps same-provider configurations separate', () => {
  const { unmount } = render(<ModelFactorGroup id="model" factor={factor} />)
  fireEvent.click(screen.getByRole('button', { name: 'Expand model levels' }))
  unmount()
  render(<ModelFactorGroup id="model" factor={{ ...factor, levels: [factor.levels[0], { provider: 'openai', model: 'gpt-5', temperature: 1, max_tokens: 2000 }] }} />)
  expect(screen.getByRole('button', { name: 'Collapse model levels' })).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getAllByText('model=gpt-5')).toHaveLength(2)
  expect(screen.getAllByTestId('handle')).toHaveLength(1)
})

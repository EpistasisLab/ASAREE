import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import type { ModelNodeData, ProtocolNode } from '@/types/protocols'
import { factorCreationFields } from './bindableFields'
import { ModelFactorDialog } from './ModelFactorDialog'
import { LlmConfigLevelRow } from './FactorEditorDialog'

vi.mock('./useProviderModels', () => ({ useProviderModels: (_provider: string, model?: string) => ({ models: [
  { id: 'gpt-5', label: 'GPT 5', supports_temperature: true, supports_effort: false, effort_levels: [] },
  { id: 'reasoning', label: 'Reasoning model', supports_temperature: false, supports_effort: true, effort_levels: ['low', 'high'] },
], capabilities: model === 'custom-id' ? {
  supports_temperature: false, supports_effort: true,
  effort_levels: ['low', 'medium', 'high'], default_effort: 'medium',
} : undefined, modelsQuery: { isLoading: false } }) }))

const node: ProtocolNode & { data: ModelNodeData } = {
  id: 'model', type: 'model_openai', position: { x: 0, y: 0 },
  data: { label: 'OpenAI', config: { provider: 'openai', model: 'gpt-5', temperature: 0.7, max_tokens: 1000 } },
}

it('routes New Factor model choices to the dedicated dialog while retaining their node grouping', () => {
  const fields = factorCreationFields([node], [])
  expect(fields).toHaveLength(5)
  expect(fields.find((field) => field.fieldPath === 'config')).toMatchObject({
    fieldLabel: 'Model levels', connectorFactor: { kind: 'model', nodeId: 'model', mode: 'model_config' },
    pickerGroup: { category: 'Model', componentId: 'model', componentLabel: 'OpenAI' },
  })
  expect(fields.every((field) => field.connectorFactor?.kind === 'model')).toBe(true)
})

it('blocks whole-model factors when individual fields are already bound, and allows editing that field', () => {
  const bound = { ...node, data: { ...node.data, factor_bindings: { 'config.temperature': 'Heat' } } }
  render(<ModelFactorDialog node={bound} nodeLabel="Writer:OpenAI" factors={[{ name: 'Heat', level_type: 'number', levels: [0.7, 1] }]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: 'Model levels' }))
  expect(screen.getByRole('status')).toHaveTextContent('cannot be combined')
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Temperature' }))
  expect(screen.getByLabelText('Factor name')).toHaveValue('Heat')
  expect(screen.getByRole('button', { name: 'Remove factor' })).toBeEnabled()
})

it('saves reordered numeric levels with their labels and previous factor name', async () => {
  const save = vi.fn().mockResolvedValue(undefined)
  const bound = { ...node, data: { ...node.data, factor_bindings: { 'config.temperature': 'Heat' } } }
  render(<ModelFactorDialog node={bound} nodeLabel="Writer:OpenAI" factors={[{ name: 'Heat', level_type: 'number', levels: [0.7, 1], level_labels: ['Low', 'High'] }]} onClose={vi.fn()} onSave={save} onRemove={vi.fn()} />)
  fireEvent.click(screen.getAllByRole('button', { name: 'Move level up' })[1])
  fireEvent.change(screen.getByLabelText('Factor name'), { target: { value: 'Temperature' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith({ name: 'Temperature', level_type: 'number', levels: [1, 0.7], level_labels: ['High', 'Low'] }, 'config.temperature', 'Heat'))
})

it('removes an existing model factor through its bound path', async () => {
  const remove = vi.fn().mockResolvedValue(undefined)
  const close = vi.fn()
  const bound = { ...node, data: { ...node.data, factor_bindings: { config: 'Models' } } }
  const { rerender } = render(<ModelFactorDialog node={bound} nodeLabel="Writer:OpenAI" factors={[{ name: 'Models', level_type: 'model_config', levels: [node.data.config, { ...node.data.config, model: 'gpt-4.1' }], level_labels: ['First', 'Second'] }]} onClose={close} onSave={vi.fn()} onRemove={remove} />)
  fireEvent.click(screen.getByRole('button', { name: 'Remove factor' }))
  await waitFor(() => expect(remove).toHaveBeenCalledWith('Models', 'config'))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled())
  expect(close).not.toHaveBeenCalled()
  rerender(<ModelFactorDialog node={node} nodeLabel="Writer:OpenAI" factors={[]} onClose={close} onSave={vi.fn()} onRemove={remove} />)
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Remove factor' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Temperature' }))
  expect(screen.getByLabelText('Factor name')).toBeEnabled()
})

it('uses the shared model dropdown and disables models selected in other levels', async () => {
  render(<ModelFactorDialog node={node} nodeLabel="Writer:OpenAI" initialFieldPath="config.model" factors={[]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.getByRole('combobox', { name: 'Level 1 model' })).toHaveTextContent('GPT 5')
  expect(screen.getByRole('combobox', { name: 'Level 2 model' })).toHaveTextContent('Select a model')
  expect(screen.getByRole('button', { name: 'Temperature' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Effort' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('combobox', { name: 'Level 2 model' }))
  expect(await screen.findByRole('option', { name: 'GPT 5' })).toHaveAttribute('aria-disabled', 'true')
  expect(screen.getByRole('option', { name: 'Reasoning model' })).not.toHaveAttribute('aria-disabled', 'true')
})

it('offers only Effort for a reasoning model and disables efforts selected in other levels', async () => {
  const reasoning = { ...node, data: { ...node.data, config: { ...node.data.config, model: 'reasoning', effort: 'low' } } }
  render(<ModelFactorDialog node={reasoning} nodeLabel="Writer:OpenAI" initialFieldPath="config.effort" factors={[]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.queryByRole('button', { name: 'Temperature' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Effort' })).toBeInTheDocument()
  expect(screen.getByRole('combobox', { name: 'Level 1 effort' })).toHaveTextContent('low')
  fireEvent.click(screen.getByRole('combobox', { name: 'Level 2 effort' }))
  expect(await screen.findByRole('option', { name: 'low' })).toHaveAttribute('aria-disabled', 'true')
  expect(screen.getByRole('option', { name: 'high' })).not.toHaveAttribute('aria-disabled', 'true')
})

it('updates whole-model level controls when its selected model changes', () => {
  const { rerender } = render(<LlmConfigLevelRow value={{ provider: 'openai', model: 'gpt-5' }} onChange={vi.fn()} />)
  expect(screen.getByText('Temperature')).toBeInTheDocument()
  expect(screen.queryByText('Effort')).not.toBeInTheDocument()
  rerender(<LlmConfigLevelRow value={{ provider: 'openai', model: 'reasoning' }} onChange={vi.fn()} />)
  expect(screen.queryByText('Temperature')).not.toBeInTheDocument()
  expect(screen.getByText('Effort')).toBeInTheDocument()
  rerender(<LlmConfigLevelRow value={{ provider: 'openai', model: 'custom-id' }} onChange={vi.fn()} />)
  expect(screen.queryByText('Temperature')).not.toBeInTheDocument()
  expect(screen.getByText('Effort')).toBeInTheDocument()
  expect(screen.getByText('Default (medium)')).toBeInTheDocument()
})

it('warns about duplicate model configurations regardless of property order and clears when changed', () => {
  const bound = { ...node, data: { ...node.data, factor_bindings: { config: 'Models' } } }
  render(<ModelFactorDialog node={bound} nodeLabel="Writer:OpenAI" factors={[{
    name: 'Models', level_type: 'model_config', level_labels: ['First', 'Second'],
    levels: [node.data.config, { max_tokens: 1000, temperature: 0.7, model: 'gpt-5', provider: 'openai' }],
  }]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.getByRole('alert')).toHaveTextContent('Duplicate levels: 1, 2')
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
  const temperatures = screen.getAllByRole('spinbutton')
  fireEvent.change(temperatures[2], { target: { value: '1' } })
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeEnabled()
})

it.each([
  ['config.temperature', '0.70', '1'],
  ['config.max_tokens', '1000.0', '2000'],
])('warns about duplicate numeric values for %s and clears after correction', (path, duplicate, distinct) => {
  render(<ModelFactorDialog node={node} nodeLabel="Writer:OpenAI" initialFieldPath={path} factors={[]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('Level 2 value'), { target: { value: duplicate } })
  expect(screen.getByRole('alert')).toHaveTextContent('Duplicate levels: 1, 2')
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Level 2 value'), { target: { value: distinct } })
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeEnabled()
})

it('does not treat an empty numeric level as a duplicate of zero', () => {
  render(<ModelFactorDialog node={node} nodeLabel="Writer:OpenAI" initialFieldPath="config.temperature" factors={[]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('Level 1 value'), { target: { value: '0' } })
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Level 2 value'), { target: { value: '0.0' } })
  expect(screen.getByRole('alert')).toHaveTextContent('Duplicate levels: 1, 2')
})

it('focuses the level selected from a canvas preview', async () => {
  const bound = { ...node, data: { ...node.data, factor_bindings: { config: 'Models' } } }
  render(<ModelFactorDialog node={bound} nodeLabel="Writer:OpenAI" initialFieldPath="config" initialLevelIndex={1} factors={[{
    name: 'Models', level_type: 'model_config', level_labels: ['First', 'Second'],
    levels: [node.data.config, { ...node.data.config, model: 'reasoning' }],
  }]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  await waitFor(() => expect(screen.getByLabelText('Level 2 label')).toHaveFocus())
})

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { EMPTY_SCOPE } from '@/lib/promptReferences'
import { defaultAgentNodeData, type AgentNodeData, type ProtocolNode } from '@/types/protocols'
import { AgentFactorDialog } from './AgentFactorDialog'
import { factorCreationFields } from './bindableFields'

const node: ProtocolNode & { data: AgentNodeData } = {
  id: 'writer', type: 'agent', position: { x: 0, y: 0 },
  data: { ...defaultAgentNodeData('Writer'), config: { ...defaultAgentNodeData().config, prompt: 'Summarize {{node:source}}' } },
}

it('routes the three Agent choices from New Factor and keeps Sub-Agent choices separate', () => {
  const fields = factorCreationFields([node], [])
  expect(fields.map((field) => field.fieldLabel)).toEqual(['This agent on/off', 'Prompt levels', 'System prompt levels'])
  expect(fields.every((field) => field.connectorFactor?.kind === 'agent' && field.pickerGroup?.category === 'Agent' && field.pickerGroup?.componentId === node.id)).toBe(true)
  const subAgentFields = factorCreationFields([{ ...node, type: 'sub_agent' }], [])
  expect(subAgentFields.some((field) => field.connectorFactor?.kind === 'agent')).toBe(false)
})

it('saves this agent on/off without changing its current activity', async () => {
  const save = vi.fn().mockResolvedValue(undefined)
  render(<AgentFactorDialog node={node} factors={[]} referenceScope={EMPTY_SCOPE} onClose={vi.fn()} onSave={save} onRemove={vi.fn()} />)
  expect(screen.queryByRole('button', { name: 'Agent levels' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'All agents on/off' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith({ name: 'Writer:Enabled', level_type: 'boolean', levels: [false, true], level_labels: ['Disabled', 'Enabled'] }, 'active', undefined))
  expect(node.data.active).not.toBe(false)
})

it('keeps prompt references in storage form while reordering levels and their labels', async () => {
  const save = vi.fn().mockResolvedValue(undefined)
  const referenceScope = { targets: [{ id: 'source', name: 'Researcher' }], names: { source: 'Researcher' } }
  render(<AgentFactorDialog node={node} factors={[]} referenceScope={referenceScope} initialFieldPath="config.prompt" onClose={vi.fn()} onSave={save} onRemove={vi.fn()} />)
  expect(screen.getByLabelText('Level 1 prompt')).toHaveValue('Summarize {{Researcher}}')
  fireEvent.change(screen.getByLabelText('Level 2 prompt'), { target: { value: 'Analyze {{Researcher}}' } })
  fireEvent.change(screen.getByLabelText('Level 2 label'), { target: { value: 'Analysis' } })
  fireEvent.click(screen.getAllByRole('button', { name: 'Move level up' })[1])
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ levels: ['Analyze {{node:source}}', 'Summarize {{node:source}}'], level_labels: ['Analysis', 'level1'] }), 'config.prompt', undefined))
})

it('supports an empty system prompt level and blocks duplicate values and labels', () => {
  render(<AgentFactorDialog node={node} factors={[]} referenceScope={EMPTY_SCOPE} initialFieldPath="config.system_prompt" onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Level 2 system prompt'), { target: { value: 'Be concise.' } })
  expect(screen.getByLabelText('Level 1 system prompt')).toHaveValue('')
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeEnabled()
  fireEvent.change(screen.getByLabelText('Level 2 label'), { target: { value: 'level1' } })
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
})

it('edits legacy prompt bindings and passes the previous factor name on save', async () => {
  const save = vi.fn().mockResolvedValue(undefined)
  const bound = { ...node, data: { ...node.data, factor_bindings: { 'config.prompt': 'Prompts' } } }
  render(<AgentFactorDialog node={bound} factors={[{ name: 'Prompts', levels: ['Original', 'Alternate'] }]} referenceScope={EMPTY_SCOPE} onClose={vi.fn()} onSave={save} onRemove={vi.fn()} />)
  expect(screen.getByRole('button', { name: 'Prompt levels' })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.change(screen.getByLabelText('Factor name'), { target: { value: 'Tasks' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ name: 'Tasks', level_type: 'text', levels: ['Original', 'Alternate'] }), 'config.prompt', 'Prompts'))
})

it('removes a factor through its selected field without closing the dialog', async () => {
  const remove = vi.fn().mockResolvedValue(undefined), close = vi.fn()
  const bound = { ...node, data: { ...node.data, factor_bindings: { active: 'Available' } } }
  render(<AgentFactorDialog node={bound} factors={[{ name: 'Available', levels: [true, false] }]} referenceScope={EMPTY_SCOPE} onClose={close} onSave={vi.fn()} onRemove={remove} />)
  fireEvent.click(screen.getByRole('button', { name: 'Remove factor' }))
  await waitFor(() => expect(remove).toHaveBeenCalledWith('Available', 'active'))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled())
  expect(close).not.toHaveBeenCalled()
})

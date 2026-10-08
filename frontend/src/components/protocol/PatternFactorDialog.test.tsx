import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import type { ProtocolGraph, ProtocolNode } from '@/types/protocols'
import { defaultAgentNodeData, defaultReasonActPatternNodeData, defaultSingleAgentBaselinePatternNodeData } from '@/types/protocols'
import { PatternFactorDialog } from './PatternFactorDialog'
import { factorCreationFields } from './bindableFields'
import { patternFactorConflict } from './patternFactors'

const pattern: ProtocolNode = { id: 'pattern', type: 'pattern_reason_act', position: { x: 0, y: 0 }, data: defaultReasonActPatternNodeData() }
const agent: ProtocolNode = { id: 'agent', type: 'agent', position: { x: 0, y: 100 }, data: defaultAgentNodeData('Writer') }
const graph: ProtocolGraph = { nodes: [pattern, agent], edges: [{ id: 'edge', source: 'pattern', target: 'agent', targetHandle: 'architectural_pattern' }] }
const props = { patternNodeId: 'pattern', graph, nodeLabel: 'Writer:Reason + Act', factors: [], onClose: vi.fn(), onSave: vi.fn(), onRemove: vi.fn() }

describe('Pattern factors', () => {
  it('groups whole-pattern and parameter choices under the Pattern node in New Factor', () => {
    const fields = factorCreationFields(graph.nodes, graph.edges)
    const whole = fields.find((field) => field.fieldPath === 'pattern_override')!
    expect(whole).toMatchObject({ nodeId: 'agent', fieldLabel: 'Pattern levels', connectorFactor: { kind: 'pattern', nodeId: 'pattern', agentId: 'agent' }, pickerGroup: { id: 'agent', category: 'Pattern', componentId: 'pattern', componentLabel: 'Reason + Act' } })
    expect(fields.filter((field) => field.nodeId === 'pattern')).toHaveLength(4)
    expect(fields.filter((field) => field.nodeId === 'pattern').every((field) => field.connectorFactor?.kind === 'pattern')).toBe(true)
  })

  it.each(['pattern_reason_act', 'pattern_single_agent_baseline'] as const)('seeds %s and its alternate and saves on the Agent', async (type) => {
    const save = vi.fn().mockResolvedValue(undefined)
    const config = type === 'pattern_reason_act' ? defaultReasonActPatternNodeData() : defaultSingleAgentBaselinePatternNodeData()
    const nextGraph = { ...graph, nodes: [{ ...pattern, type, data: config }, agent] }
    render(<PatternFactorDialog {...props} graph={nextGraph} onSave={save} />)
    expect(screen.getByRole('dialog', { name: 'Pattern factor' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save factor' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ level_type: 'pattern', levels: [expect.objectContaining({ pattern_params: expect.objectContaining({ [type.slice(8)]: config.config }) }), expect.any(Object)] }), 'pattern_override', 'agent', undefined))
  })

  it('blocks whole-pattern factors while allowing existing parameter factors to be edited and removed', async () => {
    const remove = vi.fn().mockResolvedValue(undefined)
    const nextGraph = { ...graph, nodes: [{ ...pattern, data: { ...pattern.data, factor_bindings: { 'config.max_iterations': 'Budget' } } }, agent] }
    render(<PatternFactorDialog {...props} graph={nextGraph} factors={[{ name: 'Budget', level_type: 'number', levels: [10, 30] }]} onRemove={remove} />)
    fireEvent.click(screen.getByRole('button', { name: 'Pattern levels' }))
    expect(screen.getByRole('status')).toHaveTextContent('cannot be combined')
    expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Max iterations' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove factor' }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith('Budget', 'config.max_iterations', 'pattern'))
    expect(factorCreationFields(nextGraph.nodes, nextGraph.edges).some((field) => field.fieldPath === 'pattern_override')).toBe(false)
  })

  it('blocks parameters controlled by an Agent override, including shared Pattern connections', () => {
    const otherAgent = { ...agent, id: 'other', data: { ...defaultAgentNodeData('Other'), factor_bindings: { pattern_override: 'Patterns' } } }
    const nextGraph = { ...graph, nodes: [...graph.nodes, otherAgent], edges: [...graph.edges, { id: 'other-edge', source: 'pattern', target: 'other', targetHandle: 'architectural_pattern' }] }
    expect(patternFactorConflict('pattern', 'config.max_iterations', nextGraph.nodes, nextGraph.edges)).toContain('cannot be combined')
    render(<PatternFactorDialog {...props} graph={nextGraph} factors={[{ name: 'Patterns', level_type: 'pattern', levels: [{ execution_pattern: 'reason_act', pattern_params: { reason_act: pattern.data.config } }] }]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Max iterations' }))
    expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
  })

  it('requires an Agent for Pattern levels while permitting orphan parameter factors', () => {
    render(<PatternFactorDialog {...props} graph={{ nodes: [pattern], edges: [] }} />)
    expect(screen.getByRole('status')).toHaveTextContent('Connect this node to an Agent')
    fireEvent.click(screen.getByRole('button', { name: 'Include scratchpad' }))
    expect(screen.getByRole('button', { name: 'Save factor' })).toBeEnabled()
  })

  it('preserves numeric labels when reordered and rejects duplicate and invalid budgets', async () => {
    const save = vi.fn().mockResolvedValue(undefined)
    render(<PatternFactorDialog {...props} initialFieldPath="config.max_iterations" onSave={save} />)
    fireEvent.change(screen.getByLabelText('Level 2 value'), { target: { value: '30' } })
    expect(screen.getByRole('alert')).toHaveTextContent('Duplicate levels')
    fireEvent.change(screen.getByLabelText('Level 2 value'), { target: { value: '0' } })
    expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Level 2 value'), { target: { value: '10' } })
    fireEvent.change(screen.getByLabelText('Level 1 label'), { target: { value: 'Long' } })
    fireEvent.change(screen.getByLabelText('Level 2 label'), { target: { value: 'Short' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Move level up' })[1])
    fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ levels: [10, 30], level_labels: ['Short', 'Long'] }), 'config.max_iterations', 'pattern', undefined))
  })
})

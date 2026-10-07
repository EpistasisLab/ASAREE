import { expect, it } from 'vitest'
import type { Edge, Node } from '@xyflow/react'
import { factorCreationFields, groupFactorCreationFields } from './bindableFields'

it('sorts agent groups, component groups, and factor choices alphabetically', () => {
  const nodes: Node[] = [
    { id: 'z', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Zebra', config: {} } },
    { id: 'a', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Alpha', config: {} } },
    { id: 'shared', type: 'skill', position: { x: 0, y: 0 }, data: { label: 'Shared skill', config: { skill_id: 's' } } },
    { id: 'data', type: 'dataset', position: { x: 0, y: 0 }, data: { label: 'Cohort', config: { dataset_id: 'd' } } },
    { id: 'loose', type: 'skill', position: { x: 0, y: 0 }, data: { label: 'Unconnected skill', config: { skill_id: 'u' } } },
  ]
  const edges: Edge[] = [
    { id: 's-z', source: 'shared', target: 'z', targetHandle: 'skill' },
    { id: 's-a', source: 'shared', target: 'a', targetHandle: 'skill' },
    { id: 'd-a', source: 'data', target: 'a', targetHandle: 'dataset' },
  ]
  const groups = groupFactorCreationFields(factorCreationFields(nodes, edges))
  expect(groups.map(group => group.label)).toEqual(['Alpha', 'Shared or unconnected components', 'Zebra'])
  expect(groups[0].connectors.map(connector => connector.label)).toEqual(['Agent fields', 'Datasets', 'Skills'])
  expect(groups[0].connectors.find(connector => connector.label === 'Datasets')?.components.map(component => component.label)).toEqual(['Cohort', 'Datasets on this agent'])
  expect(groups[0].connectors.find(connector => connector.label === 'Skills')?.components[0].fields.map(field => field.fieldLabel)).toEqual(['All skills on/off', 'Skill levels'])
  expect(groups[1].connectors[0].components.map(component => component.label)).toEqual(['Shared skill', 'Unconnected skill'])
})

it('uses connector labels for every model and MCP family and orders components and fields by name', () => {
  const kinds = ['mcp_tool', 'model_openai', 'mcp_client_tool', 'model_local', 'mcp_scikit_learn', 'model_anthropic', 'model_openrouter', 'model_azure_foundry', 'pattern_reason_act']
  const nodes: Node[] = [
    { id: 'agent', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Writer' } },
    ...kinds.map((kind) => ({ id: kind, type: kind, position: { x: 0, y: 0 }, data: { label: kind, config: {} } })),
  ]
  const edges: Edge[] = kinds.map((kind) => ({ id: kind, source: kind, target: 'agent' }))
  const groups = groupFactorCreationFields(factorCreationFields(nodes, edges))
  const connectors = groups[0].connectors
  expect(connectors.map((connector) => connector.label)).toEqual(['Agent fields', 'Model', 'Pattern', 'Tools'])
  expect(connectors.find((connector) => connector.label === 'Model')?.components.map((component) => component.label)).toEqual(['model_anthropic', 'model_azure_foundry', 'model_local', 'model_openai', 'model_openrouter'])
  expect(connectors.find((connector) => connector.label === 'Pattern')?.components[0].fields.map((field) => field.fieldLabel)).toEqual(['Include scratchpad', 'Max iterations', 'Observation format', 'Scratchpad window'])
})

it('routes both knowledge kinds through the dialog and offers connector modes once per agent', () => {
  const nodes: Node[] = [
    { id: 'agent', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Researcher' } },
    { id: 'bundle', type: 'okf_bundle', position: { x: 0, y: 0 }, data: { label: 'Bundle', config: { bundle_id: 'b' } } },
    { id: 'doc', type: 'okf_document', position: { x: 0, y: 0 }, data: { label: 'Document', config: { document_id: 'd' } } },
  ]
  const edges: Edge[] = ['bundle', 'doc'].map((id) => ({ id, source: id, target: 'agent', targetHandle: 'knowledge' }))
  const fields = factorCreationFields(nodes, edges).filter((field) => field.connectorFactor?.kind === 'knowledge')
  expect(fields.map((field) => field.fieldLabel)).toEqual(['This knowledge on/off', 'Knowledge levels', 'All knowledge on/off', 'This knowledge on/off'])
  expect(fields.every((field) => field.pickerGroup?.category === 'Knowledge')).toBe(true)
  expect(fields.filter((field) => field.connectorFactor?.agentId === 'agent')).toHaveLength(2)
})

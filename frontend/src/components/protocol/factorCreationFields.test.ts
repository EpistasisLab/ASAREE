import { expect, it } from 'vitest'
import type { Edge, Node } from '@xyflow/react'
import { factorCreationFields, groupFactorCreationFields } from './bindableFields'

it('groups by owning agent and component, putting shared nodes last and keeping connector modes together', () => {
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
  expect(groups.map(group => group.label)).toEqual(['Alpha', 'Zebra', 'Shared or unconnected components'])
  expect(groups[0].components.map(component => component.category)).toEqual(['Agent fields', 'Skills', 'Datasets', 'Datasets'])
  expect(groups[0].components.find(component => component.category === 'Skills')?.fields.map(field => field.fieldLabel)).toEqual(['Skill levels', 'All skills on/off'])
  expect(groups[2].components.map(component => component.label)).toEqual(['Shared skill', 'Unconnected skill'])
})

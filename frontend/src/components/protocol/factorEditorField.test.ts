import type { Node, Edge } from '@xyflow/react'
import { expect, it } from 'vitest'
import type { DesignFactor } from '@/types/experiments'
import { factorEditorField } from './bindableFields'

it.each([
  ['agent', 'config.prompt', 'text', 'agent'],
  ['agent', 'config.system_prompt', 'text', 'agent'],
  ['agent', 'active', 'boolean', 'agent'],
  ['model_anthropic', 'config', 'model_config', 'model'],
  ['model_openai', 'config.temperature', 'number', 'model'],
  ['model_azure_foundry', 'config.model', 'string', 'model'],
  ['pattern_reason_act', 'config.max_iterations', 'number', 'pattern'],
  ['sub_agent', 'active', 'boolean', 'sub_agent'],
  ['mcp_tool', 'config.enabled', 'boolean', 'tool'],
  ['mcp_tool', 'config.tool_names', 'tool_names', 'tool'],
  ['script', 'config.enabled', 'boolean', 'script'],
  ['skill', 'config.enabled', 'boolean', 'skill'],
  ['persona', 'config.enabled', 'boolean', 'persona'],
  ['dataset', 'config.enabled', 'boolean', 'dataset'],
  ['okf_bundle', 'config.enabled', 'boolean', 'knowledge'],
] as const)('resolves a bound %s %s factor to its existing %s dialog', (type, path, level_type, kind) => {
  const factor: DesignFactor = { name: 'Treatment', level_type, levels: ['a', 'b'] }
  const nodes: Node[] = [{ id: 'node', type, position: { x: 0, y: 0 }, data: {
    label: 'Subject', config: {}, factor_bindings: { [path]: factor.name },
  } }]
  const before = structuredClone(nodes)
  expect(factorEditorField(nodes, [], factor)).toMatchObject({
    nodeId: 'node', fieldPath: path, connectorFactor: { kind, nodeId: 'node', mode: level_type },
  })
  expect(nodes).toEqual(before)
})

it.each([
  ['sub_agent', 'sub_agent', 'sub_agents'], ['tool', 'mcp_tool', 'tool'],
  ['script', 'script', 'tool'], ['skill', 'skill', 'skill'], ['persona', 'persona', 'persona'],
  ['dataset', 'dataset', 'dataset'], ['knowledge', 'okf_bundle', 'knowledge'],
  ['pattern', 'pattern_reason_act', 'architectural_pattern'],
] as const)('resolves shared %s factors through their connected node', (kind, type, targetHandle) => {
  const path = kind === 'pattern' ? 'pattern_override' : `${kind}_selection`
  for (const mode of kind === 'pattern' ? ['pattern'] as const : [`${kind}_selection`, `${kind}_toggle`] as const) {
    const factor: DesignFactor = { name: 'Treatment', level_type: mode, levels: [[], []] }
    const nodes: Node[] = [
      { id: 'connector', type, position: { x: 0, y: 0 }, data: { label: 'Connector', config: {} } },
      ...['first', 'second'].map(id => ({ id, type: 'agent', position: { x: 0, y: 0 }, data: {
        label: id, config: {}, factor_bindings: { [path]: factor.name },
      } })),
    ]
    const edges: Edge[] = ['first', 'second'].map(id => ({ id, source: 'connector', target: id, targetHandle }))
    expect(factorEditorField(nodes, edges, factor)).toMatchObject({
      fieldPath: path, connectorFactor: { kind, nodeId: 'connector', mode },
    })
  }
})

it('leaves an unbound factor available for the fallback editor', () => {
  expect(factorEditorField([], [], { name: 'Unbound', levels: ['a', 'b'], level_type: 'string' })).toBeUndefined()
})

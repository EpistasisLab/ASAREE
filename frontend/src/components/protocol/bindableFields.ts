import type { Node } from '@xyflow/react'
import type { LevelType } from './factorLevels'

export interface BindableFieldSpec {
  fieldPath: string
  label: string
  levelType: LevelType
}

// The Design tab's "Add factor" picker needs to know, for any node type,
// which fields are ever wrapped in a FactorBindableField "+" -- this is the
// single catalog both that picker and (implicitly) each node inspector's
// own FactorBindableField calls agree with, so the two never drift apart.
// Kept as a plain node-type switch rather than co-locating it inside each
// inspector component: the inspectors need live, capability-gated data
// (LlmNodeInspector's Temperature/Effort only show for models that support
// them) that isn't available outside that component's own query state, so
// this catalog deliberately lists every field a node type *could* ever
// bind, not only the ones currently visible on one specific node instance.
// Binding a field the current model doesn't support (e.g. Effort on a
// model without it) is harmless -- the value just isn't read.
//
// "active" (Agent only) isn't shown anywhere in AgentNodeInspector today --
// it's the canvas hover-toolbar's Power/PowerOff toggle instead -- but
// services.protocol_execution's apply_factor_bindings already applies a
// dotted path starting at the node's own `data` (not hardcoded to
// `data.config`), so `data.active` is already a real, working bindable
// field today with zero backend changes; only the frontend never offered
// a way to bind it until now.
export function bindableFieldsForNode(node: Node): BindableFieldSpec[] {
  switch (node.type) {
    case 'agent':
      return [
        { fieldPath: 'config.system_prompt', label: 'System prompt', levelType: 'text' },
        { fieldPath: 'active', label: 'Active', levelType: 'boolean' },
      ]
    case 'critic_gate':
      return [{ fieldPath: 'config.enabled', label: 'Enabled', levelType: 'boolean' }]
    case 'llm_anthropic':
    case 'llm_openai':
    case 'llm_azure_foundry':
      return [
        { fieldPath: 'config.model', label: 'Model', levelType: 'string' },
        { fieldPath: 'config.temperature', label: 'Temperature', levelType: 'number' },
        { fieldPath: 'config.effort', label: 'Effort', levelType: 'string' },
        { fieldPath: 'config.max_tokens', label: 'Max tokens', levelType: 'number' },
      ]
    default:
      return []
  }
}

export interface UnboundField {
  nodeId: string
  nodeLabel: string
  fieldPath: string
  fieldLabel: string
  levelType: LevelType
  // The field's own current value (e.g. an Agent's actual config.system_prompt
  // text) -- lets FactorEditorDialog's field-picker seed the first level the
  // same way FactorBindableField's own popover already does, matching
  // services.protocol_execution's own apply_factor_bindings, which resolves
  // a dotted path starting at the node's whole `data` object.
  currentValue: unknown
}

function getPath(data: Record<string, unknown>, dottedPath: string): unknown {
  let target: unknown = data
  for (const part of dottedPath.split('.')) {
    if (typeof target !== 'object' || target === null) return undefined
    target = (target as Record<string, unknown>)[part]
  }
  return target
}

// Every bindable field across the canvas that ISN'T already bound to a
// factor -- what the Design tab's "Add factor" picker lists. A field
// already bound just shows its "Factor: {name}" badge in the inspector
// instead of the "+" trigger, so it has nothing left to offer here either.
export function unboundBindableFields(nodes: Node[]): UnboundField[] {
  const result: UnboundField[] = []
  for (const node of nodes) {
    const bindings = (node.data as { factor_bindings?: Record<string, string> })?.factor_bindings ?? {}
    const label = (node.data as { label?: string })?.label || node.type || 'Node'
    for (const field of bindableFieldsForNode(node)) {
      if (!bindings[field.fieldPath]) {
        result.push({
          nodeId: node.id,
          nodeLabel: label,
          fieldPath: field.fieldPath,
          fieldLabel: field.label,
          levelType: field.levelType,
          currentValue: getPath(node.data as Record<string, unknown>, field.fieldPath),
        })
      }
    }
  }
  return result
}

import type { QueryClient } from '@tanstack/react-query'
import type { Edge, Node } from '@xyflow/react'
import type { LLMSettingModelsResponse } from '@/types/llmSettings'
import type { DatasetNodeData, LlmNodeData, McpToolNodeData, ScriptNodeData } from '@/types/protocols'
import { PROVIDER_META } from './nodes/LlmNode'
import { providerModelsKey } from './useProviderModels'

export interface NodeConfigIssue {
  nodeId: string
  label: string
  issues: string[]
}

// A pre-flight scan run right before a real Run fires, so an obviously
// misconfigured node (no model, no dataset picked, no script code, an
// agent with nothing wired into its required LLM connector) surfaces as an
// upfront "run anyway?" confirmation instead of only ever showing up as a
// generic "one or more nodes failed" AFTER a real (billable) run attempt.
// Mirrors the SAME cheap, synchronous presence checks each node's own
// canvas card already computes for its own warning triangle (LlmNode.tsx/
// McpToolNode.tsx/DatasetNode.tsx/ScriptNode.tsx) -- kept as a plain
// duplicate rather than a shared import specifically to avoid coupling this
// module's shape to each node component's own render; update both places
// together if these conditions ever change.
//
// `queryClient` lets the LLM check also catch a model that's SET but not
// among the provider's own discovered list (LlmNode.tsx's own richer
// check) -- read from cache only, via the exact same queryKey LlmNode.tsx
// already populates by rendering on this same canvas, so this never fires
// its own network request or makes clicking Run wait on one. A cache miss
// (that query never ran, or hasn't resolved yet) just means "can't tell,"
// same as LlmNode.tsx's own empty-list case -- not treated as an issue.
export function findNodeConfigIssues(nodes: Node[], edges: Edge[], queryClient: QueryClient): NodeConfigIssue[] {
  const agentIdsWithLlm = new Set(edges.filter((e) => e.targetHandle === 'llm').map((e) => e.target))
  const result: NodeConfigIssue[] = []

  for (const node of nodes) {
    const data = node.data as { label?: string } | undefined
    const label = data?.label || node.type || 'node'
    const issues: string[] = []

    switch (node.type) {
      case 'agent':
        if (!agentIdsWithLlm.has(node.id)) issues.push('No LLM connected')
        break
      case 'llm_anthropic':
      case 'llm_openai':
      case 'llm_azure_foundry': {
        const config = (node.data as LlmNodeData).config
        if (!config?.model) {
          issues.push('No model set')
        } else {
          const cached = queryClient.getQueryData<LLMSettingModelsResponse>(providerModelsKey(config.provider))
          const models = cached?.models ?? []
          // Only a live listing (`source: 'api'` -- an Azure Foundry
          // project's deployments, or Anthropic's own GET /v1/models) is
          // authoritative enough to call an id wrong. The static catalog is
          // knowingly incomplete and the inspector's "Custom model..." field
          // exists to go past it, so an off-catalog id there is a supported
          // choice, not a misconfigured node worth interrupting a Run for.
          // Same gate as LlmNode.tsx's own warning triangle; see the longer
          // note there.
          if (cached?.source === 'api' && models.length > 0 && !models.some((m) => m.id === config.model)) {
            const label = PROVIDER_META[config.provider]?.label ?? config.provider
            issues.push(`"${config.model}" isn't available on your ${label} credential`)
          }
        }
        break
      }
      case 'mcp_tool': {
        const config = (node.data as McpToolNodeData).config
        if (!((config?.tool_names?.length ?? 0) > 0)) issues.push('Not configured -- pick a server and at least one tool')
        break
      }
      case 'dataset': {
        const config = (node.data as DatasetNodeData).config
        if (!config?.dataset_id) issues.push('No dataset selected')
        break
      }
      case 'script': {
        const config = (node.data as ScriptNodeData).config
        if (!config?.code) issues.push('No code set')
        break
      }
      default:
        break
    }

    if (issues.length > 0) result.push({ nodeId: node.id, label, issues })
  }

  return result
}

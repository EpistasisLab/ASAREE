import type { QueryClient } from '@tanstack/react-query'
import type { Edge, Node } from '@xyflow/react'
import type { LLMSettingModelsResponse } from '@/types/llmSettings'
import type { OkfBundle, OkfDocument } from '@/types/okf'
import type { Skill } from '@/types/skills'
import type {
  DatasetNodeData,
  LlmNodeData,
  McpToolNodeData,
  ReasonActPatternNodeData,
  ScriptNodeData,
  OkfBundleNodeData,
  OkfDocumentNodeData,
  SkillNodeData,
} from '@/types/protocols'
import { PROVIDER_META } from './nodes/LlmNode'
import { providerModelsKey } from './useProviderModels'

export interface NodeConfigIssue {
  nodeId: string
  label: string
  issues: string[]
}

// A pre-flight scan run right before a real Run fires, so an obviously
// misconfigured node (no model, no dataset or skill picked, no script code, an
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
  const agentIdsWithLlm = new Set(edges.filter((e) => e.targetHandle === 'ai').map((e) => e.target))
  const result: NodeConfigIssue[] = []

  for (const node of nodes) {
    const data = node.data as { label?: string } | undefined
    const label = data?.label || node.type || 'node'
    const issues: string[] = []

    switch (node.type) {
      case 'agent':
        if (!agentIdsWithLlm.has(node.id)) issues.push('No AI connected')
        break
      case 'llm_anthropic':
      case 'llm_openai':
      case 'llm_azure_foundry': {
        const config = (node.data as LlmNodeData).config
        let selectedModelInfo: LLMSettingModelsResponse['models'][number] | undefined
        if (!config?.model) {
          issues.push('No model set')
        } else {
          const cached = queryClient.getQueryData<LLMSettingModelsResponse>(providerModelsKey(config.provider))
          const models = cached?.models ?? []
          selectedModelInfo = models.find((m) => m.id === config.model)
          // Only a live listing (`source: 'api'` -- an Azure Foundry
          // project's deployments, or Anthropic's own GET /v1/models) is
          // authoritative enough to call an id wrong. The static catalog is
          // knowingly incomplete and the inspector's "Custom model..." field
          // exists to go past it, so an off-catalog id there is a supported
          // choice, not a misconfigured node worth interrupting a Run for.
          // Same gate as LlmNode.tsx's own warning triangle; see the longer
          // note there.
          if (cached?.source === 'api' && models.length > 0 && !selectedModelInfo) {
            const label = PROVIDER_META[config.provider]?.label ?? config.provider
            issues.push(`"${config.model}" isn't available on your ${label} credential`)
          }
        }
        if (config?.max_tokens == null) issues.push('Max tokens is required')
        // Same "unrecognized model defaults to temperature-only" fallback as
        // LlmNodeInspector.tsx's own showTemperature -- required whenever
        // it's the field actually offered for this model, so it's never
        // Motoro's own silent ModelConfig default (0.7) filling the gap.
        if ((selectedModelInfo?.supports_temperature ?? true) && config?.temperature == null) {
          issues.push('Temperature is required')
        }
        break
      }
      case 'mcp_tool':
      case 'mcp_scikit_learn':
      case 'mcp_client_tool': {
        const config = (node.data as McpToolNodeData).config
        // Same wording as McpToolNode's own badge -- an MCP node's server is
        // fixed at creation, so the allow-list is the only thing to fix.
        // Skipped once the allow-list is a factor: the node's own tool_names
        // is then just a base value every cell overrides, and an empty
        // allow-list is a legitimate level ("this server withheld here"), so
        // flagging it would block a Run that's correctly configured.
        const allowListIsFactor = !!(node.data as { factor_bindings?: Record<string, string> })?.factor_bindings?.[
          'config.tool_names'
        ]
        if (!allowListIsFactor && !((config?.tool_names?.length ?? 0) > 0)) {
          issues.push('Not configured -- allow at least one tool')
        }
        break
      }
      case 'dataset': {
        const config = (node.data as DatasetNodeData).config
        if (!config?.dataset_id) issues.push('No dataset selected')
        break
      }
      case 'skill': {
        const config = (node.data as SkillNodeData).config
        // Same wording as SkillNode's own badge.
        if (!config?.skill_id) {
          issues.push('No skill selected')
        } else {
          // A set id can still resolve to nothing -- a skill deleted from the
          // library, or a graph imported from another account, both leave the
          // id (and its cached skill_name) behind. Cache-only, same contract
          // as the model check above: a miss means "can't tell," not an issue.
          const cached = queryClient.getQueryData<Skill[]>(['skills'])
          if (cached && !cached.some((s) => s.id === config.skill_id)) {
            issues.push(`Skill no longer exists${config.skill_name ? ` ("${config.skill_name}")` : ''}`)
          }
        }
        break
      }
      case 'okf_bundle': {
        const config = (node.data as OkfBundleNodeData).config
        // Same wording as OkfBundleNode's own badge. server_name, not
        // bundle_id, is what a run keys off -- a node without it contributes
        // nothing even if it still remembers which row it came from.
        if (!config?.server_name) {
          issues.push('No bundle selected')
        } else if ((config.tool_names?.length ?? 0) === 0) {
          // Registered, but its MCP server never reported any tools -- almost
          // always a server that failed to spawn. The agent would get nothing
          // from the bundle, silently, so it's worth flagging up front.
          issues.push('No tools discovered -- the bundle server may have failed to start')
        } else {
          // A set id can still resolve to nothing -- deregistered, or a graph
          // imported from another account. Cache-only, same contract as the
          // skill check above: a miss means "can't tell," not an issue.
          const cached = queryClient.getQueryData<OkfBundle[]>(['okf-bundles'])
          if (cached && !cached.some((b) => b.id === config.bundle_id)) {
            issues.push(`OKF bundle is no longer registered${config.bundle_path ? ` ("${config.bundle_path}")` : ''}`)
          }
        }
        break
      }
      case 'okf_document': {
        const config = (node.data as OkfDocumentNodeData).config
        // The bundle case's checks, against the document registry -- same
        // wording as OkfDocumentNode's own badge.
        if (!config?.server_name) {
          issues.push('No document selected')
        } else if ((config.tool_names?.length ?? 0) === 0) {
          issues.push('No tools discovered -- the document server may have failed to start')
        } else {
          const cached = queryClient.getQueryData<OkfDocument[]>(['okf-documents'])
          if (cached && !cached.some((d) => d.id === config.document_id)) {
            issues.push(
              `OKF document is no longer stored${config.document_title ? ` ("${config.document_title}")` : ''}`,
            )
          }
        }
        break
      }
      case 'script': {
        const config = (node.data as ScriptNodeData).config
        if (!config?.code) issues.push('No code set')
        break
      }
      case 'pattern_reason_act': {
        const config = (node.data as ReasonActPatternNodeData).config
        if (config.max_iterations == null) issues.push('Max iterations is required')
        if (config.include_scratchpad && config.scratchpad_window == null) issues.push('Scratchpad window is required')
        break
      }
      default:
        break
    }

    if (issues.length > 0) result.push({ nodeId: node.id, label, issues })
  }

  return result
}

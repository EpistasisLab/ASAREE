import { AgentNode } from './nodes/AgentNode'
import { CriticGateNode } from './nodes/CriticGateNode'
import { ToolStepNode } from './nodes/ToolStepNode'
import { DatasetNode } from './nodes/DatasetNode'
import { ModelNode } from './nodes/ModelNode'
import { McpClientToolNode } from './nodes/McpClientToolNode'
import { McpToolNode } from './nodes/McpToolNode'
import { MemoryNode } from './nodes/MemoryNode'
import { OutputParserNode } from './nodes/OutputParserNode'
import { ReasonActPatternNode } from './nodes/ReasonActPatternNode'
import { ScriptNode } from './nodes/ScriptNode'
import { SingleAgentBaselinePatternNode } from './nodes/SingleAgentBaselinePatternNode'
import { OkfBundleNode } from './nodes/OkfBundleNode'
import { OkfDocumentNode } from './nodes/OkfDocumentNode'
import { SkillNode } from './nodes/SkillNode'
import { PersonaNode } from './nodes/PersonaNode'

export const NODE_TYPES = {
  agent: AgentNode, sub_agent: AgentNode,
  mcp_tool: McpToolNode, mcp_scikit_learn: McpToolNode, mcp_client_tool: McpClientToolNode,
  critic_gate: CriticGateNode, tool_step: ToolStepNode,
  model_anthropic: ModelNode, model_openai: ModelNode, model_azure_foundry: ModelNode,
  model_openrouter: ModelNode, model_local: ModelNode,
  memory: MemoryNode, output_parser: OutputParserNode, dataset: DatasetNode,
  skill: SkillNode, okf_bundle: OkfBundleNode, okf_document: OkfDocumentNode,
  script: ScriptNode, pattern_reason_act: ReasonActPatternNode,
  pattern_single_agent_baseline: SingleAgentBaselinePatternNode,
  persona: PersonaNode,
}

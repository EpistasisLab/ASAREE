import { AgentNode } from './nodes/AgentNode'
import { CriticGateNode } from './nodes/CriticGateNode'
import { ToolStepNode } from './nodes/ToolStepNode'
import { DatasetNode } from './nodes/DatasetNode'
import { ModelNode } from './nodes/ModelNode'
import { McpClientToolNode } from './nodes/McpClientToolNode'
import { McpToolNode } from './nodes/McpToolNode'
import { MemoryNode } from './nodes/MemoryNode'
import { OutputParserNode } from './nodes/OutputParserNode'
import { PersonaNode } from './nodes/PersonaNode'
import { ReasonActPatternNode } from './nodes/ReasonActPatternNode'
import { ScriptNode } from './nodes/ScriptNode'
import { SingleAgentBaselinePatternNode } from './nodes/SingleAgentBaselinePatternNode'
import { OkfBundleNode } from './nodes/OkfBundleNode'
import { OkfDocumentNode } from './nodes/OkfDocumentNode'
import { SkillNode } from './nodes/SkillNode'
import { withResourceWarnings } from './nodeResourceIssuesContext'

export const NODE_TYPES = {
  agent: withResourceWarnings(AgentNode), sub_agent: withResourceWarnings(AgentNode),
  mcp_tool: withResourceWarnings(McpToolNode), mcp_scikit_learn: withResourceWarnings(McpToolNode), mcp_client_tool: withResourceWarnings(McpClientToolNode),
  critic_gate: withResourceWarnings(CriticGateNode), tool_step: withResourceWarnings(ToolStepNode),
  model_anthropic: withResourceWarnings(ModelNode), model_openai: withResourceWarnings(ModelNode), model_azure_foundry: withResourceWarnings(ModelNode),
  model_openrouter: withResourceWarnings(ModelNode), model_local: withResourceWarnings(ModelNode),
  memory: withResourceWarnings(MemoryNode), output_parser: withResourceWarnings(OutputParserNode), dataset: withResourceWarnings(DatasetNode),
  persona: withResourceWarnings(PersonaNode),
  skill: withResourceWarnings(SkillNode), okf_bundle: withResourceWarnings(OkfBundleNode), okf_document: withResourceWarnings(OkfDocumentNode),
  script: withResourceWarnings(ScriptNode), pattern_reason_act: withResourceWarnings(ReasonActPatternNode),
  pattern_single_agent_baseline: withResourceWarnings(SingleAgentBaselinePatternNode),
}

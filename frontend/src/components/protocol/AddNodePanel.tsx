import { useState } from 'react'
import { ArrowRight, Atom, Bot, BrainCircuit, Cloud, Repeat2, ShieldCheck, Sparkles, Wrench, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

// "dataset" still needs real unbuilt backend meaning (a dataset-binding
// decision) -- adding an entry before that exists would be UI ceremony with
// no backend behind it. Every other entry here earns its place:
// GET /api/mcp-servers already backs the tool picker,
// services.protocol_execution._run_gated_worker already implements the
// critic gate's revision loop, and _resolve_llm_config/_resolve_tool_config
// already resolve an agent's LLM/Tool connectors. "memory" and the two
// pattern entries are the exceptions -- each is real in the graph/
// validation sense (wiring one up is accepted and does something visually)
// but has NO runtime effect yet, documented on the node/inspector itself,
// not hidden from the catalog. LLM/Architectural Pattern are each a family
// of node types (one per provider/pattern -- see LlmNodeData/
// ReasonActPatternNodeData in types/protocols.ts for why), not one generic
// entry with an internal picker -- this catalog, filtered to a connector's
// own family via allowedTypes, IS that picker.
const NODE_CATALOG = [
  { type: 'agent', label: 'Agent', description: 'An LLM agent stage in the pipeline', icon: Bot },
  { type: 'mcp_tool', label: 'MCP Tool', description: "Allow-list a subset of a registered MCP server's tools for an Agent", icon: Wrench },
  {
    type: 'critic_gate',
    label: 'Critic Gate',
    description: "Reviews an upstream Agent's output, requests revisions",
    icon: ShieldCheck,
  },
  { type: 'llm_anthropic', label: 'Anthropic', description: "An Agent or Critic Gate's model, temperature, and parameters", icon: Sparkles },
  { type: 'llm_openai', label: 'OpenAI', description: "An Agent or Critic Gate's model, temperature, and parameters", icon: Atom },
  {
    type: 'llm_azure_foundry',
    label: 'Azure AI Foundry',
    description: "An Agent or Critic Gate's model, temperature, and parameters -- routed through your own Azure resource",
    icon: Cloud,
  },
  {
    type: 'pattern_reason_act',
    label: 'Reason + Act',
    description: 'Alternates between reasoning and tool calls each iteration until it reaches a final answer',
    icon: Repeat2,
  },
  {
    type: 'pattern_single_agent_baseline',
    label: 'Single-Agent Baseline',
    description: 'One reasoning pass per iteration, no tool-call loop -- the cheap default when there’s nothing to call',
    icon: ArrowRight,
  },
  {
    type: 'memory',
    label: 'Memory',
    description: 'Not yet functional -- declares intent for a future phase',
    icon: BrainCircuit,
  },
]

export function AddNodePanel({
  onAdd,
  onClose,
  allowedTypes,
  title = 'Add node',
}: {
  onAdd: (nodeType: string) => void
  onClose: () => void
  // Restricts the catalog to whichever node type(s) fill a specific
  // connector slot (ConnectorAddStub) -- undefined means the normal,
  // unrestricted "+" toolbar button flow.
  allowedTypes?: string[]
  title?: string
}) {
  const [query, setQuery] = useState('')
  const catalog = allowedTypes ? NODE_CATALOG.filter((item) => allowedTypes.includes(item.type)) : NODE_CATALOG
  const filtered = catalog.filter((item) => item.label.toLowerCase().includes(query.trim().toLowerCase()))

  return (
    <div className="flex w-80 shrink-0 flex-col gap-3 border-l p-4">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold">{title}</p>
        <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={onClose}>
          <X className="size-4" />
        </Button>
      </div>
      <Input autoFocus placeholder="Search node types…" value={query} onChange={(e) => setQuery(e.target.value)} />
      <div className="flex flex-col gap-1.5">
        {filtered.length === 0 && <p className="py-4 text-center text-sm text-muted-foreground">No matching node types.</p>}
        {filtered.map((item) => (
          <button
            key={item.type}
            type="button"
            onClick={() => onAdd(item.type)}
            className="flex cursor-pointer items-center gap-2.5 rounded-lg border bg-background px-3 py-2.5 text-left text-sm shadow-[0_0_16px_-6px_var(--primary)] ring-1 ring-primary/20 transition-colors hover:bg-muted"
          >
            <item.icon className="size-4 shrink-0 text-primary" />
            <div className="min-w-0">
              <p className="font-medium">{item.label}</p>
              <p className="truncate text-xs text-muted-foreground">{item.description}</p>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

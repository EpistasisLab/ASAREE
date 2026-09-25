import { useState } from 'react'
import { ArrowRight, Atom, BookMarked, Bot, Braces, BrainCircuit, Cloud, Code2, Database, FileText, HardDrive, Repeat2, Route, ScrollText, ShieldCheck, Server, Sparkles, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { DATASET_BROWSE } from './datasetCatalog'
import { MCP_SERVER_BROWSE } from './mcpServerCatalog'
import { OKF_BUNDLE_BROWSE, OKF_DOCUMENT_BROWSE } from './okfCatalog'
import { SKILL_BROWSE } from './skillCatalog'

const NODE_CATEGORIES = [
  { id: 'agents', label: 'Agents' },
  { id: 'models', label: 'Models' },
  { id: 'patterns', label: 'Execution patterns' },
  { id: 'knowledge', label: 'Knowledge & data' },
  { id: 'tools', label: 'Tools & output' },
] as const

type NodeCategory = (typeof NODE_CATEGORIES)[number]['id']

// Every entry here earns its place: GET /api/mcp-servers already backs the
// tool picker, GET /datasets already backs the dataset picker,
// services.protocol_execution._run_gated_worker already implements the
// critic gate's revision loop, and _resolve_model_config/_resolve_tool_config/
// _resolve_dataset_configs/_resolve_script_configs already resolve an agent's
// respective connectors. "memory" and the two pattern entries are the
// exceptions -- each is real in the graph/validation sense (wiring one up is
// accepted and does something visually) but has NO runtime effect yet,
// documented on the node/inspector itself, not hidden from the catalog.
// LLM/Architectural Pattern are each a family of node types (one per
// provider/pattern -- see ModelNodeData/ReasonActPatternNodeData in
// types/protocols.ts for why), not one generic entry with an internal
// picker -- this catalog, filtered to a connector's own family via
// allowedTypes, IS that picker.
const NODE_CATALOG = [
  { type: 'agent', category: 'agents', label: 'Agent', description: 'An LLM agent stage in the pipeline', icon: Bot },
  { type: 'sub_agent', category: 'agents', label: 'Sub-Agent', description: 'A delegated worker callable by one parent Agent', icon: Bot },
  // Not a node type -- picking this opens the server browser
  // (McpServerBrowserPanel), and the node gets created from whichever
  // server is chosen there. It replaced a plain "MCP Tool" entry that made
  // a blank node whose server you then had to find in a dropdown: the
  // server IS the choice, so it belongs in this catalog, not two clicks
  // deeper. Generic `mcp_tool` nodes are still created (for any server with
  // no dedicated type of its own) and still open their old inspector, so
  // nothing already on a canvas changes.
  {
    type: MCP_SERVER_BROWSE,
    category: 'tools',
    label: 'MCP Servers',
    description: "Browse available MCP servers and allow-list their tools for an Agent",
    icon: Server,
  },
  {
    type: 'critic_gate',
    category: 'agents',
    label: 'Critic Gate',
    description: "Reviews an upstream Agent's output, requests revisions",
    icon: ShieldCheck,
  },
  { type: 'model_anthropic', category: 'models', label: 'Anthropic', description: "An Agent or Critic Gate's model, temperature, and parameters", icon: Sparkles },
  { type: 'model_openai', category: 'models', label: 'OpenAI', description: "An Agent or Critic Gate's model, temperature, and parameters", icon: Atom },
  {
    type: 'model_azure_foundry',
    category: 'models',
    label: 'Azure AI Foundry',
    description: "An Agent or Critic Gate's model, temperature, and parameters -- routed through your own Azure resource",
    icon: Cloud,
  },
  {
    type: 'model_openrouter',
    category: 'models',
    label: 'OpenRouter',
    description: "An Agent or Critic Gate's model, temperature, and parameters -- routed through your own OpenRouter account",
    icon: Route,
  },
  {
    type: 'model_local',
    category: 'models',
    label: 'Local',
    description: "An Agent or Critic Gate's model, temperature, and parameters -- routed to a self-hosted OpenAI-compatible server",
    icon: HardDrive,
  },
  {
    type: 'pattern_reason_act',
    category: 'patterns',
    label: 'Reason + Act',
    description: 'Alternates between reasoning and tool calls each iteration until it reaches a final answer',
    icon: Repeat2,
  },
  {
    type: 'pattern_single_agent_baseline',
    category: 'patterns',
    label: 'Single-Agent Baseline',
    description: 'One reasoning pass per iteration, no tool-call loop -- the cheap default when there’s nothing to call',
    icon: ArrowRight,
  },
  {
    type: 'memory',
    category: 'knowledge',
    label: 'Memory',
    description: 'Not yet functional -- declares intent for a future phase',
    icon: BrainCircuit,
  },
  // Not a node type either -- picking this opens the dataset browser
  // (DatasetBrowserPanel), same reasoning as MCP Servers above and Skills
  // below: the
  // dataset IS the choice, and that browser is the only place the library is
  // listed, so it's where registering and deleting live too. As with Skill,
  // there's no picker in the node's inspector -- the dataset IS the node.
  {
    type: DATASET_BROWSE,
    category: 'knowledge',
    label: 'Datasets',
    description: "Browse your registered datasets -- the data an Agent's workspace tools operate on",
    icon: Database,
  },
  // Not a node type either -- picking this opens the skill browser
  // (SkillBrowserPanel), same reasoning as MCP Servers above: the skill IS
  // the choice, so it belongs in this catalog rather than two clicks deeper
  // in a blank node's inspector. That browser is also the only view of the
  // whole skill library, so it's where registering and deleting live.
  {
    type: SKILL_BROWSE,
    category: 'knowledge',
    label: 'Skills',
    description: 'Browse your Agent Skills -- instructions an Agent opens when their description matches the task',
    icon: ScrollText,
  },
  // Not a node type either -- picking this opens the OKF bundle browser
  // (OkfBundleBrowserPanel), same reasoning as MCP Servers and Skills above.
  // That browser is also the only place bundles are uploaded.
  {
    type: OKF_BUNDLE_BROWSE,
    category: 'knowledge',
    label: 'OKF Bundles',
    description: 'Upload a folder of Markdown concepts an Agent reads and writes as it works',
    icon: BookMarked,
  },
  // The Knowledge connector's other half, and a separate entry rather than a
  // mode of the one above because the unit differs, not the source: both
  // upload from the user's own machine, but a bundle is a whole folder of
  // concepts and a document is a single one -- exactly like Skills.
  {
    type: OKF_DOCUMENT_BROWSE,
    category: 'knowledge',
    label: 'OKF Documents',
    description: 'Upload a single Markdown concept an Agent reads and rewrites as it works',
    icon: FileText,
  },
  {
    type: 'output_parser',
    category: 'tools',
    label: 'Output Parser',
    description: "Defines the format an Agent's answer must take, and reads its named, typed fields back out",
    icon: Braces,
  },
  {
    type: 'script',
    category: 'tools',
    label: 'Script',
    description: 'A fixed piece of Python code an Agent passes verbatim into some tool',
    icon: Code2,
  },
] satisfies Array<{
  type: string
  category: NodeCategory
  label: string
  description: string
  icon: typeof Bot
}>

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
  const catalog = allowedTypes
    ? NODE_CATALOG.filter((item) => allowedTypes.includes(item.type))
    : NODE_CATALOG
  const filtered = catalog.filter((item) => item.label.toLowerCase().includes(query.trim().toLowerCase()))
  const groups = NODE_CATEGORIES.flatMap((category) => {
    const items = filtered.filter((item) => item.category === category.id)
    return items.length > 0 ? [{ ...category, items }] : []
  })

  return (
    <div className="flex min-h-0 w-80 shrink-0 flex-col gap-3 overflow-hidden border-l p-4">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold">{title}</p>
        <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={onClose}>
          <X className="size-4" />
        </Button>
      </div>
      <Input autoFocus placeholder="Search node types…" value={query} onChange={(e) => setQuery(e.target.value)} />
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        {filtered.length === 0 && <p className="py-4 text-center text-sm text-muted-foreground">No matching node types.</p>}
        <div className="flex flex-col gap-4">
          {groups.map((group) => (
            <section key={group.id} aria-labelledby={`add-node-${group.id}`}>
              <h3 id={`add-node-${group.id}`} className="mb-1.5 font-mono text-[10px] font-medium tracking-[0.16em] text-muted-foreground uppercase">
                {group.label}
              </h3>
              <div className="flex flex-col gap-1.5">
                {group.items.map((item) => (
                  <button
                    key={item.type}
                    type="button"
                    disabled={!allowedTypes && item.type === 'sub_agent'}
                    onClick={() => onAdd(item.type)}
                    className="flex cursor-pointer items-center gap-2.5 rounded-lg border bg-background px-3 py-2.5 text-left text-sm shadow-[0_0_16px_-6px_var(--primary)] ring-1 ring-primary/20 transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-55 disabled:hover:bg-background"
                  >
                    <item.icon className="size-4 shrink-0 text-primary" />
                    <div className="min-w-0">
                      <p className="font-medium">{item.label}</p>
                      <p className="truncate text-xs text-muted-foreground">
                        {!allowedTypes && item.type === 'sub_agent'
                          ? "Add from an Agent's Sub-Agents connector"
                          : item.description}
                      </p>
                    </div>
                  </button>
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>
    </div>
  )
}

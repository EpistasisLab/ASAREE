import { useState } from 'react'
import { Bot, Wrench, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

// "dataset"/"critic_gate" still need real unbuilt backend meaning (a
// dataset-binding decision, a genuinely new revision-loop primitive) --
// adding entries here before that exists would be UI ceremony with no
// backend behind it. "mcp_tool" earns its place now: GET /api/mcp-servers
// already exists, so there's a real tool picker to back it.
const NODE_CATALOG = [
  { type: 'agent', label: 'Agent', description: 'An LLM agent stage in the pipeline', icon: Bot },
  { type: 'mcp_tool', label: 'MCP Tool', description: 'Call one tool on a registered MCP server', icon: Wrench },
]

export function AddNodePanel({ onAdd, onClose }: { onAdd: (nodeType: string) => void; onClose: () => void }) {
  const [query, setQuery] = useState('')
  const filtered = NODE_CATALOG.filter((item) => item.label.toLowerCase().includes(query.trim().toLowerCase()))

  return (
    <div className="flex w-80 shrink-0 flex-col gap-3 border-l p-4">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold">Add node</p>
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
            className="flex items-center gap-2.5 rounded-lg border bg-background px-3 py-2.5 text-left text-sm shadow-[0_0_16px_-6px_var(--primary)] ring-1 ring-primary/20 transition-colors hover:bg-muted"
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

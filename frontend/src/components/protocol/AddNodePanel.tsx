import { useState } from 'react'
import { Bot, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

// V1 ships exactly one node type. "mcp_tool"/"dataset"/"critic_gate" each
// need real unbuilt backend meaning (a tool picker, a dataset-binding
// decision, a genuinely new revision-loop primitive) -- adding entries here
// before that exists would be UI ceremony with no backend behind it. The
// search box stays even at one entry so the panel doesn't need reshaping
// once more node types land.
const NODE_CATALOG = [
  { type: 'agent', label: 'Agent', description: 'An LLM agent stage in the pipeline', icon: Bot },
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

import { Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { emptyCredentialEntry, type CredentialEntry } from '@/lib/mcpCredentials'

export function McpCredentialFields({
  entries,
  onChange,
  namePlaceholder,
}: {
  entries: CredentialEntry[]
  onChange: (entries: CredentialEntry[]) => void
  namePlaceholder: string
}) {
  function patch(id: number, change: Partial<CredentialEntry>) {
    onChange(entries.map((entry) => (entry.id === id ? { ...entry, ...change } : entry)))
  }

  return (
    <div className="space-y-2">
      {entries.map((entry) => (
        <div key={entry.id} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto] gap-2">
          <Input
            aria-label="Credential name"
            className="font-mono"
            placeholder={namePlaceholder}
            value={entry.name}
            onChange={(event) => patch(entry.id, { name: event.target.value })}
          />
          <Input
            aria-label="Credential value"
            className="font-mono"
            type="password"
            autoComplete="off"
            placeholder="Secret value"
            value={entry.value}
            onChange={(event) => patch(entry.id, { value: event.target.value })}
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Remove credential"
            disabled={entries.length === 1}
            onClick={() => onChange(entries.filter((candidate) => candidate.id !== entry.id))}
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
      ))}
      <Button type="button" variant="outline" size="sm" onClick={() => onChange([...entries, emptyCredentialEntry()])}>
        <Plus className="size-3.5" />
        Add credential
      </Button>
    </div>
  )
}

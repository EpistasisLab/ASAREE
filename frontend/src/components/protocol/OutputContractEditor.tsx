import { Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import type { OutputContract, OutputContractField } from '@/types/protocols'

const FIELD_TYPES = ['string', 'integer', 'float', 'boolean', 'list', 'dict'] as const

function emptyField(): OutputContractField {
  return { name: '', type: 'string', description: '' }
}

// Structured editor for agentic-core's output_contract field-spec -- this is
// the mechanism a later execution phase uses to extract a typed payload
// from an agent's free-text output and hand it to the next node, exactly
// how the source notebook's stage handoffs and critic verdicts already work.
export function OutputContractEditor({
  value,
  onChange,
}: {
  value: OutputContract | null
  onChange: (next: OutputContract | null) => void
}) {
  const enabled = value !== null

  function updateField(index: number, patch: Partial<OutputContractField>) {
    if (!value) return
    const fields = value.fields.map((f, i) => (i === index ? { ...f, ...patch } : f))
    onChange({ ...value, fields })
  }

  function removeField(index: number) {
    if (!value) return
    onChange({ ...value, fields: value.fields.filter((_, i) => i !== index) })
  }

  function addField() {
    if (!value) return
    onChange({ ...value, fields: [...value.fields, emptyField()] })
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <Label>Output contract</Label>
          <p className="text-xs text-muted-foreground">
            Extracts a typed payload from this agent's output -- how a later stage reads what came before it.
          </p>
        </div>
        <Switch
          checked={enabled}
          onCheckedChange={(checked) => onChange(checked ? { name: '', fields: [emptyField()] } : null)}
          aria-label="Enable output contract"
        />
      </div>

      {value && (
        <div className="space-y-3 rounded-lg border p-3">
          <div className="space-y-1.5">
            <Label htmlFor="contract-name">Contract name</Label>
            <Input
              id="contract-name"
              placeholder="e.g. dc_report"
              value={value.name}
              onChange={(e) => onChange({ ...value, name: e.target.value })}
            />
          </div>

          <div className="space-y-2">
            {value.fields.map((field, i) => (
              <div key={i} className="flex items-start gap-2 rounded-md border bg-muted/30 p-2">
                <div className="grid flex-1 grid-cols-2 gap-2">
                  <Input
                    placeholder="field name"
                    value={field.name}
                    onChange={(e) => updateField(i, { name: e.target.value })}
                  />
                  <Select value={field.type} onValueChange={(v) => v !== null && updateField(i, { type: v })}>
                    <SelectTrigger className="w-full">
                      <SelectValue>{(v: string) => v}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {FIELD_TYPES.map((t) => (
                        <SelectItem key={t} value={t}>
                          {t}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Input
                    className="col-span-2"
                    placeholder="description (optional)"
                    value={field.description ?? ''}
                    onChange={(e) => updateField(i, { description: e.target.value })}
                  />
                </div>
                <Button variant="ghost" size="icon-sm" aria-label="Remove field" onClick={() => removeField(i)}>
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            ))}
          </div>

          <Button variant="outline" size="sm" onClick={addField}>
            <Plus className="size-3.5" />
            Add field
          </Button>
        </div>
      )}
    </div>
  )
}

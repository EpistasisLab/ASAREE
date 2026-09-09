import { Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { OutputContract, OutputContractField } from '@/types/protocols'

// The types offered for a NEW field. Deliberately not the full set Motoro
// accepts: its _TYPE_MAP takes alias pairs (int/integer, float/number,
// list/array, dict/object, str/string, bool/boolean), and offering both
// spellings of the same type in a dropdown is a choice with no meaning. One
// spelling per type is picked here; the other is still valid, still runs, and
// is preserved on read -- see typeOptions below.
const FIELD_TYPES = ['string', 'integer', 'float', 'boolean', 'list', 'dict'] as const

// The dropdown for one field, widened to include whatever that field is
// ALREADY set to. Most stored contracts came from the SDK, where the natural
// spelling is Motoro's other alias (`integer`, `object`, `array`), and a
// dropdown with no matching item turns any click on it into a silent
// clobbering of a value the user never meant to change -- which is exactly
// what OutputContractField promises not to do. The trigger itself always
// displayed the stored value correctly (base-ui's Value renders the raw
// value; see ui/select.tsx), so this is only about the list.
function typeOptions(current: string): string[] {
  return FIELD_TYPES.includes(current as (typeof FIELD_TYPES)[number]) ? [...FIELD_TYPES] : [current, ...FIELD_TYPES]
}

function emptyField(): OutputContractField {
  return { name: '', type: 'string', description: '' }
}

// Structured editor for Motoro's output_contract field-spec -- the shape an
// agent's free-text answer gets read back into, and (since it became a node)
// the shape the agent is told to write in the first place. Lives in the
// Output Parser node's inspector; it used to sit in a tab of whichever agent
// owned it, where neither its effect nor its cost was visible.
//
// There is no on/off switch here anymore: the node IS the switch. Deleting it
// removes the contract, and its `enabled` toggle suspends it without losing
// the fields.
export function OutputContractEditor({
  value,
  onChange,
}: {
  value: OutputContract | null
  onChange: (next: OutputContract) => void
}) {
  // A null contract is treated as an empty one rather than a separate state:
  // it can only be reached by an older graph or a hand-edit, and there is
  // nothing useful for the node to be except its own editor.
  const contract: OutputContract = value ?? { name: '', fields: [emptyField()] }

  function updateField(index: number, patch: Partial<OutputContractField>) {
    onChange({ ...contract, fields: contract.fields.map((f, i) => (i === index ? { ...f, ...patch } : f)) })
  }

  function removeField(index: number) {
    onChange({ ...contract, fields: contract.fields.filter((_, i) => i !== index) })
  }

  function addField() {
    onChange({ ...contract, fields: [...contract.fields, emptyField()] })
  }

  return (
    <div className="space-y-3">
      <div>
        <Label>Fields</Label>
        <p className="text-xs text-muted-foreground">
          The values read back out of the answer — for consumers that cannot read prose: a tool taking an argument,
          a metric reading a number. The connected agent is told to state each of these explicitly, so the
          descriptions here are what it goes on.
        </p>
      </div>

      <div className="space-y-3 rounded-lg border p-3">
          <div className="space-y-1.5">
            <Label htmlFor="contract-name">Contract name</Label>
            <Input
              id="contract-name"
              placeholder="e.g. dc_report"
              value={contract.name}
              onChange={(e) => onChange({ ...contract, name: e.target.value })}
            />
          </div>

          <div className="space-y-2">
            {contract.fields.map((field, i) => (
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
                      {typeOptions(field.type).map((t) => (
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
    </div>
  )
}

import { useState } from 'react'
import { useEdges, useNodes, useReactFlow } from '@xyflow/react'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { displayNames, handoffSenderFields } from '@/lib/promptReferences'
import type { EdgeHandoff, EdgeHandoffField, EdgeHandoffMode, ProtocolEdge, ProtocolNode } from '@/types/protocols'

// What a main edge into an Agent passes along -- `edge.data.handoff`, read by
// `protocol_execution._handoff_block`. Three choices and never "nothing": the
// edge is the request, so it always carries something. The narrowed modes need
// the sender's Output Parser (a gate forwards its worker's, see
// `handoffSenderFields`); without one, Full output is the only option. A
// narrowed edge whose sender extracted none of its fields falls back to the
// full output at run time, so a failed extraction never starves the receiver.
const MODES: { mode: EdgeHandoffMode; label: string; hint: string }[] = [
  { mode: 'full', label: 'Full output', hint: 'The answer plus every extracted field.' },
  { mode: 'fields', label: 'Extracted fields only', hint: "The Output Parser's fields, without the prose." },
  { mode: 'selected', label: 'Selected fields', hint: 'Only the fields ticked below.' },
]

export function EdgeHandoffPanel({ edgeId, source }: { edgeId: string; source: string }) {
  const { setEdges } = useReactFlow()
  const nodes = useNodes() as unknown as ProtocolNode[]
  const edges = useEdges() as unknown as ProtocolEdge[]
  const handoff = edges.find((e) => e.id === edgeId)?.data?.handoff ?? { mode: 'full' as const }
  const fields = handoffSenderFields(nodes, edges, source)
  const senderName = displayNames(nodes)[source] ?? source

  function update(next: EdgeHandoff) {
    setEdges((current) =>
      current.map((e) => {
        if (e.id !== edgeId) return e
        const rest = { ...(e.data ?? {}) }
        delete rest.handoff
        return { ...e, data: next.mode === 'full' ? rest : { ...rest, handoff: next } }
      }),
    )
  }

  const selected = handoff.fields ?? []
  function toggleField(name: string, checked: boolean) {
    const nextFields = checked
      ? [...selected, { name }].sort(
          (a, b) => fields.findIndex((f) => f.name === a.name) - fields.findIndex((f) => f.name === b.name),
        )
      : selected.filter((f) => f.name !== name)
    update({ mode: 'selected', fields: nextFields })
  }
  function setItemKeys(name: string, keys: string[]) {
    update({
      mode: 'selected',
      fields: selected.map((f): EdgeHandoffField => {
        if (f.name !== name) return f
        return keys.length ? { name, item_keys: keys } : { name }
      }),
    })
  }

  return (
    <div className="space-y-2.5 text-xs">
      <p className="font-mono text-[0.65rem] tracking-wider text-primary uppercase">What passes</p>
      <div className="space-y-1.5" role="radiogroup" aria-label="What this connection passes">
        {MODES.map(({ mode, label, hint }) => {
          const disabled = mode !== 'full' && fields.length === 0
          return (
            <label
              key={mode}
              className={`flex cursor-pointer items-start gap-2 ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
            >
              <input
                type="radio"
                name={`handoff-${edgeId}`}
                className="mt-0.5 accent-[var(--primary)]"
                checked={handoff.mode === mode}
                disabled={disabled}
                onChange={() => update(mode === 'selected' ? { mode, fields: selected } : { mode })}
              />
              <span>
                <span className="block">{label}</span>
                <span className="block text-muted-foreground">{hint}</span>
              </span>
            </label>
          )
        })}
      </div>
      {fields.length === 0 && (
        <p className="text-muted-foreground">
          {senderName} has no Output Parser, so only its full output can pass.
        </p>
      )}
      {handoff.mode === 'selected' && fields.length > 0 && (
        <div className="space-y-1.5 border-t pt-2">
          {fields.map((field) => {
            const pick = selected.find((f) => f.name === field.name)
            return (
              <div key={field.name} className="space-y-1">
                <label className="flex cursor-pointer items-center gap-2 font-mono">
                  <Checkbox
                    aria-label={field.name}
                    checked={!!pick}
                    onCheckedChange={(checked) => toggleField(field.name, checked === true)}
                  />
                  <span className="break-all">{field.name}</span>
                  <span className="text-muted-foreground">{field.type}</span>
                </label>
                {pick && field.type === 'array' && (
                  <ItemKeysInput
                    field={field.name}
                    keys={pick.item_keys ?? []}
                    onCommit={(keys) => setItemKeys(field.name, keys)}
                  />
                )}
              </div>
            )
          })}
          {selected.length === 0 && <p className="text-destructive">Tick at least one field — publishing refuses an empty selection.</p>}
        </div>
      )}
    </div>
  )
}

// Optional: narrows each list item to these keys (`name` -> just the names).
// Committed on blur so a half-typed comma doesn't reformat under the cursor.
function ItemKeysInput({ field, keys, onCommit }: { field: string; keys: string[]; onCommit: (keys: string[]) => void }) {
  const [text, setText] = useState(keys.join(', '))
  return (
    <Input
      aria-label={`Item keys for ${field}`}
      placeholder="all item keys (or e.g. name, op)"
      className="ml-6 h-6 w-[calc(100%-1.5rem)] font-mono text-xs"
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={() =>
        onCommit(
          text
            .split(',')
            .map((k) => k.trim())
            .filter(Boolean),
        )
      }
    />
  )
}

import { useState } from 'react'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import type { LLMModelInfo } from '@/types/llmSettings'

const NONE_VALUE = '__none__'
const CUSTOM_VALUE = '__custom__'

// The Model picker, shared by LlmNodeInspector and an "llm_config" factor
// level's own row (FactorEditorDialog's LlmConfigLevelRow) -- a factor level
// IS a whole LLM node config (protocol_execution.py's _resolve_llm_config
// reads it verbatim), so the two have to offer exactly the same choices or a
// factor sweep can't express what a single node can.
//
// A dropdown over whatever GET /llm-settings/{provider}/models returned, but
// never ONLY that. Anthropic and Azure Foundry answer with a live listing
// (that provider's own API, per credential), so there "Custom model..." only
// matters for something created after this list was fetched. OpenAI has no
// capability endpoint at all, so its response is a curated static catalog
// living in agentic-core -- see llm_model_discovery.py's docstring for the
// per-provider findings -- which necessarily lags any model released after
// the pinned agentic-core version. With a dropdown alone, a brand-new OpenAI
// model would be unreachable until someone tags a release in another repo.
// The same fallback applies to Anthropic whenever its listing call fails.
//
// An off-catalog id is a real, supported choice, not an error state: the
// backend never validates `model` against the catalog (it's passed straight
// through to the provider), and capability lookup already falls back to
// agentic-core's DEFAULT_CAPABILITIES for anything it doesn't recognize.
export function ModelField({
  id,
  value,
  models,
  isLoading,
  onChange,
}: {
  id?: string
  value: string
  models: LLMModelInfo[]
  isLoading: boolean
  onChange: (model: string) => void
}) {
  const known = models.find((m) => m.id === value)
  // Sticky only for the "I picked Custom while a catalog model was selected"
  // case. An already-saved off-catalog value doesn't need it -- that's derived
  // below, so reopening a node that was configured with a custom model comes
  // back up as free text without any state to restore.
  const [customChosen, setCustomChosen] = useState(false)

  // Free text when there's nothing to list (Azure with no credential, failed
  // discovery), when asked for, or when the current value isn't in the list --
  // that last one matters most: a Select whose value matches none of its items
  // renders blank, so a saved custom model would look unset and be one stray
  // click away from being silently replaced.
  const freeText = models.length === 0 || customChosen || (!!value && !known)

  if (isLoading) return <Skeleton className="h-8 w-full" />

  if (freeText) {
    return (
      <div className="space-y-1">
        <Input id={id} value={value} placeholder="e.g. gpt-5.1" onChange={(e) => onChange(e.target.value)} />
        {models.length > 0 && (
          <button
            type="button"
            className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
            onClick={() => {
              setCustomChosen(false)
              // Clearing is what actually returns the dropdown: leaving an
              // off-catalog value in place would re-derive `freeText` above and
              // flip straight back to this input.
              if (value && !known) onChange('')
            }}
          >
            Choose from the catalog instead
          </button>
        )}
      </div>
    )
  }

  return (
    <Select
      value={value || NONE_VALUE}
      onValueChange={(next) => {
        if (!next || next === NONE_VALUE) return
        // Keep whatever was selected as the starting text rather than blanking
        // it -- "custom" is usually a variant of a listed model (a dated
        // snapshot, a newer point release), so it's an edit, not a fresh start.
        if (next === CUSTOM_VALUE) setCustomChosen(true)
        else onChange(next)
      }}
    >
      <SelectTrigger id={id} className="w-full">
        <SelectValue>{() => known?.label ?? value ?? 'Select a model…'}</SelectValue>
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NONE_VALUE} disabled>
          Select a model…
        </SelectItem>
        {models.map((m) => (
          <SelectItem key={m.id} value={m.id}>
            {m.label ?? m.id}
          </SelectItem>
        ))}
        <SelectItem value={CUSTOM_VALUE}>Custom model…</SelectItem>
      </SelectContent>
    </Select>
  )
}

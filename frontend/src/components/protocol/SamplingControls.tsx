import { useId } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { ModelCapabilities, ResolvedSampling } from '@/types/llmSettings'
import { useSamplingPreview } from './useSamplingPreview'

function sourceLabel(source: ResolvedSampling['temperature_source'] | undefined) {
  return source === 'advertised_default' ? 'Provider default' : source === 'application_fallback' ? 'Fallback' : 'Provider chooses'
}

export function SamplingControls({ capabilities, model, temperature, effort, onChange }: {
  capabilities?: ModelCapabilities
  model: string
  temperature?: number | null
  effort?: string | null
  onChange: (patch: { temperature?: number | null; effort?: string | null }) => void
}) {
  const id = useId()
  const preview = useSamplingPreview(capabilities, model, temperature, effort)
  const defaults = useSamplingPreview(capabilities, model)
  const resolved = defaults.data
  const defaultEffort = resolved?.effort ?? 'provider chooses'
  const levels = capabilities?.effort_levels ?? (capabilities?.default_effort ? [capabilities.default_effort] : [])
  return <>
    {capabilities?.supports_temperature === true && <div className="space-y-1.5">
      <Label htmlFor={`${id}-temperature`}>Temperature</Label>
      <Input id={`${id}-temperature`} type="number" step="0.1" min="0" max="2"
        value={temperature ?? ''}
        placeholder={resolved?.temperature == null ? 'Provider chooses' : `Default (${resolved.temperature})`}
        onChange={(event) => onChange({ temperature: event.target.value === '' ? null : Number(event.target.value) })} />
      {temperature == null ? <p className="text-xs text-muted-foreground">
        {sourceLabel(preview.data?.temperature_source)}{preview.data?.temperature != null ? `: ${preview.data.temperature}` : ''}
      </p> : <Button type="button" variant="ghost" size="sm" onClick={() => onChange({ temperature: null })}>Use default temperature</Button>}
      {capabilities.temperature_effort_levels && <p className="text-xs text-muted-foreground">
        Temperature requires effort: {capabilities.temperature_effort_levels.join(', ') || 'unavailable'}.
      </p>}
    </div>}
    {capabilities?.supports_effort === true && <div className="space-y-1.5">
      <Label htmlFor={`${id}-effort`}>Effort</Label>
      <Select value={effort ?? '__default__'} onValueChange={(value) => onChange({ effort: value === '__default__' ? null : value })}>
        <SelectTrigger id={`${id}-effort`} className="w-full">
          <SelectValue>{(value: string) => value === '__default__' ? `Default (${defaultEffort})` : value}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__default__">Default ({defaultEffort})</SelectItem>
          {levels.map((level) => <SelectItem key={level} value={level}>{level}</SelectItem>)}
        </SelectContent>
      </Select>
      {effort == null && <p className="text-xs text-muted-foreground">{sourceLabel(resolved?.effort_source)}</p>}
    </div>}
    {capabilities?.supports_effort === false && effort != null && <Button type="button" variant="outline" size="sm"
      onClick={() => onChange({ effort: null })}>Clear unsupported effort</Button>}
    {preview.error && <p role="alert" className="col-span-full text-xs text-destructive">{preview.error.message}</p>}
  </>
}

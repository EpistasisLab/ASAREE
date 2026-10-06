import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowDown, ArrowUp } from 'lucide-react'
import { skillsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { DesignFactor } from '@/types/experiments'

export function SkillFactorEditor({ factor, open, onOpenChange, onSave, onRemove }: {
  factor: DesignFactor
  open: boolean
  onOpenChange: (open: boolean) => void
  onSave: (factor: DesignFactor) => void | Promise<unknown>
  onRemove?: () => Promise<unknown>
}) {
  const [draft, setDraft] = useState(factor)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const skills = useQuery({ queryKey: ['skills'], queryFn: () => skillsApi.list() })
  const labels = draft.level_labels ?? []
  function move(index: number, offset: number) {
    const levels = [...draft.levels]
    const nextLabels = [...labels]
    ;[levels[index], levels[index + offset]] = [levels[index + offset], levels[index]]
    ;[nextLabels[index], nextLabels[index + offset]] = [nextLabels[index + offset], nextLabels[index]]
    setDraft({ ...draft, levels, level_labels: nextLabels })
  }
  async function commit(remove = false) {
    setSaving(true)
    setError('')
    try {
      if (remove) await onRemove?.()
      else await onSave({ ...draft, name: draft.name.trim(), level_labels: labels.map((label) => label.trim()) })
      onOpenChange(false)
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Could not save the skill factor.')
    } finally { setSaving(false) }
  }
  const valid = draft.name.trim() && labels.length === draft.levels.length && labels.every((label) => label.trim()) && new Set(labels.map((label) => label.trim())).size === labels.length
  return <Dialog open={open} onOpenChange={(open) => !saving && onOpenChange(open)}>
    <DialogContent className="sm:max-w-xl">
      <DialogHeader>
        <DialogTitle>Skills as levels</DialogTitle>
        <DialogDescription>Each cell receives exactly one connected skill. Add or disconnect Skill nodes on the canvas to change the levels.</DialogDescription>
      </DialogHeader>
      <div className="space-y-3">
        <Label htmlFor="skill-factor-name">Factor name</Label>
        <Input id="skill-factor-name" value={draft.name} disabled={saving} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
        {draft.levels.length < 2 && <p className="text-xs text-destructive">Incomplete: connect at least two different skills before generating cells.</p>}
        <div className="max-h-96 space-y-2 overflow-y-auto">
          {draft.levels.map((level, index) => {
            const id = (level as string[])[0]
            const skill = skills.data?.find((skill) => skill.id === id)
            return <div key={id} className="flex items-center gap-2 rounded-md border p-2">
              <div className="min-w-0 flex-1 space-y-1">
                <p className="font-mono text-xs">{skill?.name ?? labels[index] ?? 'Unavailable skill'}{index === 0 ? ' · default test selection' : ''}</p>
                {skills.isSuccess && !skill && <p className="text-xs text-destructive">Unavailable: disconnect or restore this skill.</p>}
                <Input aria-label={`Level ${index + 1} label`} value={labels[index] ?? ''} disabled={saving} onChange={(event) => setDraft({ ...draft, level_labels: labels.map((label, i) => i === index ? event.target.value : label) })} />
              </div>
              <Button variant="ghost" size="icon-sm" aria-label="Move level up" disabled={saving || index === 0} onClick={() => move(index, -1)}><ArrowUp className="size-3.5" /></Button>
              <Button variant="ghost" size="icon-sm" aria-label="Move level down" disabled={saving || index === draft.levels.length - 1} onClick={() => move(index, 1)}><ArrowDown className="size-3.5" /></Button>
            </div>
          })}
        </div>
        {skills.isError && <p className="text-xs text-destructive">Could not load the skill library.</p>}
        <p className="text-xs text-muted-foreground">Individual skill switches are controlled by this connector. Changes require design review and regeneration.</p>
        {onRemove && <div className="space-y-1"><Button variant="destructive" size="sm" disabled={saving} onClick={() => void commit(true)}>Remove factor</Button><p className="text-xs text-muted-foreground">Removing this factor enables all connected skills and restores their individual controls.</p></div>}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      </div>
      <DialogFooter><Button variant="outline" disabled={saving} onClick={() => onOpenChange(false)}>Cancel</Button><Button disabled={saving || !valid} onClick={() => void commit()}>{saving ? 'Saving…' : 'Save factor'}</Button></DialogFooter>
    </DialogContent>
  </Dialog>
}

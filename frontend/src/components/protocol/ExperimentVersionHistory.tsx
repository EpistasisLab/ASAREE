import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { protocolsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import type { ProtocolRevision } from '@/types/protocols'
import { versionLabel } from '@/lib/experimentVersion'

function changeSummary(version: ProtocolRevision, previous?: ProtocolRevision) {
  if (!previous) return 'First published version'
  const changes: string[] = []
  if (JSON.stringify(version.graph) !== JSON.stringify(previous.graph)) changes.push('Canvas')
  for (const [key, label] of [['hypothesis', 'Hypothesis'], ['design_type', 'Design type'], ['design_spec', 'Design'], ['measurement_plan', 'Measurement plan'], ['task_brief', 'Task brief']] as const) {
    if (JSON.stringify(version.experiment_snapshot?.[key]) !== JSON.stringify(previous.experiment_snapshot?.[key])) changes.push(label)
  }
  if (version.design_revision_id !== previous.design_revision_id) changes.push('Generated cells')
  return changes.length ? `Changed: ${changes.join(', ')}` : 'Experiment definition unchanged'
}

function VersionEntry({ version, previous, current, selected, protocolId, onSelect }: {
  version: ProtocolRevision; previous?: ProtocolRevision; current: boolean; selected: boolean;
  protocolId: string; onSelect: (id: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(version.name ?? '')
  const [note, setNote] = useState(version.note ?? '')
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: () => protocolsApi.updateRevision(protocolId, version.id, { name: name.trim() || null, note: note.trim() || null }),
    onSuccess: updated => {
      queryClient.setQueryData<ProtocolRevision[]>(['protocols', protocolId, 'revisions'], versions => versions?.map(item => item.id === updated.id ? updated : item))
      setEditing(false)
    },
  })
  return <Card className="gap-2 p-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="min-w-0 break-words text-sm font-medium"><span className="font-mono">Version {version.revision}</span>{version.name ? ` · ${version.name}` : ''}</span>
      {current && <span className="text-xs text-primary">Production</span>}
      <span className="font-mono text-xs text-muted-foreground" title={new Date(version.published_at).toLocaleString()}>{new Date(version.published_at).toLocaleDateString()}</span>
    </div>
    <p className="font-mono text-xs text-muted-foreground">{version.run_count ?? 0} runs · {version.result_count ?? 0} with results</p>
    <div className="flex flex-wrap gap-2">
      <Button variant="outline" size="sm" aria-pressed={selected} onClick={() => onSelect(version.id)}>View {versionLabel(version)}</Button>
      <Button variant="ghost" size="sm" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? 'Hide details' : 'Details'}</Button>
    </div>
    {expanded && <div className="space-y-2 border-t pt-2">
      <p className="text-xs text-muted-foreground">{changeSummary(version, previous)}</p>
      {editing ? <form className="space-y-2" onSubmit={event => { event.preventDefault(); mutation.mutate() }}>
        <label className="block space-y-1 text-xs">Version name (optional)<Input maxLength={120} value={name} onChange={event => setName(event.target.value)} /></label>
        <label className="block space-y-1 text-xs">Version note (optional)<Textarea maxLength={4000} value={note} onChange={event => setNote(event.target.value)} /></label>
        {mutation.isError && <p role="alert" className="text-xs text-destructive">Could not save version details. Try again.</p>}
        <div className="flex gap-2"><Button size="sm" type="submit" disabled={mutation.isPending}>{mutation.isPending ? 'Saving…' : 'Save details'}</Button><Button size="sm" variant="outline" type="button" disabled={mutation.isPending} onClick={() => setEditing(false)}>Cancel</Button></div>
      </form> : <>
        <p className="whitespace-pre-wrap break-words text-sm">{version.note || 'No note yet.'}</p>
        <Button variant="ghost" size="sm" onClick={() => { setName(version.name ?? ''); setNote(version.note ?? ''); mutation.reset(); setEditing(true) }}>Edit name and note</Button>
      </>}
    </div>}
  </Card>
}

export function ExperimentVersionHistory({ versions, protocolId, publishedId, selectedId, search, onSearch, onSelect }: {
  versions: ProtocolRevision[]; protocolId: string; publishedId?: string | null; selectedId: string;
  search: string; onSearch: (value: string) => void; onSelect: (id: string) => void
}) {
  const [filter, setFilter] = useState('all')
  const [visibleCount, setVisibleCount] = useState(10)
  useEffect(() => setVisibleCount(10), [search, filter])
  const term = search.trim().toLowerCase()
  const matches = versions.filter(item => (
    !term || `${versionLabel(item)} ${item.note ?? ''} ${item.published_at.slice(0, 10)} ${new Date(item.published_at).toLocaleDateString()}`.toLowerCase().includes(term)
  ) && (filter === 'all' || (filter === 'runs' ? (item.run_count ?? 0) > 0 : (item.result_count ?? 0) > 0)))
  const current = versions.find(item => item.id === publishedId)
  const visible = current ? [current, ...matches.filter(item => item.id !== current.id)] : matches
  return <>
    <Input aria-label="Search versions" placeholder="Search names, notes, version numbers or dates…" value={search} onChange={event => onSearch(event.target.value)} />
    <div className="flex flex-wrap gap-2" aria-label="Version filters">
      {([['all', 'All'], ['runs', 'With runs'], ['results', 'With results']] as const).map(([value, label]) => <Button key={value} size="sm" variant={filter === value ? 'secondary' : 'outline'} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</Button>)}
    </div>
    <p className="text-xs text-muted-foreground">{matches.length} of {versions.length} versions · newest first</p>
    {current && <p className="text-xs text-muted-foreground">The production version stays at the top.</p>}
    <div className="max-h-[50vh] space-y-2 overflow-y-auto p-1" aria-label="Matching versions">
      {visible.slice(0, visibleCount).map(item => <VersionEntry key={item.id} version={item} previous={versions[versions.indexOf(item) + 1]} current={item.id === publishedId} selected={item.id === selectedId} protocolId={protocolId} onSelect={onSelect} />)}
      {visible.length > visibleCount && <Button variant="outline" size="sm" onClick={() => setVisibleCount(visibleCount + 10)}>Show more versions</Button>}
      {!matches.length && <p role="status" className="py-3 text-sm text-muted-foreground">No versions match your search.</p>}
    </div>
  </>
}

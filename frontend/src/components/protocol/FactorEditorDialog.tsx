import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, Split, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { datasetsApi, mcpServersApi } from '@/api/client'
import { cardAccent, cn, hashToChartHue, HUD_ACCENT_RING_CLASSNAME } from '@/lib/utils'
import { pickToolNamesForServer, selectableMcpServers, type UnboundField } from './bindableFields'
import { useProviderModels } from './useProviderModels'
import {
  computeFactorName,
  emptyStructuredLevel,
  isStructuredLevelType,
  LEVEL_TYPE_LABELS,
  levelTypeOf,
  parseLevelValue,
  seedLevels,
  seedStructuredLevels,
  type LevelType,
} from './factorLevels'
import { ModelField } from './ModelField'
import { NODE_INSPECTOR_CONTENT_CLASSNAME } from './NodeInspectorDialog'
import { PROVIDER_META } from './nodes/LlmNode'
import { PythonCodeEditor } from './PythonCodeEditor'
import type { DesignFactor } from '@/types/experiments'

const LEVEL_TYPES: LevelType[] = [
  'string',
  'text',
  'number',
  'boolean',
  'llm_config',
  'tool_config',
  'pattern',
  'script_config',
  'dataset_config',
]
const EFFORT_LEVELS_FALLBACK = ['low', 'medium', 'high', 'xhigh', 'max']
const PATTERN_OPTIONS = [
  { slug: 'reason_act', label: 'Reason + Act' },
  { slug: 'single_agent_baseline', label: 'Single-Agent Baseline' },
]

type StructuredLevel = Record<string, unknown>

// One row of an "llm_config" factor's levels -- mirrors LlmNodeInspector's
// own Provider/Model/Temperature/Effort/Max tokens fields exactly, since a
// level here IS a whole LLM node's config (protocol_execution.py's
// _resolve_llm_config reads it verbatim, never the node's xyflow type).
function LlmConfigLevelRow({ value, onChange }: { value: StructuredLevel; onChange: (next: StructuredLevel) => void }) {
  const provider = (value.provider as string) || 'anthropic'
  const { modelsQuery, models } = useProviderModels(provider)
  const selectedModelInfo = models.find((m) => m.id === value.model)
  const showEffort = selectedModelInfo?.supports_effort ?? false
  const effortLevels = selectedModelInfo?.effort_levels.length ? selectedModelInfo.effort_levels : EFFORT_LEVELS_FALLBACK

  function patch(patch: StructuredLevel) {
    onChange({ ...value, ...patch })
  }

  return (
    <div className="grid grid-cols-2 gap-2 rounded-lg border p-2">
      <div className="space-y-1">
        <Label className="text-xs">Provider</Label>
        <Select value={provider} onValueChange={(v) => v && patch({ provider: v, model: '' })}>
          <SelectTrigger className="h-8 w-full">
            <SelectValue>{() => PROVIDER_META[provider]?.label ?? provider}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {Object.keys(PROVIDER_META).map((p) => (
              <SelectItem key={p} value={p}>
                {PROVIDER_META[p].label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1">
        <Label className="text-xs">Model</Label>
        <ModelField
          value={(value.model as string) ?? ''}
          models={models}
          isLoading={modelsQuery.isLoading}
          onChange={(model) => patch({ model })}
        />
      </div>
      <div className="space-y-1">
        <Label className="text-xs">Temperature</Label>
        <Input
          className="h-8"
          type="number"
          step="0.1"
          min="0"
          max="2"
          value={(value.temperature as number | undefined) ?? ''}
          onChange={(e) => patch({ temperature: e.target.value === '' ? null : Number(e.target.value) })}
        />
      </div>
      {showEffort && (
        <div className="space-y-1">
          <Label className="text-xs">Effort</Label>
          <Select value={(value.effort as string) || '__none__'} onValueChange={(v) => patch({ effort: v === '__none__' ? null : v })}>
            <SelectTrigger className="h-8 w-full">
              <SelectValue>{(v: string) => (v === '__none__' ? '(none)' : v)}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">(none)</SelectItem>
              {effortLevels.map((level) => (
                <SelectItem key={level} value={level}>
                  {level}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
      <div className="space-y-1">
        <Label className="text-xs">Max tokens</Label>
        <Input
          className="h-8"
          type="number"
          min="1"
          max="200000"
          value={(value.max_tokens as number | undefined) ?? ''}
          onChange={(e) =>
            patch({ max_tokens: Math.min(200000, Math.max(1, Math.trunc(Number(e.target.value)) || 1)) })
          }
        />
      </div>
    </div>
  )
}

// One row of a "tool_config" factor's levels -- mirrors McpToolNodeInspector's
// own Server/Tools-allowed fields; a level here is a whole mcp_tool node's
// config (protocol_execution.py's _resolve_tool_config reads each wired
// node's own config fresh).
function ToolConfigLevelRow({ value, onChange }: { value: StructuredLevel; onChange: (next: StructuredLevel) => void }) {
  const serversQuery = useQuery({ queryKey: ['mcp-servers'], queryFn: () => mcpServersApi.list() })
  const servers = selectableMcpServers(serversQuery.data ?? [], value.server_id as string | undefined)
  const selectedServer = servers.find((s) => s.id === value.server_id)
  const tools = selectedServer?.capabilities?.tools ?? []
  const selectedTools = (value.tool_names as string[] | undefined) ?? []

  function patch(patch: StructuredLevel) {
    onChange({ ...value, ...patch })
  }

  function toggleTool(name: string, allowed: boolean) {
    patch({ tool_names: allowed ? [...selectedTools, name] : selectedTools.filter((t) => t !== name) })
  }

  return (
    <div className="space-y-2 rounded-lg border p-2">
      <div className="space-y-1">
        <Label className="text-xs">Server</Label>
        <Select
          value={(value.server_id as string) || '__none__'}
          onValueChange={(v) => {
            if (!v || v === '__none__') return
            const server = servers.find((s) => s.id === v)
            const availableToolNames = server?.capabilities?.tools?.map((t) => t.name) ?? []
            patch({
              server_id: v,
              server_name: server?.name ?? null,
              tool_names: pickToolNamesForServer(selectedTools, availableToolNames),
            })
          }}
        >
          <SelectTrigger className="h-8 w-full">
            <SelectValue>{() => selectedServer?.name ?? 'Select a server…'}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__none__" disabled>
              Select a server…
            </SelectItem>
            {servers.map((s) => (
              <SelectItem key={s.id} value={s.id}>
                {s.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {!value.server_id ? (
        <p className="text-xs text-muted-foreground">Pick a server first.</p>
      ) : tools.length === 0 ? (
        <p className="text-xs text-muted-foreground">This server has no tools.</p>
      ) : (
        <div className="max-h-32 space-y-0.5 overflow-y-auto rounded-md border p-1">
          {tools.map((tool) => (
            <div key={tool.name} className="flex items-center justify-between gap-2 rounded px-1.5 py-1 text-xs hover:bg-muted">
              <span className="truncate">{tool.name}</span>
              <Switch size="sm" checked={selectedTools.includes(tool.name)} onCheckedChange={(allowed) => toggleTool(tool.name, allowed)} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// One row of a "pattern" factor's levels -- a whole {execution_pattern,
// pattern_params} payload, mirroring ReasonActPatternNodeInspector/
// SingleAgentBaselinePatternNodeInspector's own fields conditionally by
// slug. Written onto the agent's own synthetic data.pattern_override (see
// bindableFields.ts) -- protocol_execution.py's _resolve_pattern_config
// checks this before falling back to the wired connector node.
function PatternLevelRow({ value, onChange }: { value: StructuredLevel; onChange: (next: StructuredLevel) => void }) {
  const slug = (value.execution_pattern as string) || 'reason_act'
  const allParams = (value.pattern_params as Record<string, StructuredLevel> | undefined) ?? {}
  const params = allParams[slug] ?? {}

  function patchParams(patch: StructuredLevel) {
    onChange({ execution_pattern: slug, pattern_params: { [slug]: { ...params, ...patch } } })
  }

  function changeSlug(nextSlug: string) {
    onChange({ execution_pattern: nextSlug, pattern_params: { [nextSlug]: allParams[nextSlug] ?? {} } })
  }

  return (
    <div className="space-y-2 rounded-lg border p-2">
      <div className="space-y-1">
        <Label className="text-xs">Pattern</Label>
        <Select value={slug} onValueChange={(v) => v && changeSlug(v)}>
          <SelectTrigger className="h-8 w-full">
            <SelectValue>{() => PATTERN_OPTIONS.find((p) => p.slug === slug)?.label ?? slug}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {PATTERN_OPTIONS.map((p) => (
              <SelectItem key={p.slug} value={p.slug}>
                {p.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-1">
        <Label className="text-xs">Max iterations</Label>
        <Input
          className="h-8"
          type="number"
          min="1"
          value={(params.max_iterations as number | undefined) ?? ''}
          onChange={(e) => patchParams({ max_iterations: Number(e.target.value) })}
        />
      </div>
      {slug === 'reason_act' && (
        <div className="flex items-center justify-between rounded-md border px-2 py-1.5">
          <Label className="text-xs">Include scratchpad</Label>
          <Switch size="sm" checked={(params.include_scratchpad as boolean | undefined) ?? true} onCheckedChange={(checked) => patchParams({ include_scratchpad: checked })} />
        </div>
      )}
      {slug === 'single_agent_baseline' && (
        <div className="flex items-center justify-between rounded-md border px-2 py-1.5">
          <Label className="text-xs">Stop on first success</Label>
          <Switch
            size="sm"
            checked={(params.stop_on_first_success as boolean | undefined) ?? true}
            onCheckedChange={(checked) => patchParams({ stop_on_first_success: checked })}
          />
        </div>
      )}
    </div>
  )
}

// One row of a "script_config" factor's levels -- mirrors ScriptNodeInspector's
// own Name/Language/Code fields exactly, since a level here IS a whole
// Script node's config (protocol_execution.py's _resolve_script_config
// reads it verbatim, never the node's xyflow type). Python-only for v1,
// same as ScriptNodeInspector's own fixed "Language: Python" label.
function ScriptConfigLevelRow({ value, onChange }: { value: StructuredLevel; onChange: (next: StructuredLevel) => void }) {
  function patch(patch: StructuredLevel) {
    onChange({ ...value, ...patch })
  }

  return (
    <div className="space-y-2 rounded-lg border p-2">
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <Label className="text-xs">Name</Label>
          <Input className="h-8" value={(value.name as string) ?? ''} onChange={(e) => patch({ name: e.target.value })} />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Language</Label>
          <p className="rounded-md border border-dashed px-2 py-1.5 text-xs text-muted-foreground">Python</p>
        </div>
      </div>
      <div className="space-y-1">
        <Label className="text-xs">Code</Label>
        <PythonCodeEditor value={(value.code as string) ?? ''} onChange={(code) => patch({ code })} rows={10} />
      </div>
    </div>
  )
}

// One row of a "dataset_config" factor's levels -- a whole Dataset node
// config, so each level is one registered dataset this experiment runs
// against (protocol_execution.py's _resolve_dataset_configs reads the wired
// node's own, already factor-patched, config verbatim and seeds the cell's
// workspace from `dataset_name`).
//
// This is the one structured row that does NOT mirror its inspector's own
// controls: DatasetNodeInspector deliberately has no "which dataset" picker
// at all (the dataset IS the node, chosen from the canvas's Datasets
// browser). A factor's levels have no node to be, so the picker has to live
// here -- it's the only place in the app where "which dataset" is a choice
// rather than an identity. Deliberately narrow: name, target column and
// split state are the three things that decide whether two datasets are
// comparable, and the full read-out stays in the inspector for the node's
// own base level.
function DatasetConfigLevelRow({
  value,
  onChange,
  usedElsewhere,
}: {
  value: StructuredLevel
  onChange: (next: StructuredLevel) => void
  // Dataset ids already picked by this factor's OTHER levels -- greyed out
  // rather than removed, same convention as FactorBindableField's own
  // levelOptions Select: two levels naming one dataset wouldn't vary
  // anything between their cells, but hiding the option would leave no clue
  // why it's gone.
  usedElsewhere: Set<string>
}) {
  const datasetsQuery = useQuery({ queryKey: ['datasets'], queryFn: () => datasetsApi.list() })
  const datasets = datasetsQuery.data ?? []
  const selected = datasets.find((d) => d.id === value.dataset_id)

  return (
    <div className="space-y-2 rounded-lg border p-2">
      <div className="space-y-1">
        <Label className="text-xs">Dataset</Label>
        <Select
          value={(value.dataset_id as string) || '__none__'}
          onValueChange={(v) => {
            if (!v || v === '__none__') return
            const dataset = datasets.find((d) => d.id === v)
            // id AND name together -- the executor resolves by name, the
            // canvas's dataset_ids sync by id (see factorLevels.ts's
            // emptyStructuredLevel). Writing one without the other would
            // leave one of the two silently broken.
            onChange({ ...value, dataset_id: v, dataset_name: dataset?.name ?? null })
          }}
        >
          <SelectTrigger className="h-8 w-full">
            <SelectValue>
              {() => selected?.name ?? (value.dataset_name as string) ?? 'Select a dataset…'}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__none__" disabled>
              {datasetsQuery.isLoading ? 'Loading…' : datasets.length === 0 ? 'No registered datasets' : 'Select a dataset…'}
            </SelectItem>
            {datasets.map((d) => (
              <SelectItem key={d.id} value={d.id} disabled={d.id !== value.dataset_id && usedElsewhere.has(d.id)}>
                {d.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {selected ? (
        <div className="flex flex-wrap items-center gap-1.5 font-mono text-xs">
          <Badge variant="outline">Target: {selected.target_column ?? '—'}</Badge>
          {/* A dataset with no train_path has never been split, and a cell
              can't open a workspace on it -- worth catching here rather
              than at Run time, when it costs a real attempt. */}
          <Badge variant="outline" className={selected.train_path ? undefined : 'text-destructive'}>
            {selected.train_path ? 'Split' : 'Not split yet'}
          </Badge>
        </div>
      ) : value.dataset_name ? (
        <p className="text-xs text-muted-foreground">
          This level names <span className="font-mono">{String(value.dataset_name)}</span>, which is not in your
          library — pick a replacement.
        </p>
      ) : null}
      <div className="flex items-center justify-between rounded-md border px-2 py-1.5">
        <Label className="text-xs">Enabled</Label>
        <Switch
          size="sm"
          checked={(value.enabled as boolean | undefined) ?? true}
          onCheckedChange={(enabled) => onChange({ ...value, enabled })}
        />
      </div>
    </div>
  )
}

// A per-factor editor with real room -- reuses the node inspector's own
// fixed near-fullscreen frame sizing (NODE_INSPECTOR_CONTENT_CLASSNAME) and
// HUD glow/ring/corner-brackets (HUD_ACCENT_RING_CLASSNAME), but is built
// directly on Dialog/DialogContent rather than on NodeInspectorDialog
// itself: that component's transparency slider and onDelete are node-
// specific concepts (peeking at the canvas *around a specific node*,
// deleting *that node*) that don't map onto a factor, which isn't a canvas
// element and may be bound to zero, one, or many node fields scattered
// across the graph. Deleting a factor already has an obvious home (the
// remove button on DesignTab's own summary row), so this dialog only ever
// edits, never deletes.
export function FactorEditorDialog({
  open,
  onOpenChange,
  factor,
  pickableFields,
  existingNames,
  emptyPickerMessage,
  onSave,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  factor: DesignFactor
  // Only passed by DesignTab's "Add factor" entry point or a node's own
  // hover-toolbar "Make experimental factor" icon -- every bindable field
  // (on the whole canvas, or scoped to just that one node) that isn't
  // already bound to something. Lives here (not a separate small popover
  // before this dialog even opens) since a canvas can realistically have a
  // large number of fields to search through, and this dialog already has
  // the room a cramped popover wouldn't.
  pickableFields?: UnboundField[]
  // Needed to dedupe the computed name once a field is picked -- only
  // meaningful alongside pickableFields.
  existingNames?: string[]
  // Shown when pickableFields is an empty array -- defaults to the
  // whole-canvas wording; a node-scoped picker (the hover-toolbar entry
  // point) passes something node-specific instead, since "already a
  // factor" isn't accurate for a node type with nothing bindable on it at
  // all (e.g. a Pattern connector node).
  emptyPickerMessage?: string
  // `field` is only present when this save came from picking one of
  // pickableFields -- the caller uses it to write the binding onto the
  // actual canvas node (this dialog has no way to do that itself).
  onSave: (factor: DesignFactor, field?: UnboundField) => void
}) {
  const [selectedField, setSelectedField] = useState<UnboundField | null>(null)
  const [search, setSearch] = useState('')
  const [levelType, setLevelType] = useState<LevelType>(levelTypeOf(factor))
  const [levels, setLevels] = useState<unknown[]>(() => (isStructuredLevelType(levelTypeOf(factor)) ? factor.levels : factor.levels.map((l) => String(l))))

  // Re-seed the local draft every time this dialog (re)opens -- same
  // "re-seed on open" convention DesignTab.tsx and FactorBindableField.tsx's
  // popover already use. In field-picker mode there's nothing to seed yet
  // until a field is actually picked (see pickField below).
  useEffect(() => {
    if (open) {
      setSelectedField(null)
      setSearch('')
      if (!pickableFields) {
        const t = levelTypeOf(factor)
        setLevelType(t)
        setLevels(isStructuredLevelType(t) ? factor.levels : factor.levels.map((l) => String(l)))
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, factor, pickableFields])

  function pickField(field: UnboundField) {
    setSelectedField(field)
    setLevelType(field.levelType)
    setLevels(isStructuredLevelType(field.levelType) ? seedStructuredLevels(field.currentValue, field.levelType) : seedLevels(field.currentValue))
  }

  function changeLevelType(next: LevelType) {
    setLevelType(next)
    // Boolean levels are fixed ([true, false], nothing to type); the 3
    // structured kinds carry objects, never strings -- any of these
    // transitions (into/out of boolean, into/out of a structured kind)
    // can't carry the old levels forward, so both directions just reset to
    // that type's own default starting levels.
    if (next === 'boolean' || levelType === 'boolean' || isStructuredLevelType(next) || isStructuredLevelType(levelType)) {
      setLevels(next === 'boolean' ? [true, false] : isStructuredLevelType(next) ? [emptyStructuredLevel(next), emptyStructuredLevel(next)] : ['', ''])
    }
  }

  // Computed, not user-typed (see factorLevels.ts's computeFactorName) --
  // standardized so the same field label on two different nodes (e.g. two
  // Agents' own "System prompt") can never collide into one shared factor
  // by accident. Outside field-picker mode this is just whatever was
  // already computed when the factor was first bound to its field.
  const name = pickableFields ? (selectedField ? computeFactorName(selectedField.nodeLabel, selectedField.fieldLabel, existingNames ?? []) : '') : factor.name
  const needsFieldPick = !!pickableFields && !selectedField

  function save() {
    const parsedLevels = isStructuredLevelType(levelType)
      ? levels
      : levelType === 'boolean'
        ? [true, false]
        : (levels as string[]).filter((l) => l.trim() !== '').map((l) => parseLevelValue(l, levelType))
    onSave({ name, levels: parsedLevels, level_type: levelType }, selectedField ?? undefined)
    onOpenChange(false)
  }

  const accent = hashToChartHue(name || 'factor')
  const filteredFields = (pickableFields ?? []).filter((f) =>
    `${f.nodeLabel}:${f.fieldLabel}`.toLowerCase().includes(search.trim().toLowerCase()),
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        style={cardAccent(accent)}
        className={cn(NODE_INSPECTOR_CONTENT_CLASSNAME, HUD_ACCENT_RING_CLASSNAME)}
      >
        <div className="flex shrink-0 items-center justify-between border-b px-4 py-3">
          <div className="flex items-center gap-2">
            <Split className="size-5" style={{ color: accent }} />
            <h2 className="text-lg font-semibold">{name || 'New factor'}</h2>
          </div>
          <Button variant="outline" size="icon" aria-label="Close" onClick={() => onOpenChange(false)}>
            <X className="size-4" />
          </Button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {needsFieldPick ? (
            <div className="space-y-1.5">
              <Label>Bind to a field on the canvas</Label>
              <Input autoFocus placeholder="Search fields…" value={search} onChange={(e) => setSearch(e.target.value)} />
              <div className="max-h-96 space-y-0.5 overflow-y-auto rounded-lg border p-1.5">
                {filteredFields.length === 0 && (
                  <p className="py-4 text-center text-sm text-muted-foreground">
                    {pickableFields?.length === 0
                      ? (emptyPickerMessage ?? 'Every bindable field on the canvas is already a factor.')
                      : 'No matching fields.'}
                  </p>
                )}
                {filteredFields.map((field) => (
                  <button
                    key={`${field.nodeId}.${field.fieldPath}`}
                    type="button"
                    onClick={() => pickField(field)}
                    className="flex w-full cursor-pointer items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted"
                  >
                    <span className="truncate">
                      {field.nodeLabel}:{field.fieldLabel}
                    </span>
                    <Badge variant="outline" className="shrink-0">
                      {LEVEL_TYPE_LABELS[field.levelType]}
                    </Badge>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <Label>Factor name</Label>
                  <p className="rounded-md border border-dashed px-2.5 py-1.5 text-sm text-muted-foreground">{name}</p>
                  {pickableFields && (
                    <button
                      type="button"
                      className="cursor-pointer text-xs text-muted-foreground underline hover:text-foreground"
                      onClick={() => setSelectedField(null)}
                    >
                      Change field
                    </button>
                  )}
                </div>
                <div className="space-y-1.5">
                  <Label>Level type</Label>
                  <Select value={levelType} onValueChange={(value) => value && changeLevelType(value as LevelType)}>
                    <SelectTrigger className="w-full">
                      <SelectValue>{() => LEVEL_TYPE_LABELS[levelType]}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {LEVEL_TYPES.map((t) => (
                        <SelectItem key={t} value={t}>
                          {LEVEL_TYPE_LABELS[t]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="space-y-1.5">
                <Label>Levels</Label>
                {levelType === 'boolean' ? (
                  <p className="text-xs text-muted-foreground">Levels: true, false</p>
                ) : levelType === 'text' ? (
                  <div className="space-y-2">
                    {(levels as string[]).map((level, i) => (
                      <div key={i} className="flex items-start gap-1.5">
                        <Textarea
                          rows={6}
                          className="font-mono text-xs"
                          value={level}
                          onChange={(e) => setLevels((ls) => ls.map((l, j) => (j === i ? e.target.value : l)))}
                        />
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label="Remove level"
                          onClick={() => setLevels((ls) => ls.filter((_, j) => j !== i))}
                        >
                          <X className="size-3.5" />
                        </Button>
                      </div>
                    ))}
                    <Button variant="outline" size="sm" onClick={() => setLevels((ls) => [...ls, ''])}>
                      <Plus className="size-3.5" /> Add level
                    </Button>
                  </div>
                ) : isStructuredLevelType(levelType) ? (
                  <div className="space-y-2">
                    {levels.map((level, i) => (
                      <div key={i} className="flex items-start gap-1.5">
                        <div className="flex-1">
                          {levelType === 'llm_config' ? (
                            <LlmConfigLevelRow
                              value={level as StructuredLevel}
                              onChange={(next) => setLevels((ls) => ls.map((l, j) => (j === i ? next : l)))}
                            />
                          ) : levelType === 'tool_config' ? (
                            <ToolConfigLevelRow
                              value={level as StructuredLevel}
                              onChange={(next) => setLevels((ls) => ls.map((l, j) => (j === i ? next : l)))}
                            />
                          ) : levelType === 'pattern' ? (
                            <PatternLevelRow
                              value={level as StructuredLevel}
                              onChange={(next) => setLevels((ls) => ls.map((l, j) => (j === i ? next : l)))}
                            />
                          ) : levelType === 'dataset_config' ? (
                            <DatasetConfigLevelRow
                              value={level as StructuredLevel}
                              usedElsewhere={
                                new Set(
                                  levels
                                    .filter((_, j) => j !== i)
                                    .map((l) => (l as StructuredLevel).dataset_id)
                                    .filter((id): id is string => typeof id === 'string'),
                                )
                              }
                              onChange={(next) => setLevels((ls) => ls.map((l, j) => (j === i ? next : l)))}
                            />
                          ) : (
                            <ScriptConfigLevelRow
                              value={level as StructuredLevel}
                              onChange={(next) => setLevels((ls) => ls.map((l, j) => (j === i ? next : l)))}
                            />
                          )}
                        </div>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label="Remove level"
                          onClick={() => setLevels((ls) => ls.filter((_, j) => j !== i))}
                        >
                          <X className="size-3.5" />
                        </Button>
                      </div>
                    ))}
                    <Button variant="outline" size="sm" onClick={() => setLevels((ls) => [...ls, emptyStructuredLevel(levelType)])}>
                      <Plus className="size-3.5" /> Add level
                    </Button>
                  </div>
                ) : (
                  <div className="space-y-1.5">
                    {(levels as string[]).map((level, i) => (
                      <div key={i} className="flex items-center gap-1.5">
                        <Input
                          type={levelType === 'number' ? 'number' : 'text'}
                          value={level}
                          onChange={(e) => setLevels((ls) => ls.map((l, j) => (j === i ? e.target.value : l)))}
                        />
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label="Remove level"
                          onClick={() => setLevels((ls) => ls.filter((_, j) => j !== i))}
                        >
                          <X className="size-3.5" />
                        </Button>
                      </div>
                    ))}
                    <Button variant="outline" size="sm" onClick={() => setLevels((ls) => [...ls, ''])}>
                      <Plus className="size-3.5" /> Add level
                    </Button>
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        <div className="flex shrink-0 items-center justify-end gap-2 border-t bg-muted/50 px-4 py-3">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={needsFieldPick} onClick={save}>
            Save
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

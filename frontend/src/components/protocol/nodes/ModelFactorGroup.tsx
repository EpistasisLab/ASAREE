import { useEffect, useState } from 'react'
import { Handle, Position, useUpdateNodeInternals } from '@xyflow/react'
import { ChevronDown, ChevronUp, Split } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { nodeAccent } from '@/lib/nodeAccent'
import { cardAccent } from '@/lib/utils'
import type { DesignFactor } from '@/types/experiments'
import { factorLevelLabels } from '../factorLevels'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'
import { useProviderModels } from '../useProviderModels'
import { PROVIDER_META } from './ModelNode'
import { NodeHoverToolbar } from './NodeHoverToolbar'
import { WarningBadge } from './WarningBadge'

const expansionKey = (id: string) => `asaree:model-factor-expanded:${id}`

function LevelPreview({ level, label, index, disabled, onEdit }: {
  level: unknown
  label: string
  index: number
  disabled: boolean
  onEdit: () => void
}) {
  const config = (level ?? {}) as Record<string, unknown>
  const provider = String(config.provider ?? '')
  const meta = PROVIDER_META[provider]
  const Icon = meta?.icon ?? Split
  const { models } = useProviderModels(provider)
  const model = models.find((model) => model.id === config.model)
  const effort = model?.supports_effort ?? false
  const temperature = model?.supports_temperature ?? true
  return <Card size="sm" className="nodrag nopan gap-2 px-3" style={cardAccent(nodeAccent('model'))}>
    <Button variant="ghost" className="h-auto w-full justify-start px-2 py-2 text-left whitespace-normal" disabled={disabled} onClick={(event) => { event.stopPropagation(); onEdit() }} onDoubleClick={(event) => event.stopPropagation()} aria-label={`Edit model level ${index + 1}: ${label}`}>
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="flex items-center gap-2"><Icon className="size-4 shrink-0 text-[color:var(--card-accent)]" /><span className="truncate">{label}</span></span>
        <span className="text-xs text-muted-foreground">{meta?.label ?? provider}{index === 0 ? ' · default test selection' : ''}</span>
        <span className="break-all font-mono text-xs">model={String(config.model || '(unset)')}</span>
        <span className="font-mono text-xs text-muted-foreground">
          {effort ? `effort=${String(config.effort ?? '(unset)')} ` : ''}
          {temperature ? `temperature=${String(config.temperature ?? '(unset)')} ` : ''}
          max_tokens={String(config.max_tokens ?? '(unset)')}
        </span>
      </span>
    </Button>
  </Card>
}

/** Derived previews only: this group remains one graph node and one source handle. */
export function ModelFactorGroup({ id, factor, selected, warning }: {
  id: string
  factor: DesignFactor
  selected?: boolean
  warning?: string[]
}) {
  const { requestModelFactor, experimentLocked } = useProtocolCanvasActions()
  const [expanded, setExpanded] = useState(() => {
    try { return localStorage.getItem(expansionKey(id)) === 'true' } catch { return false }
  })
  const updateNodeInternals = useUpdateNodeInternals()
  useEffect(() => { updateNodeInternals(id) }, [id, expanded, factor.levels.length, updateNodeInternals])
  const providers = [...new Set(factor.levels.map((level) => String((level as Record<string, unknown>)?.provider ?? '')))]
  const labels = factorLevelLabels(factor)
  const accent = nodeAccent('model')
  return <div className="group relative w-72" style={cardAccent(accent)}>
    <NodeHoverToolbar nodeId={id} onMakeFactor={!experimentLocked && requestModelFactor ? () => requestModelFactor(id) : undefined} />
    <Handle type="source" id="model" position={Position.Top} className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]" />
    <Card size="sm" className={`gap-3 px-3 ${selected ? 'ring-2 ring-[color:var(--card-accent)]' : ''}`}>
      <div className="flex items-center gap-2">
        <Split className="size-5 shrink-0 text-chart-2" />
        <div className="min-w-0 flex-1"><p className="truncate text-sm font-semibold" title={factor.name}>{factor.name}</p><p className="text-xs text-muted-foreground">Model factor · {factor.levels.length} levels</p></div>
        {!!warning?.length && <WarningBadge issues={warning} className="flex size-4 shrink-0 items-center justify-center" />}
        <Button variant="ghost" size="icon-sm" className="nodrag nopan" aria-label={expanded ? 'Collapse model levels' : 'Expand model levels'} aria-expanded={expanded} onClick={(event) => {
          event.stopPropagation()
          setExpanded(!expanded)
          try { localStorage.setItem(expansionKey(id), String(!expanded)) } catch { /* Expansion still works when storage is unavailable. */ }
        }} onDoubleClick={(event) => event.stopPropagation()}>{expanded ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}</Button>
      </div>
      <div className="flex flex-wrap items-center gap-3">{providers.map((provider) => {
        const meta = PROVIDER_META[provider]
        const Icon = meta?.icon ?? Split
        return <span key={provider} title={meta?.label ?? provider} className="inline-flex items-center gap-1 text-xs text-muted-foreground"><Icon className="size-3.5 text-[color:var(--card-accent)]" />{meta?.label ?? provider}</span>
      })}</div>
      {expanded && <div className="relative space-y-3 border-l border-[color:var(--card-accent)]/50 pl-4">
        <p className="text-xs text-muted-foreground">One configuration per cell</p>
        {factor.levels.map((level, index) => <div key={index} className="relative before:absolute before:top-6 before:-left-4 before:w-4 before:border-t before:border-[color:var(--card-accent)]/50">
          <LevelPreview level={level} label={labels[index]} index={index} disabled={!!experimentLocked || !requestModelFactor} onEdit={() => requestModelFactor?.(id, index)} />
        </div>)}
      </div>}
    </Card>
  </div>
}

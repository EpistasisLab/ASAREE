import { ChevronDown, Info, Pencil, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { metricCatalogEntry } from '@/lib/metricCatalog'
import { metricMetadata, type MetricReadiness } from '@/lib/measurementPlan'
import type { DesignMetric } from '@/types/experiments'

function MetricInfo({ metric }: { metric: DesignMetric }) {
  const catalog = metricCatalogEntry(metric.catalogKey)
  return <TooltipProvider delay={200}><Tooltip>
    <TooltipTrigger render={<button type="button" className="shrink-0 text-muted-foreground hover:text-foreground" aria-label={`About ${metric.name}`} />}><Info className="size-3.5" /></TooltipTrigger>
    <TooltipContent className="max-w-72 flex-col items-start gap-1 text-left">
      <span>{metric.description || catalog?.shortDescription || 'Metric definition unavailable.'}</span>
      <span className="text-background/70">{catalog ? `${catalog.kind.replace(/_/g, ' ')} metric` : `${metric.kind ?? 'custom'} metric`}</span>
    </TooltipContent>
  </Tooltip></TooltipProvider>
}

export function MetricRow({ metric, readiness, disabled, onEdit, onRemove, sourceBadge, producerNote }: {
  metric: DesignMetric
  readiness: MetricReadiness
  disabled: boolean
  onEdit?: () => void
  onRemove: () => void
  sourceBadge?: string
  producerNote?: string
}) {
  return <div className="rounded-md border bg-muted/20 p-2.5"><div className="flex flex-col gap-2 @md/metrics:flex-row @md/metrics:items-center">
    <div className="min-w-0 flex-1 text-sm">
      <div className="flex flex-wrap items-center gap-1.5"><span className="truncate font-medium">{metric.name}</span><MetricInfo metric={metric} />{sourceBadge && <Badge variant="outline">{sourceBadge}</Badge>}<Badge variant="outline" className={readiness.ready ? 'border-[color:var(--chart-3)]/50 text-[color:var(--chart-3)]' : 'border-[color:var(--chart-4)]/50 text-[color:var(--chart-4)]'}>{readiness.ready ? 'Ready' : 'Needs attention'}</Badge></div>
      <p className="mt-1 text-xs text-muted-foreground"><span className="font-medium text-foreground">{readiness.producer}</span> · {metricMetadata(metric)}</p>
      <p className="mt-0.5 text-xs text-muted-foreground">{readiness.detail}</p>
      {producerNote && <p className="mt-0.5 text-xs text-muted-foreground">{producerNote}</p>}
    </div>
    <div className="flex min-w-0 flex-wrap items-center gap-1.5">
      {onEdit && <Button variant="ghost" size="icon-sm" aria-label={`Edit ${metric.name}`} disabled={disabled} onClick={onEdit}><Pencil className="size-3.5" /></Button>}
      <Button variant="ghost" size="icon-sm" aria-label={`Remove ${metric.name}`} disabled={disabled} onClick={onRemove}><X className="size-3.5" /></Button>
    </div>
  </div></div>
}

export function BuiltInMetricsGroup({ metrics, readinessFor, expanded, summaryId, rowsId, disabled, onExpandedChange, onRemove, onManage }: {
  metrics: DesignMetric[]
  readinessFor: (metric: DesignMetric) => MetricReadiness
  expanded: boolean
  summaryId: string
  rowsId: string
  disabled: boolean
  onExpandedChange: (expanded: boolean) => void
  onRemove: (metric: DesignMetric) => void
  onManage: () => void
}) {
  const needsAttention = metrics.filter((metric) => !readinessFor(metric).ready).length
  return <section className={needsAttention > 0 ? 'rounded-md border border-[color:var(--chart-4)]/50 bg-[color:var(--chart-4)]/5' : 'rounded-md border bg-muted/10'}>
    <Button type="button" variant="ghost" className="h-auto w-full justify-start rounded-md px-3 py-2 text-left" aria-label={`Built-in metrics, ${metrics.length} active`} aria-describedby={summaryId} aria-expanded={expanded} aria-controls={rowsId} onClick={() => onExpandedChange(!expanded)}>
      <span className="flex-1 font-medium">Built-in metrics · {metrics.length} active</span><ChevronDown className={`size-4 transition-transform ${expanded ? 'rotate-180' : ''}`} />
    </Button>
    <p id={summaryId} className={`px-3 pb-2 text-xs ${needsAttention > 0 ? 'font-medium text-[color:var(--chart-4)]' : 'text-muted-foreground'}`} role="status" aria-live="polite">{needsAttention > 0 ? `${needsAttention} needs attention` : 'Ready'}</p>
    {expanded && <div id={rowsId} className="space-y-2 border-t p-2">{metrics.map((metric) => <MetricRow key={metric.id} metric={metric} readiness={readinessFor(metric)} disabled={disabled} onRemove={() => onRemove(metric)} />)}<Button type="button" variant="ghost" size="sm" disabled={disabled} onClick={onManage}>Manage built-in metrics</Button></div>}
  </section>
}

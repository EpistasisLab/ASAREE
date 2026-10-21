import { useId, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { experimentsApi, type ResultsScope } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { executionKeys } from '@/lib/executionQueries'
import { formatUsageDistributionValue, type UsageMetric } from '@/lib/resultUsage'
import type { UsageDistribution, UsageRange } from '@/types/executionResults'
import { useGenerationRefresh } from './ExecutionBrowser'

const metricLabels: Record<UsageMetric, string> = { total_tokens: 'Tokens', cost_usd: 'Cost', duration_seconds: 'Duration' }

function DistributionChart({ data, range, onRangeChange, selected }: {
  data: UsageDistribution; range: UsageRange | null; onRangeChange: (range: UsageRange | null) => void
  selected?: { label: string; value: number | null }
}) {
  const id = useId()
  const format = (value: number | null) => formatUsageDistributionValue(data.metric, value)
  const maximumCount = Math.max(1, ...data.bins.map(bin => bin.count))
  const width = 288
  const height = 128
  const step = width / data.bins.length
  const unit = data.unit === 'row' ? 'rows' : 'replicates'
  const selectedValue = selected?.value
  const markerX = selectedValue != null && data.minimum != null && data.maximum != null
    && selectedValue >= data.minimum && selectedValue <= data.maximum
    ? data.minimum === data.maximum ? 196 : 52 + (selectedValue - data.minimum) / (data.maximum - data.minimum) * width
    : null
  return <>
    <p className="text-[11px] text-muted-foreground">{data.unit === 'row' ? 'Rows' : 'Replicates'} per range</p>
    <svg viewBox="0 0 360 190" className="w-full" role="group" aria-labelledby={`${id}-title`} aria-describedby={`${id}-description`}>
      <title id={`${id}-title`}>{metricLabels[data.metric]} distribution</title>
      <desc id={`${id}-description`}>{data.count.toLocaleString()} completed {unit}. Each bar counts {unit} within a {metricLabels[data.metric].toLowerCase()} range.</desc>
      {(maximumCount === 1 ? [0, 1] : [0, 0.5, 1]).map(fraction => <g key={fraction}>
        <line x1="52" x2="340" y1={12 + height * fraction} y2={12 + height * fraction} className="stroke-border" />
        <text x="46" y={16 + height * fraction} textAnchor="end" className="fill-current font-mono text-[10px] text-muted-foreground">{Math.round(maximumCount * (1 - fraction)).toLocaleString()}</text>
      </g>)}
      {data.bins.map((bin, index) => {
        const barHeight = bin.count / maximumCount * height
        const label = bin.lower === bin.upper ? format(bin.lower) : `${format(bin.lower)} ≤ value ${bin.upper_inclusive ? '≤' : '<'} ${format(bin.upper)}`
        const active = range?.metric === data.metric && range.lower === bin.lower && range.upper === bin.upper
        const choose = () => bin.count > 0 && onRangeChange(active ? null : {
          metric: data.metric, lower: bin.lower, upper: bin.upper, upper_inclusive: bin.upper_inclusive,
        })
        return <g key={index} role="button" aria-label={`${label}: ${bin.count.toLocaleString()} ${unit}`}
          aria-pressed={active} aria-disabled={bin.count === 0} tabIndex={bin.count ? 0 : -1}
          onClick={choose} onKeyDown={event => {
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose() }
          }} className="group/bin cursor-pointer outline-none">
          <rect x={52 + index * step + 1} y={12 + height - barHeight} width={Math.max(1, step - 2)} height={Math.max(1, barHeight)}
            className={`${active ? 'fill-primary/90' : range ? 'fill-primary/25' : 'fill-primary/50'} stroke-primary/70 group-hover/bin:fill-primary/80 group-focus-visible/bin:stroke-foreground group-focus-visible/bin:stroke-2`}>
            <title>{label}: {bin.count.toLocaleString()} {unit}</title>
          </rect>
        </g>
      })}
      {markerX !== null && selected && <g role="img" aria-label={`${selected.label}: ${format(selected.value)} on distribution`} className="pointer-events-none">
        <line x1={markerX} x2={markerX} y1="8" y2="143" strokeDasharray="4 3" className="stroke-[color:var(--chart-4)]" strokeWidth="2" />
        <path d={`M ${markerX - 4} 4 L ${markerX + 4} 4 L ${markerX} 10 Z`} className="fill-[color:var(--chart-4)]" />
      </g>}
      {data.minimum === data.maximum
        ? <text x="196" y="158" textAnchor="middle" className="fill-current font-mono text-[10px] text-muted-foreground">All values: {format(data.minimum)}</text>
        : <>
          <text x="52" y="158" className="fill-current font-mono text-[10px] text-muted-foreground">{format(data.minimum)}</text>
          <text x="340" y="158" textAnchor="end" className="fill-current font-mono text-[10px] text-muted-foreground">{format(data.maximum)}</text>
        </>}
      <text x="196" y="181" textAnchor="middle" className="fill-current text-[11px] text-muted-foreground">{metricLabels[data.metric]} per {data.unit === 'row' ? 'row' : 'replicate'}</text>
    </svg>
    {selected && <p className="text-xs text-[color:var(--chart-4)]">{selected.label} · latest attempt: {selected.value === null ? 'No completed value recorded' : format(selected.value)}{selected.value !== null && markerX === null ? ' · outside the loaded distribution' : ''}</p>}
    <dl className="grid grid-cols-2 gap-2 text-xs">
      <div><dt className="text-muted-foreground">Median</dt><dd className="mt-1 font-mono tabular-nums">{format(data.median)}</dd></div>
      <div><dt className="text-muted-foreground">Middle 50%</dt><dd className="mt-1 font-mono tabular-nums">{format(data.p25)}–{format(data.p75)}</dd></div>
    </dl>
  </>
}

export function ReplicateUsageDistribution({ experimentId, cellId, scope, generation, selectedSlotId, range: controlledRange, onRangeChange }: {
  experimentId: string; cellId: string; scope: ResultsScope; generation?: number
  selectedSlotId?: string; range?: UsageRange | null; onRangeChange?: (range: UsageRange | null) => void
}) {
  const [metric, setMetric] = useState<UsageMetric>('total_tokens')
  const [internalRange, setInternalRange] = useState<UsageRange | null>(null)
  const range = controlledRange === undefined ? internalRange : controlledRange
  const updateRange = (next: UsageRange | null) => { setInternalRange(next); onRangeChange?.(next) }
  const query = useQuery({
    queryKey: [...executionKeys.root(experimentId), 'usage-distribution', scope, cellId, metric],
    queryFn: ({ signal }) => experimentsApi.getUsageDistribution(experimentId, scope, cellId, metric, signal),
    staleTime: 5000,
  })
  useGenerationRefresh(query.refetch, generation)
  const selectedHistory = useQuery({
    queryKey: [...executionKeys.root(experimentId), 'attempts', scope, selectedSlotId, undefined],
    queryFn: ({ signal }) => experimentsApi.listExecutionAttempts(experimentId, selectedSlotId!, scope, undefined, signal),
    enabled: !!selectedSlotId,
  })
  useGenerationRefresh(() => { if (selectedSlotId) void selectedHistory.refetch() }, generation)
  const selectedSlot = selectedHistory.data?.slot
  const selectedValue = selectedSlot?.usage?.[metric]
  const selected = selectedSlot ? {
    label: selectedSlot.row_index != null ? `Replicate ${selectedSlot.replicate_number}, source row ${selectedSlot.row_index + 1}` : `Replicate ${selectedSlot.replicate_number}`,
    value: selectedSlot.status === 'completed' && typeof selectedValue === 'number' && Number.isFinite(selectedValue) && selectedValue >= 0 ? selectedValue : null,
  } : undefined
  const data = query.data
  const unit = data?.unit === 'row' ? 'rows' : 'replicates'
  return <Card size="sm" className="my-4 gap-3 p-3" role="region" aria-label="Replicate usage distribution">
    <div><h3 className="text-sm font-medium">Usage distribution</h3><p className="mt-1 text-xs text-muted-foreground">Latest attempts of completed {unit}.</p></div>
    <div role="group" aria-label="Distribution metric" className="flex flex-wrap gap-2">
      {(Object.keys(metricLabels) as UsageMetric[]).map(key => <Button key={key} size="xs" variant={key === metric ? 'default' : 'outline'}
        aria-pressed={key === metric} onClick={() => { if (key !== metric) { setMetric(key); updateRange(null) } }}>{metricLabels[key]}</Button>)}
    </div>
    {query.isPending ? <Skeleton className="h-48" /> : data && data.count > 0 ? <>
      <p className="text-xs text-muted-foreground">{data.count.toLocaleString()} of {data.completed_count.toLocaleString()} completed {unit} with recorded {metricLabels[metric].toLowerCase()}.</p>
      <p className="text-[11px] text-muted-foreground">Select a bar to filter the list below.</p>
      <DistributionChart data={data} range={range} onRangeChange={updateRange} selected={selected} />
    </> : !query.error && <p className="text-xs text-muted-foreground">No completed {unit} with recorded {metricLabels[metric].toLowerCase()} yet.</p>}
    {query.error && <div role="alert" className="space-y-2 text-xs text-destructive"><p>{data ? 'Refresh failed; showing the last loaded distribution.' : 'Could not load the usage distribution.'}</p>
      <Button size="xs" variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>Retry distribution</Button></div>}
    {selectedHistory.error && <p role="alert" className="text-xs text-destructive">Could not load the selected replicate’s chart marker.</p>}
    {range && <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-2">
      <p className="font-mono text-xs">{metricLabels[range.metric]}: {formatUsageDistributionValue(range.metric, range.lower)} ≤ value {range.upper_inclusive ? '≤' : '<'} {formatUsageDistributionValue(range.metric, range.upper)}</p>
      <Button size="xs" variant="outline" onClick={() => updateRange(null)}>Clear range</Button>
    </div>}
  </Card>
}

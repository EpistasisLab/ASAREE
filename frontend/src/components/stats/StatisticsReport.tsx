import { useState } from 'react'
import { Download } from 'lucide-react'
import { statsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader } from '@/components/ui/card'
import type { StatsAnalysis } from '@/types/stats'
import { describeStatsReportFindings, primaryComparisonResult } from '@/lib/statsFindings'
import { AnalysisMethods } from './AnalysisMethods'

const distributionNames: Record<string, string> = {
  gaussian: 'Gaussian (normal)', gamma: 'Gamma', lognormal: 'Lognormal',
  poisson: 'Poisson', negbin: 'Negative binomial', binomial: 'Binomial',
  beta: 'Beta', betabinomial: 'Beta-binomial', ordinal_logit: 'Ordered categorical (cumulative logit)',
  zip: 'Zero-inflated Poisson', zinb: 'Zero-inflated negative binomial',
  zoib: 'Zero/one-inflated beta', gamma_hurdle: 'Hurdle Gamma',
}

function value(number: number | null, p = false) {
  if (number == null || !Number.isFinite(number)) return 'Unavailable'
  if (p) return number < .001 ? '<0.001' : number.toFixed(3)
  return number.toLocaleString(undefined, { maximumSignificantDigits: 4 })
}

function interval(low: number | null | undefined, high: number | null | undefined) {
  const endpoint = (number: number) => number !== 1 && Math.abs(number - 1) < .001 ? String(number) : value(number)
  return low == null || high == null ? 'Unavailable' : `${endpoint(low)} to ${endpoint(high)}`
}

export function StatisticsReport({ analysis, experiment }: { analysis: StatsAnalysis; experiment: string }) {
  const [error, setError] = useState('')
  const [downloading, setDownloading] = useState(false)
  const report = analysis.output?.summary
  if (!report) return <p className="text-sm text-muted-foreground">Run an automatic analysis to create a concise report for this metric.</p>
  const resampled = !!report.resampling
  const findings = describeStatsReportFindings(report)
  const primary = report.primary_analysis === 'parametric_bootstrap_intervals'
  const distribution = report.distribution
  const bins = distribution?.plot?.bins ?? []
  const tickStep = Math.max(1, Math.ceil(Math.max(1, ...bins.map(bin => bin.count)) / 4))
  const maximumCount = tickStep * 4
  const plot = distribution?.plot
  const axisMinimum = bins[0]?.lower ?? distribution?.minimum ?? 0
  const axisMaximum = bins.at(-1)?.upper ?? distribution?.maximum ?? 1
  const family = analysis.output?.family
  const metricInfo = report.metric_info ?? {
    description: report.metric_definition?.description?.trim() || 'Description not provided.',
    unit: report.metric_definition?.unit,
    coding: [],
  }
  async function download() {
    setError('')
    setDownloading(true)
    try {
      const { blob, filename } = await statsApi.report(experiment, analysis.id)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The report could not be downloaded.')
    } finally {
      setDownloading(false)
    }
  }
  return <Card>
    <CardHeader>
      <div className="flex justify-start p-3 mb-2">
        <Button variant="outline" size="sm" onClick={() => void download()} disabled={downloading}>
          <Download className="size-3.5" />{downloading ? 'Downloading…' : 'Download HTML report'}
        </Button>
      </div>
      <CardDescription>{metricInfo.description}</CardDescription>
      {metricInfo.unit && <p className="text-sm text-muted-foreground">Unit: {metricInfo.unit}</p>}
      {metricInfo.coding.map((line, i) => <p key={i} className="text-sm text-muted-foreground">{line}</p>)}
    </CardHeader>
    <CardContent className="space-y-5">
      <p className="text-sm text-muted-foreground">{report.description}</p>
      {report.execution_status === 'incomplete' && <p role="alert" className="text-sm text-destructive">Analysis incomplete. Interrupted procedures supply no inference.</p>}
      {!!findings.length && <ul aria-label="Key findings" className="list-disc space-y-3 pl-5 text-sm">
        {findings.map((finding, i) => <li key={i}>{finding}</li>)}
      </ul>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {report.status === 'descriptive' ? <p className="text-sm text-muted-foreground">Descriptive only. No uncertainty or statistical conclusions are estimated.</p> : report.status !== 'valid' && <p role="alert" className="rounded border border-destructive p-3 text-sm">Statistical conclusions are unavailable for this metric. The reason is listed below.</p>}
      {family && report.status !== 'descriptive' && <p className="text-sm">Selected regression model distribution: <span className="font-medium">{distributionNames[family] ?? family}</span></p>}
      {distribution && distribution.n > 0 && <section aria-label="Metric distribution" className="space-y-3">
        <h3 className="text-sm font-medium">Observed distribution</h3>
        {bins.some(bin => bin.count > 0) ? <figure aria-label={`${report.metric} observed distribution histogram`} className="space-y-3 rounded-lg border border-border bg-muted/20 p-4">
          <figcaption className="text-xs text-muted-foreground">{plot?.kind === 'frequency' ? 'Observed frequencies' : 'Histogram'}{plot?.binning ? ` · ${plot.binning}` : ''}</figcaption>
          <div className="flex gap-2">
            <span className="flex h-40 select-none items-center text-xs text-muted-foreground [writing-mode:vertical-rl] rotate-180">Replicates</span>
            <div aria-hidden="true" className="flex h-40 flex-col justify-between text-right font-mono text-[10px] text-muted-foreground">
              {[4, 3, 2, 1, 0].map(tick => <span key={tick}>{value(tick * tickStep)}</span>)}
            </div>
            <div className="min-w-0 flex-1 space-y-2">
              <ol className={`flex border-b border-border ${plot?.kind === 'frequency' ? 'gap-2' : 'gap-px'}`}>
                {bins.map((bin, i) => <li key={i} className="relative flex h-40 min-w-0 flex-1 items-end" title={`${bin.label}: ${bin.count} replicates`}>
                  <span className="sr-only">{bin.label}: {bin.count} replicates</span>
                  <span aria-hidden="true" className="pointer-events-none absolute inset-0 border-t border-border/50 bg-[repeating-linear-gradient(to_top,transparent_0,transparent_calc(25%-1px),var(--border)_25%)] opacity-40" />
                  {bin.count > 0 && <span aria-hidden="true" className="relative w-full border border-primary/70 bg-primary/60" style={{ height: `${bin.count / maximumCount * 100}%` }} />}
                </li>)}
              </ol>
              {plot?.kind === 'frequency' ? <div aria-hidden="true" className="grid gap-2 font-mono text-[10px] text-muted-foreground" style={{ gridTemplateColumns: `repeat(${bins.length}, minmax(0, 1fr))` }}>
                {bins.map((bin, i) => <span key={i} className="min-w-0 break-words text-center">{bin.label}</span>)}
              </div> : <div aria-hidden="true" className="flex justify-between gap-1 font-mono text-[10px] text-muted-foreground">
                {[0, 1, 2, 3, 4].map(tick => <span key={tick}>{value(axisMinimum + (axisMaximum - axisMinimum) * tick / 4)}</span>)}
              </div>}
              <p className="text-center text-xs text-muted-foreground">Metric value{metricInfo.unit ? ` (${metricInfo.unit})` : ''}</p>
            </div>
          </div>
        </figure> : <p className="rounded-md border border-border bg-muted/20 p-3 text-xs text-muted-foreground">This saved report does not contain enough observations to draw a chart. Analyze this experiment version again to include it.</p>}
        <p className="text-xs text-muted-foreground">Observed values pool the analyzed replicates across conditions. {report.status === 'descriptive' ? 'Descriptive only; no statistical inference is provided.' : 'The selected model describes outcomes after accounting for the experimental factors.'}</p>
        {!!metricInfo.coding.length && <p className="text-xs text-muted-foreground">Categorical outcomes use the analyzed category codes. Their numeric summaries depend on that coding.</p>}
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs @lg:grid-cols-3">
          {([
            ['Replicates', distribution.n], ['Minimum', distribution.minimum], ['Maximum', distribution.maximum],
            ['Mean', distribution.mean], ['Standard deviation', distribution.sd],
            ['Distinct values', distribution.distinct], ['Zero values', distribution.zeros],
          ] satisfies [string, number | null][]).map(([label, number]) => <div key={label} className="min-w-0">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="mt-1 break-words font-mono">{value(number)}</dd>
          </div>)}
        </dl>
      </section>}
      {!!report.tests.length && <div className="overflow-x-auto"><table className="w-full text-left text-sm">
        <caption className="mb-2 text-left font-medium">{primary ? 'Secondary statistical tests' : 'Statistical tests'}</caption>
        <thead><tr>{['Effect', resampled ? 'Analytic adjusted p' : 'Adjusted p-value', ...(resampled ? ['Resampling adjusted p'] : []), 'Analytic result'].map(title => <th scope="col" key={title} className="border-b border-border p-3">{title}</th>)}</tr></thead>
        <tbody>{report.tests.map((row, i) => <tr key={i}>
          <td className="border-b border-border p-3">{row.effect}{row.method && <span className="mt-1 block text-xs text-muted-foreground">{row.method}</span>}</td>
          <td className="border-b border-border p-3 font-mono">{value(row.p_adjusted, true)}</td>
          {resampled && <td className="border-b border-border p-3"><span className="font-mono">{value(row.permutation_p_adjusted ?? null, true)}</span><span className="mt-1 block text-xs text-muted-foreground">{row.permutation_p_adjusted == null ? row.permutation_status : row.permutation_result}</span>{row.permutation_p_adjusted != null && <span className="mt-1 block text-xs text-muted-foreground">{row.resampling_label}</span>}</td>}
          <td className="border-b border-border p-3">{row.result}</td>
        </tr>)}</tbody>
      </table></div>}
      {resampled && !!report.tests.length && <p className="text-xs text-muted-foreground">Resampling procedures are named per row. Some test a different null hypothesis from the analytic test. Agreement does not establish validity.</p>}
      {!!report.effects.length && <div className="overflow-x-auto"><table className="w-full text-left text-sm">
        <caption className="mb-2 text-left font-medium">Comparisons · 95% confidence intervals</caption>
        <thead><tr>{['Comparison', 'Effect', ...(primary ? [`Primary parametric-bootstrap ${Math.round((report.confidence_level ?? .95) * 100)}% interval`, 'Sensitivity: analytic 95% interval'] : [resampled ? 'Analytic 95% interval' : '95% interval', ...(resampled ? ['Bootstrap 95% interval'] : [])]), primary ? 'Sensitivity: analytic adjusted p' : 'Analytic adjusted p', primary ? 'Primary interval result' : 'Result'].map(title => <th scope="col" key={title} className="border-b border-border p-3">{title}</th>)}</tr></thead>
        <tbody>{report.effects.map((row, i) => <tr key={i}>
          <td className="border-b border-border p-3">{row.comparison}{row.method && <span className="mt-1 block text-xs text-muted-foreground">{row.method}</span>}</td>
          <td className="border-b border-border p-3"><span className="font-mono">{value(row.estimate)}</span><span className="block text-xs text-muted-foreground">{row.scale.replaceAll('_', ' ')}{row.unit ? ` · ${row.unit}` : ''}</span></td>
          {primary && <td className="border-b border-border p-3"><span className="font-mono">{interval(row.primary_ci_low, row.primary_ci_high)}</span><span className="mt-1 block text-xs text-muted-foreground">{row.primary_status === 'completed' ? row.interval_agreement : 'Primary inference unavailable; sensitivity results do not replace it.'}</span></td>}
          <td className="border-b border-border p-3 font-mono">{interval(row.ci_low, row.ci_high)}</td>
          {resampled && !primary && <td className="border-b border-border p-3"><span className="font-mono">{interval(row.bootstrap_ci_low, row.bootstrap_ci_high)}</span><span className="mt-1 block text-xs text-muted-foreground">{row.bootstrap_ci_low == null ? row.bootstrap_status : row.interval_agreement}</span></td>}
          <td className="border-b border-border p-3 font-mono">{value(row.p_adjusted, true)}</td>
          <td className="border-b border-border p-3">{primary ? primaryComparisonResult(row) : row.result}</td>
        </tr>)}</tbody>
      </table></div>}
      {report.resampling && <p className="text-xs text-muted-foreground">Bootstrap: {report.resampling.bootstrap.successful}/{report.resampling.bootstrap.requested} successful refits; {report.resampling.bootstrap.failed} failed. {report.resampling.bootstrap.status !== 'completed' && report.resampling.bootstrap.status}</p>}
      <p className="text-xs text-muted-foreground">A result without clear evidence does not establish that the effect is zero. Statistical evidence does not establish practical importance.</p>
      {!!report.warnings.length && <div className="space-y-2 text-sm text-muted-foreground" aria-label="Limitations and exclusions">
        {report.warnings.map((warning, i) => <p key={i}>{warning}</p>)}
      </div>}
      <AnalysisMethods analysis={analysis} report={report} modelName={family ? distributionNames[family] ?? family : 'Model not recorded'} />
    </CardContent>
  </Card>
}

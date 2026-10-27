import { useState } from 'react'
import { Download } from 'lucide-react'
import { statsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { StatsAnalysis } from '@/types/stats'
import { describeStatsFindings } from '@/lib/statsFindings'
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
  return low == null || high == null ? 'Unavailable' : `${value(low)} to ${value(high)}`
}

export function StatisticsReport({ analysis, experiment }: { analysis: StatsAnalysis; experiment: string }) {
  const [error, setError] = useState('')
  const [downloading, setDownloading] = useState(false)
  const report = analysis.output?.summary
  if (!report) return <p className="text-sm text-muted-foreground">Run an automatic analysis to create a concise report for this metric.</p>
  const resampled = !!report.resampling
  const findings = describeStatsFindings(report.tests)
  const distribution = report.distribution
  const bins = distribution?.plot?.bins ?? []
  const maximumCount = Math.max(1, ...bins.map(bin => bin.count))
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
      const blob = await statsApi.report(experiment, analysis.id)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `asaree-statistics-${analysis.id}.html`
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
      <div className="flex flex-col items-start gap-3 @2xl:flex-row @2xl:items-center @2xl:justify-between">
        <CardTitle className="min-w-0 break-words">{report.metric}</CardTitle>
        <Button variant="outline" onClick={() => void download()} disabled={downloading}>
          <Download className="size-4" />{downloading ? 'Downloading…' : 'Download HTML report'}
        </Button>
      </div>
      <CardDescription>{metricInfo.description}</CardDescription>
      {metricInfo.unit && <p className="text-sm text-muted-foreground">Unit: {metricInfo.unit}</p>}
      {metricInfo.coding.map((line, i) => <p key={i} className="text-sm text-muted-foreground">{line}</p>)}
    </CardHeader>
    <CardContent className="space-y-5">
      <p className="text-sm text-muted-foreground">{report.description}</p>
      {!!findings.length && <ul aria-label="Key findings" className="list-disc space-y-3 pl-5 text-sm">
        {findings.map((finding, i) => <li key={i}>{finding}</li>)}
      </ul>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {report.status !== 'valid' && <p role="alert" className="rounded border border-destructive p-3 text-sm">Statistical conclusions are unavailable for this metric. The reason is listed below.</p>}
      {family && <p className="text-sm">Selected model distribution: <span className="font-medium">{distributionNames[family] ?? family}</span></p>}
      {distribution && distribution.n > 0 && <section aria-label="Metric distribution" className="space-y-3">
        <h3 className="text-sm font-medium">Observed distribution</h3>
        {bins.some(bin => bin.count > 0) && <figure aria-label={`${report.metric} observed distribution histogram`} className="space-y-2 rounded-md border border-border bg-muted/20 p-3">
          <figcaption className="text-xs text-muted-foreground">Histogram</figcaption>
          <div className="flex gap-2">
            <span className="flex h-32 select-none items-center text-xs text-muted-foreground [writing-mode:vertical-rl] rotate-180">Replicates</span>
            <div className="min-w-0 flex-1 space-y-1.5">
              <ol className="flex gap-0.5">
                {bins.map((bin, i) => <li key={i} className="flex min-w-0 flex-1 flex-col" title={bin.label}>
                  <span className="flex h-32 w-full items-end border-b border-border">
                    {bin.count > 0 && <span aria-hidden="true" className="w-full rounded-t-sm bg-primary shadow-[0_0_8px_var(--primary)]" style={{ height: `${bin.count / maximumCount * 100}%` }} />}
                  </span>
                  <span className="pt-1 text-center font-mono text-[10px] leading-tight text-muted-foreground">
                    <span className="block break-words">{bin.label}</span>
                    <span className="block">{bin.count}</span>
                  </span>
                </li>)}
              </ol>
              <p className="text-center text-xs text-muted-foreground">Metric value{metricInfo.unit ? ` (${metricInfo.unit})` : ''}</p>
            </div>
          </div>
        </figure>}
        {!bins.some(bin => bin.count > 0) && <p className="rounded-md border border-border bg-muted/20 p-3 text-xs text-muted-foreground">This saved report does not contain enough observations to draw a chart. Analyze this experiment version again to include it.</p>}
        <p className="text-xs text-muted-foreground">Observed values pool the analyzed replicates across conditions. The selected model describes outcomes after accounting for the experimental factors.</p>
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
        <caption className="mb-2 text-left font-medium">Statistical tests</caption>
        <thead><tr>{['Effect', resampled ? 'Analytic adjusted p' : 'Adjusted p-value', ...(resampled ? ['Permutation adjusted p'] : []), 'Analytic result'].map(title => <th scope="col" key={title} className="border-b border-border p-3">{title}</th>)}</tr></thead>
        <tbody>{report.tests.map((row, i) => <tr key={i}>
          <td className="border-b border-border p-3">{row.effect}{row.method && <span className="mt-1 block text-xs text-muted-foreground">{row.method}</span>}</td>
          <td className="border-b border-border p-3 font-mono">{value(row.p_adjusted, true)}</td>
          {resampled && <td className="border-b border-border p-3"><span className="font-mono">{value(row.permutation_p_adjusted ?? null, true)}</span><span className="mt-1 block text-xs text-muted-foreground">{row.permutation_p_adjusted == null ? row.permutation_status : row.permutation_result}</span></td>}
          <td className="border-b border-border p-3">{row.result}</td>
        </tr>)}</tbody>
      </table></div>}
      {resampled && !!report.tests.length && <p className="text-xs text-muted-foreground">Permutation results are working-scale sensitivity checks and may test a different null hypothesis. See “What ran and why” for assumptions.</p>}
      {!!report.effects.length && <div className="overflow-x-auto"><table className="w-full text-left text-sm">
        <caption className="mb-2 text-left font-medium">Comparisons · 95% confidence intervals</caption>
        <thead><tr>{['Comparison', 'Effect', resampled ? 'Analytic 95% interval' : '95% interval', ...(resampled ? ['Bootstrap 95% interval'] : []), 'Analytic adjusted p', 'Result'].map(title => <th scope="col" key={title} className="border-b border-border p-3">{title}</th>)}</tr></thead>
        <tbody>{report.effects.map((row, i) => <tr key={i}>
          <td className="border-b border-border p-3">{row.comparison}{row.method && <span className="mt-1 block text-xs text-muted-foreground">{row.method}</span>}</td>
          <td className="border-b border-border p-3"><span className="font-mono">{value(row.estimate)}</span><span className="block text-xs text-muted-foreground">{row.scale.replaceAll('_', ' ')}{row.unit ? ` · ${row.unit}` : ''}</span></td>
          <td className="border-b border-border p-3 font-mono">{interval(row.ci_low, row.ci_high)}</td>
          {resampled && <td className="border-b border-border p-3"><span className="font-mono">{interval(row.bootstrap_ci_low, row.bootstrap_ci_high)}</span><span className="mt-1 block text-xs text-muted-foreground">{row.bootstrap_ci_low == null ? row.bootstrap_status : row.interval_agreement}</span></td>}
          <td className="border-b border-border p-3 font-mono">{value(row.p_adjusted, true)}</td>
          <td className="border-b border-border p-3">{row.result}</td>
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

import { useState } from 'react'
import { Download } from 'lucide-react'
import { statsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { StatsAnalysis } from '@/types/stats'

function value(number: number | null, p = false) {
  if (number == null || !Number.isFinite(number)) return 'Unavailable'
  if (p) return number < .001 ? '<0.001' : number.toFixed(3)
  return number.toLocaleString(undefined, { maximumSignificantDigits: 4 })
}

export function StatisticsReport({ analysis, experiment }: { analysis: StatsAnalysis; experiment: string }) {
  const [error, setError] = useState('')
  const [downloading, setDownloading] = useState(false)
  const report = analysis.output?.summary
  if (!report) return <p className="text-sm text-muted-foreground">Run an automatic analysis to create a concise report for this metric.</p>
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
      <div className="flex flex-wrap items-center justify-between gap-3">
        <CardTitle>{report.metric}</CardTitle>
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
      {!!report.findings.length && <p className="text-sm">{report.findings.join(' ')}</p>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {report.status !== 'valid' && <p role="alert" className="rounded border border-destructive p-3 text-sm">Statistical conclusions are unavailable for this metric. The reason is listed below.</p>}
      {!!report.tests.length && <div className="overflow-x-auto"><table className="w-full text-left text-sm">
        <caption className="mb-2 text-left font-medium">Statistical tests</caption>
        <thead><tr>{['Effect', 'Adjusted p-value', 'Result'].map(title => <th scope="col" key={title} className="border-b border-border p-3">{title}</th>)}</tr></thead>
        <tbody>{report.tests.map((row, i) => <tr key={i}>
          <td className="border-b border-border p-3">{row.effect}</td>
          <td className="border-b border-border p-3 font-mono">{value(row.p_adjusted, true)}</td>
          <td className="border-b border-border p-3">{row.result}</td>
        </tr>)}</tbody>
      </table></div>}
      {!!report.effects.length && <div className="overflow-x-auto"><table className="w-full text-left text-sm">
        <caption className="mb-2 text-left font-medium">Comparisons · 95% confidence intervals</caption>
        <thead><tr>{['Comparison', 'Effect', '95% interval', 'Adjusted p-value', 'Result'].map(title => <th scope="col" key={title} className="border-b border-border p-3">{title}</th>)}</tr></thead>
        <tbody>{report.effects.map((row, i) => <tr key={i}>
          <td className="border-b border-border p-3">{row.comparison}</td>
          <td className="border-b border-border p-3"><span className="font-mono">{value(row.estimate)}</span><span className="block text-xs text-muted-foreground">{row.scale.replaceAll('_', ' ')}{row.unit ? ` · ${row.unit}` : ''}</span></td>
          <td className="border-b border-border p-3 font-mono">{row.ci_low == null || row.ci_high == null ? 'Unavailable' : `${value(row.ci_low)} to ${value(row.ci_high)}`}</td>
          <td className="border-b border-border p-3 font-mono">{value(row.p_adjusted, true)}</td>
          <td className="border-b border-border p-3">{row.result}</td>
        </tr>)}</tbody>
      </table></div>}
      <p className="text-xs text-muted-foreground">A result without clear evidence does not establish that the effect is zero. Statistical evidence does not establish practical importance.</p>
      {!!report.warnings.length && <div className="space-y-2 text-sm text-muted-foreground" aria-label="Limitations and exclusions">
        {report.warnings.map((warning, i) => <p key={i}>{warning}</p>)}
      </div>}
      <details className="rounded border border-border p-3 text-sm">
        <summary className="cursor-pointer font-medium">What ran and why</summary>
        <div className="mt-3 space-y-2 text-muted-foreground">{report.methods.map((method, i) => <p key={i}>{method}</p>)}</div>
        <p className="mt-3 break-all font-mono text-xs text-muted-foreground">Input snapshot: {analysis.fingerprint} · Analysis: {analysis.id}</p>
      </details>
    </CardContent>
  </Card>
}

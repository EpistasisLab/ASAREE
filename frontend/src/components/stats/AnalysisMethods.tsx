import { Activity, ChevronDown, FlaskConical, ListChecks, ShieldCheck } from 'lucide-react'
import type { ReactNode } from 'react'
import type { StatsAnalysis, StatsSummary } from '@/types/stats'

function MethodStep({ number, title, icon, children }: {
  number: string; title: string; icon: ReactNode; children: ReactNode
}) {
  return <section className="min-w-0 space-y-3 rounded-lg border border-border bg-background/60 p-4">
    <div className="flex items-center gap-2">
      <span className="font-mono text-[10px] tracking-widest text-primary">{number}</span>
      <span className="text-primary">{icon}</span>
      <h4 className="text-sm font-medium">{title}</h4>
    </div>
    {children}
  </section>
}

function uniqueMethods(rows: (StatsSummary['tests'][number] | StatsSummary['effects'][number])[]) {
  return [...new Map(rows.filter(row => row.method).map(row => [row.method, row])).values()]
}

export function AnalysisMethods({ analysis, report, modelName }: {
  analysis: StatsAnalysis; report: StatsSummary; modelName: string
}) {
  const bootstrap = report.resampling?.bootstrap
  const percentage = bootstrap?.requested ? Math.min(100, bootstrap.successful / bootstrap.requested * 100) : 0
  const permutationCount = report.tests.filter(test => test.permutation_p_adjusted != null).length
  const treatmentLabels = report.provenance.treatment_labels
  const factorNames = analysis.config.factors.length ? analysis.config.factors
    : Object.keys(treatmentLabels && typeof treatmentLabels === 'object' ? treatmentLabels : {})
  const tests = uniqueMethods(report.tests)
  const comparisons = uniqueMethods(report.effects)

  return <section aria-label="What ran and why" className="overflow-hidden rounded-xl border border-primary/25 bg-muted/15">
    <header className="space-y-3 border-b border-primary/15 bg-primary/5 p-4 @lg:p-5">
      <div className="flex items-start gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-primary/30 bg-primary/10 text-primary shadow-[0_0_16px_-6px_var(--primary)]"><FlaskConical className="size-4" /></span>
        <div className="min-w-0">
          <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-primary">Analysis approach</p>
          <h3 className="mt-1 text-base font-semibold">What ran and why</h3>
        </div>
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">From recorded measurements to statistical conclusions—here’s how this report was calculated.</p>
      <div className="flex flex-wrap gap-2 text-[10px] font-medium">
        <span className="rounded-full border border-primary/25 bg-primary/10 px-2.5 py-1 text-primary">Statistical calculations · no AI</span>
        <span className="rounded-full border border-border bg-background/60 px-2.5 py-1 font-mono text-muted-foreground">{analysis.output?.n_observations ?? report.distribution?.n ?? '—'} analyzed replicates</span>
      </div>
    </header>
    <div className="grid gap-3 p-3 @2xl:grid-cols-2 @lg:p-4">
      <MethodStep number="01" title="Model selection" icon={<Activity className="size-3.5" />}>
        <p className="break-words text-base font-semibold">{modelName}</p>
        {report.methods[0] && <p className="text-xs leading-relaxed text-muted-foreground">{report.methods[0]}</p>}
        {!!factorNames.length && <div className="flex flex-wrap gap-1.5">{factorNames.map(name => <span key={name} className="max-w-full break-words rounded border border-border bg-muted/40 px-2 py-1 text-[10px]">{name}</span>)}</div>}
      </MethodStep>
      <MethodStep number="02" title="Tests and comparisons" icon={<ListChecks className="size-3.5" />}>
        {[{ title: 'Factor tests', rows: tests }, { title: 'Comparisons', rows: comparisons }].map(({ title, rows }) => <div key={title} className="space-y-1.5">
          <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">{title}</p>
          {rows.map(row => <div key={row.method} className="space-y-1 border-l-2 border-primary/30 pl-2.5">
            <p className="break-words text-xs font-medium">{row.method}</p>
            {row.purpose && <p className="text-xs leading-relaxed text-muted-foreground">{row.purpose}</p>}
          </div>)}
        </div>)}
      </MethodStep>
      <MethodStep number="03" title="Uncertainty checks" icon={<Activity className="size-3.5" />}>
        {bootstrap ? <div className="space-y-2">
          <div className="flex flex-wrap items-baseline justify-between gap-1 text-xs"><span>Bootstrap refits</span><span className="font-mono">{bootstrap.successful.toLocaleString()} / {bootstrap.requested.toLocaleString()}</span></div>
          <div role="meter" aria-label="Successful bootstrap refits" aria-valuemin={0} aria-valuemax={bootstrap.requested || 1} aria-valuenow={bootstrap.successful} className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div className={`h-full rounded-full ${bootstrap.status === 'completed' ? 'bg-[color:var(--chart-3)]' : 'bg-[color:var(--chart-4)]'}`} style={{ width: `${percentage}%` }} />
          </div>
          <p className="text-xs leading-relaxed text-muted-foreground">Repeated fits estimate the uncertainty in each comparison. {bootstrap.failed} refits failed.{bootstrap.status !== 'completed' ? ` ${bootstrap.status}.` : ''}</p>
        </div> : <p className="text-xs text-muted-foreground">No bootstrap results were recorded for this report.</p>}
        <div className="space-y-1 border-t border-border pt-3">
          <p className="text-xs">Permutation checks: <span className="font-mono">{permutationCount} / {report.tests.length}</span> available</p>
          <p className="text-xs leading-relaxed text-muted-foreground">These provide an additional sensitivity check. Agreement with the main test does not establish that the model is valid.</p>
        </div>
      </MethodStep>
      <MethodStep number="04" title="How to interpret it" icon={<ShieldCheck className="size-3.5" />}>
        <ul className="list-disc space-y-2 pl-3.5 text-xs leading-relaxed text-muted-foreground">
          <li>Adjusted p-values account for multiple tests within each metric.</li>
          <li>Confidence intervals show a range of plausible effect sizes; they are not adjusted for multiple comparisons.</li>
          <li>A result without clear evidence does not mean the effect is zero.</li>
          <li>Results across separate metrics are exploratory.</li>
        </ul>
      </MethodStep>
    </div>
    <details className="group border-t border-border">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-4 py-3 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground [&::-webkit-details-marker]:hidden">
        Full technical details <ChevronDown className="size-3.5 transition-transform group-open:rotate-180" />
      </summary>
      <div className="space-y-3 px-4 pb-4">
        <p className="text-xs text-muted-foreground">Calculated with statistical libraries. Explanations use fixed templates; no AI is used to generate this report.</p>
        <ol className="list-decimal space-y-3 pl-4 text-xs leading-relaxed text-muted-foreground">{report.methods.map((method, i) => <li key={i} className="break-words pl-1">{method}</li>)}</ol>
        <dl className="space-y-2 border-t border-border pt-3 text-[10px]">
          <div><dt className="text-muted-foreground">Input snapshot</dt><dd className="mt-1 break-all font-mono">{analysis.fingerprint}</dd></div>
          <div><dt className="text-muted-foreground">Analysis ID</dt><dd className="mt-1 break-all font-mono">{analysis.id}</dd></div>
        </dl>
      </div>
    </details>
  </section>
}

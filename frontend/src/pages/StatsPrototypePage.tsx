import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity, ArrowLeftRight, Play } from 'lucide-react';
import { experimentsApi, statsApi } from '@/api/client';
import { AppHeader } from '@/components/AppHeader';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import type { StatsAnalysis, StatsBackend, StatsConfig, StatsRow } from '@/types/stats';
const mapped = ['gaussian', 'lognormal', 'gamma', 'poisson', 'negbin', 'zip', 'zinb', 'beta', 'binomial', 'ordinal_logit', 'tweedie'];
const original = ['auto', ...mapped, 'betabinomial', 'zoib', 'gamma_hurdle', 'hurdle_poisson', 'hurdle_negbin'];
const initial: StatsConfig = {
  metric: '',
  factors: [],
  family: 'gaussian',
  measurement_type: 'continuous',
  denominator: null,
  ordinal_levels: [],
  max_order: 2,
  n_boot: 100,
  n_boot_lrt: 0,
  n_perm: 999,
  seed: 20261016,
  tweedie_power: 1.5
};
const selectClass = 'w-full rounded-md border border-input bg-background px-3 py-2 text-sm';
const titles: Record<string, string> = {
  cell_means: 'Cell means',
  marginal_means: 'Marginal means',
  contrasts: 'Pairwise and interaction contrasts',
  coefficients: 'Model coefficients',
  tests: 'Hypothesis tests',
  model_fit: 'Model fit',
  outcome_profile: 'Outcome profile',
  model_candidates: 'Model selection',
  diagnostics: 'Model diagnostics',
  diagnostics_ppc: 'Observed vs simulated summaries',
  diagnostics_by_cell: 'Residuals by cell',
  residuals: 'Residual observations',
  art_terms: 'ART omnibus tests',
  artc_contrasts: 'ART-C contrasts',
  permutation_tests: 'Permutation tests',
  effect_stability: 'Effect stability',
  comparison_terms: 'Term agreement',
  comparison_contrasts: 'Contrast agreement',
  comparison_summary: 'Agreement summary',
  validation: 'Input validation',
  factor_registry: 'Factor registry',
  quantities: 'All estimated quantities',
  pit_qq: 'PIT quantile plot',
  distribution_envelope: 'Observed vs simulated distribution',
  link_interactions: 'Link-scale interactions'
};
function format(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'number') return Number.isFinite(value) ? value.toLocaleString(undefined, {
    maximumSignificantDigits: 5
  }) : '—';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}
function DataTable({
  rows
}: {
  rows: StatsRow[];
}) {
  const [page, setPage] = useState(0);
  if (!rows.length) return <p className="text-sm text-muted-foreground">No rows produced.</p>;
  const columns = [...new Set(rows.flatMap(Object.keys))];
  const lastPage = Math.max(0, Math.ceil(rows.length / 20) - 1);
  const currentPage = Math.min(page, lastPage);
  return <>
    <div className="max-h-[32rem] overflow-auto rounded border border-border">
      <table className="w-full text-left text-xs font-mono">
        <thead className="sticky top-0 bg-card"><tr>{columns.map(key => <th key={key} className="whitespace-nowrap border-b border-border p-3">{key}</th>)}</tr></thead>
        <tbody>{rows.slice(currentPage * 20, currentPage * 20 + 20).map((row, i) => <tr key={i} className="border-b border-border/50">{columns.map(key => <td key={key} className="max-w-96 whitespace-pre-wrap break-words p-3 align-top">{format(row[key])}</td>)}</tr>)}</tbody>
      </table>
    </div>
    <div className="mt-3 flex items-center gap-3 text-xs text-muted-foreground"><span>{rows.length} rows · page {currentPage + 1}/{lastPage + 1}</span>
      <Button variant="outline" size="sm" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous</Button>
      <Button variant="outline" size="sm" disabled={currentPage === lastPage} onClick={() => setPage(currentPage + 1)}>Next</Button>
    </div>
  </>;
}
function EstimateChart({
  rows,
  title
}: {
  rows: StatsRow[];
  title: string;
}) {
  const data = rows.filter(row => typeof row.estimate === 'number').slice(0, 40);
  if (!data.length) return null;
  const values = data.flatMap(row => [row.estimate, row.ci_low, row.ci_high]).filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  let lo = Math.min(...values),
    hi = Math.max(...values);
  const ratio = data.every(row => typeof row.scale === 'string' && row.scale.startsWith('ratio'));
  const reference = ratio ? 1 : 0;
  lo = Math.min(lo, reference);
  hi = Math.max(hi, reference);
  const span = hi - lo || 1;
  lo -= span * .06;
  hi += span * .06;
  const x = (v: number) => 290 + (v - lo) / (hi - lo) * 580;
  return <Card><CardHeader><CardTitle>{title}</CardTitle><CardDescription>Point estimates; whiskers appear where this backend provides intervals. Up to 40 rows; all values are in the tables below.</CardDescription></CardHeader><CardContent>
    <div className="overflow-x-auto"><svg role="img" aria-label={title} viewBox={`0 0 920 ${data.length * 32 + 50}`} className="min-w-[700px] w-full">
      <line x1={x(reference)} x2={x(reference)} y1="4" y2={data.length * 32 + 8} stroke="var(--muted-foreground)" strokeDasharray="4 4" />
      {data.map((row, i) => {
            const y = i * 32 + 20;
            const label = String(row.label ?? row.parameter ?? i);
            return <g key={i}>
        <text x="280" y={y + 4} textAnchor="end" fill="var(--muted-foreground)" fontSize="11" fontFamily="monospace"><title>{label}</title>{label.length > 36 ? label.slice(0, 33) + '…' : label}</text>
        {typeof row.ci_low === 'number' && typeof row.ci_high === 'number' && <line x1={x(row.ci_low)} x2={x(row.ci_high)} y1={y} y2={y} stroke="var(--primary)" strokeWidth="2" />}
        <circle cx={x(row.estimate as number)} cy={y} r="4" fill="var(--primary)"><title>{label}: {format(row.estimate)}; interval {format(row.ci_low)}–{format(row.ci_high)}</title></circle>
      </g>;
          })}
      {[lo, (lo + hi) / 2, hi].map(v => <text key={v} x={x(v)} y={data.length * 32 + 35} textAnchor="middle" fill="var(--muted-foreground)" fontSize="11">{format(v)}</text>)}
    </svg></div>
  </CardContent></Card>;
}
function ResidualChart({
  rows
}: {
  rows: StatsRow[];
}) {
  const pit = rows.some(row => typeof row.pit === 'number');
  const points = rows.filter(row => typeof row.fitted === 'number' && typeof row[pit ? 'pit' : 'residual'] === 'number');
  if (!points.length) return null;
  const xs = points.map(row => Number(row.fitted)),
    ys = points.map(row => Number(row[pit ? 'pit' : 'residual']));
  const xmin = Math.min(...xs),
    xmax = Math.max(...xs),
    ymin = pit ? 0 : Math.min(...ys),
    ymax = pit ? 1 : Math.max(...ys);
  const x = (v: number) => 65 + (v - xmin) / (xmax - xmin || 1) * 790;
  const y = (v: number) => 240 - (v - ymin) / (ymax - ymin || 1) * 210;
  return <Card><CardHeader><CardTitle>{pit ? 'PIT residuals vs fitted' : 'Response residuals vs fitted'}</CardTitle><CardDescription>{pit ? 'The original simulation-based PIT diagnostic.' : 'Observed minus fitted values; descriptive diagnostics, not the original PIT test.'}</CardDescription></CardHeader><CardContent>
    <svg role="img" aria-label="Residual scatter plot" viewBox="0 0 920 290" className="w-full">
      <path d="M65 15 V240 H870" fill="none" stroke="var(--muted-foreground)" />
      <line x1="65" x2="870" y1={y(pit ? .5 : 0)} y2={y(pit ? .5 : 0)} stroke="var(--muted-foreground)" strokeDasharray="4 4" />
      {points.map((row, i) => <circle key={i} cx={x(Number(row.fitted))} cy={y(Number(row[pit ? 'pit' : 'residual']))} r="3" opacity=".55" fill="var(--chart-3)"><title>fitted={format(row.fitted)}, residual={format(row[pit ? 'pit' : 'residual'])}</title></circle>)}
      <text x="460" y="280" textAnchor="middle" fill="var(--muted-foreground)" fontSize="12">Fitted value · {format(xmin)} to {format(xmax)}</text>
      <text x="10" y="24" fill="var(--muted-foreground)" fontSize="11">{format(ymax)}</text><text x="10" y="240" fill="var(--muted-foreground)" fontSize="11">{format(ymin)}</text>
    </svg>
  </CardContent></Card>;
}
function SimulationCharts({
  tables
}: {
  tables: Record<string, StatsRow[]>;
}) {
  const qq = tables.pit_qq ?? [];
  const distribution = tables.distribution_envelope ?? [];
  if (!qq.length || !distribution.length) return null;
  const numbers = distribution.flatMap(row => [row.observed, row.sim_low, row.sim_high]).filter((v): v is number => typeof v === 'number');
  const min = Math.min(...numbers),
    max = Math.max(...numbers);
  const x = (v: unknown) => 55 + (Number(v) - min) / (max - min || 1) * 480;
  const y = (v: unknown) => 240 - Number(v) * 210;
  const line = (key: string) => distribution.map(row => `${x(row[key])},${y(row.quantile)}`).join(' ');
  return <div className="grid gap-6 xl:grid-cols-2">
    <Card><CardHeader><CardTitle>PIT quantile check</CardTitle><CardDescription>Observed PIT quantiles against a uniform distribution; the diagonal is the reference.</CardDescription></CardHeader><CardContent><svg role="img" aria-label="PIT quantile plot" viewBox="0 0 600 280" className="w-full">
      <path d="M55 30 V240 H535 M55 240 L535 30" fill="none" stroke="var(--muted-foreground)" strokeDasharray="4 4" />
      <polyline points={qq.map(row => `${55 + Number(row.expected) * 480},${y(row.observed)}`).join(' ')} fill="none" stroke="var(--primary)" strokeWidth="2" />
      <text x="55" y="262" fill="var(--muted-foreground)" fontSize="12">0</text><text x="525" y="262" fill="var(--muted-foreground)" fontSize="12">1</text>
    </svg></CardContent></Card>
    <Card><CardHeader><CardTitle>Observed vs simulated distribution</CardTitle><CardDescription>Empirical cumulative distributions. Shading shows the 95% simulation envelope for ordered outcomes.</CardDescription></CardHeader><CardContent><svg role="img" aria-label="Observed and simulated cumulative distributions" viewBox="0 0 600 280" className="w-full">
      <path d="M55 30 V240 H535" fill="none" stroke="var(--muted-foreground)" />
      <polygon points={`${line('sim_low')} ${[...distribution].reverse().map(row => `${x(row.sim_high)},${y(row.quantile)}`).join(' ')}`} fill="var(--chart-4)" opacity=".18" />
      <polyline points={line('simulated')} fill="none" stroke="var(--chart-4)" strokeWidth="2" />
      <polyline points={line('observed')} fill="none" stroke="var(--primary)" strokeWidth="2" />
      <text x="55" y="262" fill="var(--muted-foreground)" fontSize="12">{format(min)}</text><text x="505" y="262" fill="var(--muted-foreground)" fontSize="12">{format(max)}</text>
    </svg><p className="text-xs text-muted-foreground"><span className="text-primary">Observed</span> · <span className="text-[color:var(--chart-4)]">Simulated</span></p></CardContent></Card>
  </div>;
}
function UnavailableFeatures({
  items,
  experiment,
  snapshot
}: {
  items: {
    title: string;
    reason: string;
    reference: string;
  }[];
  experiment: string;
  snapshot?: string;
}) {
  if (!items.length) return null;
  return <Card><CardHeader><CardTitle>Features without an established mapping</CardTitle><CardDescription>These methods have not been reimplemented in this backend.</CardDescription></CardHeader><CardContent className="grid gap-4 md:grid-cols-2">{items.map(item => <div key={item.title} className="rounded border border-border p-4"><h3 className="text-sm font-medium">{item.title}</h3><p className="mt-2 text-sm text-muted-foreground">No mapping available. {item.reason}</p><Link className="mt-2 inline-block text-sm text-primary underline" to={`${item.reference}${experiment ? `?experiment=${experiment}${snapshot ? `&source=${snapshot}` : ''}` : ''}`}>Reference: original /stats implementation</Link></div>)}</CardContent></Card>;
}
function AnalysisOutput({
  analysis,
  experiment
}: {
  analysis: StatsAnalysis;
  experiment: string;
}) {
  const [table, setTable] = useState('cell_means');
  const output = analysis.output;
  if (!output || analysis.status !== 'completed') return null;
  const keys = Object.keys(output.tables);
  const selected = keys.includes(table) ? table : keys[0];
  const other = analysis.backend === 'stats' ? 'stats_sm' : 'stats';
  return <div className="space-y-6">
    <Card><CardHeader><CardTitle>Saved analysis <Badge variant="outline">{output.family}</Badge></CardTitle><CardDescription>{analysis.config.metric} · {output.n_observations} observations · {output.n_excluded} missing/non-numeric observations excluded · design revision {analysis.design_revision ?? '—'} · {format(output.runtime_s)} seconds</CardDescription></CardHeader><CardContent className="space-y-3">
      <p className="break-all text-xs font-mono text-muted-foreground">Source SHA-256: {analysis.fingerprint}</p>
      {output.notes.map((note, i) => <p key={i} className="text-sm text-muted-foreground">{note}</p>)}
      <Button variant="outline" render={<Link to={`/${other}?experiment=${experiment}&source=${analysis.id}`} />}><ArrowLeftRight className="size-4" />Compare this exact input snapshot in /{other}</Button>
    </CardContent></Card>
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
      <EstimateChart rows={output.tables.cell_means ?? []} title="Predicted cell means" />
      <EstimateChart rows={output.tables.marginal_means ?? []} title="Equally weighted marginal means" />
    </div>
    <EstimateChart rows={output.tables.contrasts ?? []} title="Pairwise and interaction effects" />
    <ResidualChart rows={output.tables.residuals ?? []} />
    <SimulationCharts tables={output.tables} />
    <Card><CardHeader><CardTitle>Analysis tables</CardTitle><CardDescription>Every generated table is available here, including diagnostic flags and method labels.</CardDescription></CardHeader><CardContent className="space-y-4">
      <select aria-label="Analysis table" className={selectClass} value={selected} onChange={e => setTable(e.target.value)}>{keys.map(key => <option key={key} value={key}>{titles[key] ?? key} ({output.tables[key].length})</option>)}</select>
      <DataTable key={selected} rows={output.tables[selected] ?? []} />
    </CardContent></Card>
    {output.report && <Card><CardHeader><CardTitle>Original package report</CardTitle></CardHeader><CardContent><pre className="max-h-[40rem] overflow-auto whitespace-pre-wrap text-xs font-mono">{output.report}</pre></CardContent></Card>}
  </div>;
}
export function StatsPrototypePage({
  backend
}: {
  backend: StatsBackend;
}) {
  const [params, setParams] = useSearchParams();
  const experiment = params.get('experiment') ?? '';
  const snapshot = params.get('source') ?? undefined;
  const client = useQueryClient();
  const [config, setConfig] = useState<StatsConfig>(initial);
  const [analysisId, setAnalysisId] = useState('');
  const [validation, setValidation] = useState('');
  const experiments = useQuery({
    queryKey: ['experiments'],
    queryFn: () => experimentsApi.list()
  });
  const capabilities = useQuery({
    queryKey: ['stats-capabilities', backend],
    queryFn: () => statsApi.capabilities(backend)
  });
  const source = useQuery({
    queryKey: ['stats-source', experiment],
    queryFn: () => statsApi.source(experiment),
    enabled: !!experiment && !snapshot
  });
  const reference = useQuery({
    queryKey: ['stats-reference', experiment, snapshot],
    queryFn: () => statsApi.get(experiment, snapshot!),
    enabled: !!experiment && !!snapshot
  });
  const sourceData = snapshot ? reference.data?.source : source.data;
  const history = useQuery({
    queryKey: ['stats-history', experiment, backend],
    queryFn: () => statsApi.history(experiment, backend),
    enabled: !!experiment,
    refetchInterval: query => query.state.data?.some(a => a.status === 'pending' || a.status === 'running') ? 2500 : false
  });
  const analysis = useQuery({
    queryKey: ['stats-analysis', experiment, analysisId],
    queryFn: () => statsApi.get(experiment, analysisId),
    enabled: !!analysisId,
    refetchInterval: query => query.state.data?.status === 'pending' || query.state.data?.status === 'running' ? 2500 : false
  });
  const mutation = useMutation({
    mutationFn: () => statsApi.run(experiment, backend, config, snapshot),
    onSuccess: data => {
      setAnalysisId(data.id);
      void client.invalidateQueries({
        queryKey: ['stats-history', experiment, backend]
      });
    }
  });
  useEffect(() => {
    setAnalysisId('');
    setConfig(initial);
    setValidation('');
    mutation.reset();
  }, [experiment, backend, snapshot]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (reference.data) setConfig(reference.data.config);
  }, [reference.data, backend]);
  const activeId = analysisId || (snapshot ? history.data?.find(a => a.fingerprint === reference.data?.fingerprint && a.config.metric === reference.data?.config.metric)?.id : history.data?.[0]?.id);
  useEffect(() => {
    if (!analysisId && activeId) setAnalysisId(activeId);
  }, [activeId, analysisId]);
  const update = <K extends keyof StatsConfig,>(key: K, value: StatsConfig[K]) => setConfig(c => ({
    ...c,
    [key]: value
  }));
  const busy = mutation.isPending || analysis.data?.status === 'pending' || analysis.data?.status === 'running';
  const error = validation || mutation.error?.message || source.error?.message || reference.error?.message || history.error?.message || analysis.error?.message || experiments.error?.message || capabilities.error?.message;
  const numeric = (key: 'max_order' | 'n_boot' | 'n_boot_lrt' | 'n_perm' | 'seed' | 'tweedie_power', title: string, min: number, max: number, step = 1) => <label className="space-y-2 text-sm">{title}<Input type="number" min={min} max={max} step={step} value={config[key]} onChange={e => update(key, Number(e.target.value))} /></label>;
  return <div className="min-h-screen"><AppHeader /><main className="mx-auto max-w-[1600px] space-y-6 p-6">
    <div className="flex flex-wrap items-center justify-between gap-4"><div><h1 className="flex items-center gap-3 text-2xl font-semibold"><Activity className="text-primary" />{backend === 'stats' ? 'Original statistics' : 'Established statistics'}</h1><p className="mt-2 text-sm text-muted-foreground">Prototype · {backend === 'stats' ? 'asaree.stats custom engine' : 'statsmodels + scikit-learn'} · database input and saved database outputs</p></div><Button variant="outline" render={<Link to={`/${backend === 'stats' ? 'stats_sm' : 'stats'}${experiment ? `?experiment=${experiment}` : ''}`} />}>Switch backend</Button></div>
    <Card><CardHeader><CardTitle>Experiment and analysis setup</CardTitle><CardDescription>Whole-dataset replicate metrics from the current design revision. Replicates are treated as independent; datasets and paired seeds are not inferred as blocks. Existing Results features are unchanged.</CardDescription></CardHeader><CardContent className="space-y-5">
      <label className="block space-y-2 text-sm">Experiment<select aria-label="Experiment" value={experiment} className={selectClass} onChange={e => setParams(e.target.value ? {
              experiment: e.target.value
            } : {})}><option value="">Choose an experiment</option>{experiments.data?.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}</select></label>
      {sourceData && <p className="text-sm text-muted-foreground">Design revision {sourceData.design_revision ?? '—'} · {sourceData.n_replicates} whole-dataset replicates · {sourceData.excluded_row_replicates} row-mode replicates excluded. Row-mode analysis needs a separate dependence model.</p>}
      {snapshot && <div className="rounded border border-primary/40 p-3 text-sm">Comparison uses the saved input snapshot from <span className="font-mono">{snapshot.slice(0, 8)}</span>, even if live results have changed. Review the family setting when switching from an unsupported model. <Button variant="ghost" size="sm" onClick={() => setParams({
              experiment
            })}>Use live database input</Button></div>}
      {experiment && <>
        <div className="grid gap-4 md:grid-cols-3">
          <label className="space-y-2 text-sm">Outcome metric<select aria-label="Outcome metric" className={selectClass} value={config.metric} onChange={e => update('metric', e.target.value)}><option value="">Choose a metric</option>{sourceData?.metrics.map(m => <option key={m.name} value={m.name}>{m.name} ({m.n} measured)</option>)}{snapshot && config.metric && !sourceData?.metrics.some(m => m.name === config.metric) && <option value={config.metric}>{config.metric} (snapshot)</option>}</select></label>
          <label className="space-y-2 text-sm">Model family<select aria-label="Model family" className={selectClass} value={config.family} onChange={e => update('family', e.target.value)}>{backend === 'stats_sm' && !mapped.includes(config.family) && <option value={config.family}>{config.family} — no mapping available</option>}{(backend === 'stats' ? original : mapped).map(f => <option key={f}>{f}</option>)}</select></label>
          <label className="space-y-2 text-sm">Measurement type<select aria-label="Measurement type" className={selectClass} value={config.measurement_type} onChange={e => update('measurement_type', e.target.value)}>{['continuous', 'count', 'binary', 'proportion', 'ordinal', 'auto'].map(t => <option key={t}>{t}</option>)}</select></label>
        </div>
        <fieldset><legend className="mb-2 text-sm">Categorical experimental factors (up to six)</legend><div className="flex flex-wrap gap-4">{[...new Set([...(sourceData?.factors.map(f => f.name) ?? []), ...(snapshot ? config.factors : [])])].map(name => <label key={name} className="flex items-center gap-2 text-sm font-mono"><input type="checkbox" checked={config.factors.includes(name)} onChange={e => update('factors', e.target.checked ? [...config.factors, name] : config.factors.filter(f => f !== name))} />{name}</label>)}</div></fieldset>
        <div className="grid gap-4 md:grid-cols-3">
          {numeric('max_order', 'Maximum interaction order', 1, 3)}
          {numeric('seed', 'Random seed', 0, 4294967295)}
          <label className="space-y-2 text-sm">Trials metric (optional, successes/trials)<select aria-label="Trials metric" className={selectClass} value={config.denominator ?? ''} onChange={e => update('denominator', e.target.value || null)}><option value="">No denominator</option>{sourceData?.metrics.map(m => <option key={m.name}>{m.name}</option>)}</select></label>
          {(config.family === 'ordinal_logit' || config.measurement_type === 'ordinal') && <label className="space-y-2 text-sm">Ordinal levels, lowest to highest (one per line)<textarea aria-label="Ordinal levels" className={selectClass} value={config.ordinal_levels.join('\n')} onChange={e => update('ordinal_levels', e.target.value.split('\n'))} /></label>}
          {backend === 'stats_sm' && config.family === 'tweedie' && numeric('tweedie_power', 'Fixed Tweedie power', 1.01, 1.99, .01)}
        </div>
        {backend === 'stats' && <><div className="grid gap-4 md:grid-cols-3">{numeric('n_boot', 'Interval bootstrap draws', 20, 5000)}{numeric('n_boot_lrt', 'Bootstrap LRT draws (0 = HC3)', 0, 1999)}{numeric('n_perm', 'Permutations', 99, 10000)}</div><p className="text-xs text-muted-foreground">Defaults are exploratory. Original scientific defaults are 5,000 interval draws, 1,999 LRT draws and 10,000 permutations; larger analyses can take minutes. Jobs run on the existing worker with a 30-minute limit.</p></>}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {backend === 'stats_sm' && !mapped.includes(config.family) && <p className="text-sm text-muted-foreground">No mapping available for {config.family}. <Link className="text-primary underline" to={`/stats?experiment=${experiment}${snapshot ? `&source=${snapshot}` : ''}`}>Reference: original /stats implementation</Link>. Select a supported family to explore a different model.</p>}
        <div className="flex flex-wrap gap-3"><Button disabled={busy || !config.metric || !config.factors.length || !!snapshot && !reference.data || backend === 'stats_sm' && !mapped.includes(config.family)} onClick={() => {
                setValidation('');
                if (config.factors.length > 6) {
                  setValidation('Choose at most six factors.');
                  return;
                }
                mutation.mutate();
              }}><Play className="size-4" />{busy ? 'Analysis queued / running…' : 'Run and save analysis'}</Button>{analysis.data && <Button variant="outline" onClick={() => setConfig(analysis.data!.config)}>Load saved settings</Button>}</div>
      </>}
    </CardContent></Card>
    {!experiment && <p className="text-sm text-muted-foreground">Select an experiment with scored replicates to begin.</p>}
    {error && !experiment && <p role="alert" className="text-destructive">{error}</p>}
    {!!history.data?.length && <label className="block space-y-2 text-sm">Saved analyses<select aria-label="Saved analyses" className={selectClass} value={analysisId} onChange={e => setAnalysisId(e.target.value)}><option value="">Choose a saved analysis</option>{history.data.map(a => <option value={a.id} key={a.id}>{new Date(a.created_at).toLocaleString()} · {a.config.metric} · {a.config.family} · {a.status}</option>)}</select></label>}
    {analysis.data?.status === 'failed' && <p role="alert" className="rounded border border-destructive p-4 text-sm text-destructive whitespace-pre-wrap">{analysis.data.error}</p>}
    {analysis.data && (analysis.data.status === 'pending' || analysis.data.status === 'running') && <p role="status" className="text-sm text-muted-foreground">{analysis.data.status === 'pending' ? 'Waiting for the ASAREE worker.' : 'Computing statistics in the background.'} You can leave this page and return to the saved analysis.</p>}
    {analysis.data && <AnalysisOutput key={analysis.data.id} analysis={analysis.data} experiment={experiment} />}
    <UnavailableFeatures items={capabilities.data?.unavailable ?? []} experiment={experiment} snapshot={analysis.data?.id ?? snapshot} />
  </main></div>;
}

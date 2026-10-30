import type { StatsSummary } from '@/types/stats'

export function primaryComparisonResult(effect: StatsSummary['effects'][number]): string {
  const low = effect.primary_ci_low
  const high = effect.primary_ci_high
  if (effect.primary_status !== 'completed' || low == null || high == null
    || !Number.isFinite(low) || !Number.isFinite(high) || low > high) return 'Primary inference unavailable'
  const nullValue = effect.scale.startsWith('ratio') ? 1 : 0
  return low > nullValue || high < nullValue ? 'Evidence of a difference' : 'No clear evidence of a difference'
}

export function describeStatsReportFindings(report: StatsSummary): string[] {
  const secondary = describeStatsFindings(report.tests, report.alpha)
  if (report.primary_analysis !== 'parametric_bootstrap_intervals') return secondary
  const primary = report.effects.map(effect => {
    const result = primaryComparisonResult(effect)
    if (result === 'Primary inference unavailable') {
      return `Primary parametric-bootstrap inference for ${effect.comparison} was unavailable. Sensitivity results do not replace it.`
    }
    const evidence = result === 'Evidence of a difference' ? 'evidence' : 'no clear evidence'
    return `The primary parametric-bootstrap interval found ${evidence} of a difference for ${effect.comparison} (pointwise; not multiplicity-adjusted).`
  })
  return [...primary, ...secondary.map(finding => `Secondary factor test: ${finding}`)]
}

// Summarize numeric tests using fixed rules. Never display stored narrative.
export function describeStatsFindings(tests: StatsSummary['tests'], alpha = .05): string[] {
  function evidence(p: number | null | undefined) {
    return p == null || !Number.isFinite(p) ? null : p < alpha
  }
  return tests.map(test => {
    const factors = test.effect.split(':')
    const subject = factors.length === 1 ? `${factors[0]} has an effect on this metric`
      : factors.length === 2 ? `the effect of ${factors[0]} changes across ${factors[1]} settings`
        : `the effects of ${factors.join(' and ')} depend on one another`
    const main = evidence(test.p_adjusted)
    const check = evidence(test.permutation_p_adjusted)
    const name = test.resampling_name ?? 'permutation check'
    if (main !== null && check === main) {
      return main
        ? `Both the main test and the ${name} found evidence that ${subject}.`
        : `Neither the main test nor the ${name} found clear evidence that ${subject}.`
    }
    const conclusion = main === null
      ? `The main statistical test for ${factors.join(' and ')} was unavailable.`
      : main ? `The main test found evidence that ${subject}.`
        : `The main test found no clear evidence that ${subject}.`
    if (main === null && check === null) return `${conclusion.slice(0, -1)}, and so was its ${name}.`
    const sensitivity = check === null ? `A ${name} was not available for this comparison.`
      : check ? `The ${name} found evidence that ${subject}.`
        : `The ${name} found no clear evidence that ${subject}.`
    const disagreement = main !== null && check !== null
      ? ' The two methods differ; interpret this finding cautiously.' : ''
    return `${conclusion} ${sensitivity}${disagreement}`
  })
}

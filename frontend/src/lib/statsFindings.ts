import type { StatsSummary } from '@/types/stats'

// Summarize numeric tests using fixed rules. Never display stored narrative.
export function describeStatsFindings(tests: StatsSummary['tests']): string[] {
  function evidence(p: number | null | undefined) {
    return p == null || !Number.isFinite(p) ? null : p < .05
  }
  return tests.map(test => {
    const factors = test.effect.split(':')
    const subject = factors.length === 1 ? `${factors[0]} has an effect on this metric`
      : factors.length === 2 ? `the effect of ${factors[0]} changes across ${factors[1]} settings`
        : `the effects of ${factors.join(' and ')} depend on one another`
    const main = evidence(test.p_adjusted)
    const check = evidence(test.permutation_p_adjusted)
    if (main !== null && check === main) {
      return main
        ? `Both the main test and the permutation check found evidence that ${subject}.`
        : `Neither the main test nor the permutation check found clear evidence that ${subject}.`
    }
    const conclusion = main === null
      ? `The main statistical test for ${factors.join(' and ')} was unavailable.`
      : main ? `The main test found evidence that ${subject}.`
        : `The main test found no clear evidence that ${subject}.`
    const sensitivity = check === null ? 'A permutation check was not available for this comparison.'
      : check ? `The permutation check found evidence that ${subject}.`
        : `The permutation check found no clear evidence that ${subject}.`
    const disagreement = main !== null && check !== null
      ? ' The two methods differ; interpret this finding cautiously.' : ''
    return `${conclusion} ${sensitivity}${disagreement}`
  })
}

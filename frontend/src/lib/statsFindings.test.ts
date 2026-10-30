import { describe, expect, it } from 'vitest'
import { describeStatsFindings } from './statsFindings'

describe('statistical findings use the recorded threshold and procedure', () => {
  it('applies the configured threshold to both procedures', () => {
    const tests = [{ effect: 'Treatment', p_adjusted: .02, permutation_p_adjusted: .03, result: '' }]
    expect(describeStatsFindings(tests, .01)[0]).toContain('Neither')
    expect(describeStatsFindings(tests, .05)[0]).toContain('Both')
  })

  it('names null-model bootstrap checks and retains a nonsignificant analytic conclusion', () => {
    const findings = describeStatsFindings([{
      effect: 'A:B', p_adjusted: .2, permutation_p_adjusted: .01,
      resampling_name: 'parametric bootstrap check', result: '',
    }])
    expect(findings[0]).toBe('The main test found no clear evidence that the effect of A changes across B settings. '
      + 'The parametric bootstrap check found evidence that the effect of A changes across B settings. '
      + 'The two methods differ; interpret this finding cautiously.')
  })

  it('does not turn unavailable tests into nonsignificant conclusions', () => {
    expect(describeStatsFindings([{ effect: 'A', p_adjusted: null, result: '' }])).toEqual([
      'The main statistical test for A was unavailable, and so was its permutation check.',
    ])
  })
})

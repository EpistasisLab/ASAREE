import { describe, expect, it } from 'vitest'
import { formatTokens } from './tokenFormatting'

describe('token counts', () => {
  it.each([
    [1_000_000, '1.00M'],
    [1_200_000, '1.20M'],
    [1_250_000, '1.25M'],
    [1_256_789, '1.26M'],
    [12_345_678, '12.35M'],
    [1_250_000_000, '1.25B'],
    [12500, '12.5K'],
    [850, '850'],
    [0, '0'],
  ])('formats %s tokens as %s', (value, expected) => {
    expect(formatTokens(value)).toBe(expected)
  })
})

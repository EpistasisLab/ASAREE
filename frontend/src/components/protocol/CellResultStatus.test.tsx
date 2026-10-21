import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ExecutionCell } from '@/types/executionResults'
import { CellResultStatus } from './CellResultStatus'

const cell: ExecutionCell = { cell_id: 'cell', cell_label: 'model=a', factor_values: { model: 'a' },
  replicate_count: 3, total: 3, usable_results: 2, results_needing_attention: 1 }

describe('Cell result status', () => {
  it('explains the usable results and specific reason a cell needs attention', () => {
    render(<CellResultStatus cell={{ ...cell, result_status: 'needs_attention', result_issues: { measurements_missing: 1 } }} unit="replicate" />)
    expect(screen.getByText('Needs attention')).toBeInTheDocument()
    expect(screen.getByText('2 of 3 replicates have usable results.')).toBeInTheDocument()
    expect(screen.getByText('1 replicate needs attention.')).toBeInTheDocument()
    expect(screen.getByText('1 replicate has missing, unavailable, or unreported measurements.')).toBeInTheDocument()
  })

  it('uses row labels and distinguishes run failures, cancellation, limits and measurement failures', () => {
    render(<CellResultStatus cell={{ ...cell, total: 8, usable_results: 3, result_status: 'needs_attention', results_needing_attention: 5,
      result_issues: { failed: 2, cancelled: 1, limit_reached: 1, measurement_failed: 1 } }} unit="row" />)
    expect(screen.getByText('5 rows need attention.')).toBeInTheDocument()
    expect(screen.getByText('2 rows failed during the run.')).toBeInTheDocument()
    expect(screen.getByText('1 row was cancelled.')).toBeInTheDocument()
    expect(screen.getByText('1 row hit the iteration limit.')).toBeInTheDocument()
    expect(screen.getByText('Measurement processing failed for 1 row.')).toBeInTheDocument()
  })

  it('confirms when all planned results are usable', () => {
    render(<CellResultStatus cell={{ ...cell, result_status: 'ready', usable_results: 3, results_needing_attention: 0 }} unit="replicate" />)
    expect(screen.getByText('Ready to review')).toBeInTheDocument()
    expect(screen.getByText('3 of 3 replicates have usable results.')).toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })

  it('keeps unfinished work distinct from result problems', () => {
    render(<CellResultStatus cell={{ ...cell, result_status: 'awaiting_results', results_needing_attention: 0 }} unit="replicate" />)
    expect(screen.getByText('Awaiting results')).toBeInTheDocument()
    expect(screen.getByText('Waiting for remaining replicates or measurement processing. No result problems have been identified.')).toBeInTheDocument()
  })

  it('does not guess a status while older cached results are refreshing', () => {
    render(<CellResultStatus cell={cell} />)
    expect(screen.getByRole('status')).toHaveTextContent('Preparing cell result status…')
    expect(screen.queryByText('Ready to review')).not.toBeInTheDocument()
  })
})

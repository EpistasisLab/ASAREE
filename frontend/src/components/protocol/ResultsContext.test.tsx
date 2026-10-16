import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ExecutionSummary } from '@/types/executionResults'
import { ResultsContext, ResultsMetadata } from './ResultsContext'

const summary = {
  unit: 'replicate', protocol_revision: 2, design_revision: 3,
  protocol_revision_id: 'publication', design_revision_id: 'design',
  datasets: [{ id: 'dataset', name: 'Classification dataset' }],
  cell_count: 24, cell_result_counts: { ready: 12, needs_attention: 5, awaiting_results: 7 },
  as_of: '2026-10-15T18:30:00Z', updated_at: '2026-10-01T00:00:00Z',
} as ExecutionSummary

function renderContext(value: ExecutionSummary) {
  return render(<><ResultsMetadata summary={value} /><ResultsContext summary={value} /></>)
}

describe('Results context', () => {
  it('shows cell result counts, published scope, dataset and the summary calculation time', () => {
    renderContext(summary)
    const counts = within(screen.getByRole('region', { name: 'Cell results overview' }))
    expect(counts.getByText('Total cells')).toBeInTheDocument()
    expect(counts.getByText('24')).toBeInTheDocument()
    expect(counts.getByText('Ready to review')).toBeInTheDocument()
    expect(counts.getByText('12')).toBeInTheDocument()
    expect(counts.getByText('Needs attention')).toBeInTheDocument()
    expect(counts.getByText('5')).toBeInTheDocument()
    expect(counts.getByText('Awaiting results')).toBeInTheDocument()
    expect(counts.getByText('7')).toBeInTheDocument()
    expect(screen.queryByText('Results scope')).not.toBeInTheDocument()
    expect(screen.queryByText('Classification dataset')).not.toBeInTheDocument()
    expect(screen.getByText('Whole dataset per replicate')).toBeInTheDocument()
    expect(document.querySelector('time')).toHaveAttribute('datetime', '2026-10-15T18:30:00.000Z')
  })

  it('keeps counts at cell level for per-row runs and preserves zero counts', () => {
    renderContext({ ...summary, unit: 'row', source_row_count: 240,
      cell_result_counts: { ready: 0, needs_attention: 0, awaiting_results: 24 } })
    expect(screen.getByText('Per dataset row · 240 source rows')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Cell results overview' })).getAllByText('0')).toHaveLength(2)
  })

  it('keeps missing metadata and result counts unknown while an older cache rebuilds', () => {
    renderContext({ ...summary, datasets: undefined, cell_result_counts: undefined,
      as_of: undefined, updated_at: null })
    expect(screen.getByText('Preparing cell result counts…')).toBeInTheDocument()
    expect(screen.getByText('Not available')).toBeInTheDocument()
  })
})

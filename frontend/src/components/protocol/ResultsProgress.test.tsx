import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ExecutionCell, ExecutionSummary } from '@/types/executionResults'
import { ResultsProgress } from './ResultsProgress'
import { CellUsageChart } from './CellUsageChart'

const summary: ExecutionSummary = {
  scope_key: 'scope', unit: 'replicate', ready: true, protocol_id: 'protocol',
  protocol_revision_id: 'revision', design_revision_id: 'design', generation: 1,
  change_id: 1, refreshing: false, updated_at: null, counts: { completed: 1 }, total: 2,
  design_spec: null, metric_types: {}, metric_aggregations: {}, metric_directions: {},
}
const cells: ExecutionCell[] = [
  { cell_id: 'a', cell_label: 'model=a', cell_number: 7, factor_values: {},
    usage: { total_tokens: 1200, cost_usd: 0.025, duration_seconds: 65, input_tokens: null, output_tokens: null, agent_run_count: null } },
  { cell_id: 'b', cell_label: 'model=b', cell_number: 8, factor_values: {},
    usage: { total_tokens: 0, cost_usd: 0, duration_seconds: null, input_tokens: null, output_tokens: null, agent_run_count: null } },
]

describe('Results usage charts', () => {
  it('switches tile and chart labels when the experiment completes', () => {
    const view = render(<ResultsProgress summary={summary} cells={cells} cellsLoading={false} />)
    expect(screen.getByRole('button', { name: 'Show tokens so far by cell' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show cost so far by cell' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Show accumulated execution time by cell' }))
    expect(screen.getByRole('region', { name: 'Accumulated execution time by cell' })).toBeInTheDocument()
    view.rerender(<ResultsProgress summary={{ ...summary, counts: { completed: 2 } }} cells={cells} cellsLoading={false} />)
    expect(screen.getByRole('button', { name: 'Show total tokens by cell' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show final cost by cell' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show total execution time by cell' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('region', { name: 'Total execution time by cell' })).toBeInTheDocument()
  })

  it('switches between cell metrics using the tiles and keeps stable cell numbers', () => {
    render(<ResultsProgress summary={summary} cells={cells} cellsLoading={false} />)
    expect(screen.getByRole('region', { name: 'Tokens so far by cell' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Cell 7: 1,200' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Show cost so far by cell' }))
    expect(screen.getByRole('button', { name: 'Show cost so far by cell' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Show tokens so far by cell' })).toHaveAttribute('aria-pressed', 'false')
    expect(within(screen.getByRole('region', { name: 'Cost so far by cell' })).getByRole('img', { name: 'Cell 7: $0.03' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Show accumulated execution time by cell' }))
    expect(screen.getByRole('img', { name: 'Cell 7: 65 s' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Cell 8: Not reported' })).toBeInTheDocument()
  })

  it('distinguishes zero usage from missing usage', () => {
    render(<ResultsProgress summary={summary} cells={[...cells, { cell_id: 'c', cell_label: 'model=c', cell_number: 9, factor_values: {} }]} cellsLoading={false} />)
    expect(screen.getByRole('img', { name: 'Cell 8: 0' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Cell 9: Not reported' })).toBeInTheDocument()
  })

  it.each([
    [119, 's', '119 s', '30 s'],
    [120, 'min', '2 min', '0.5 min'],
    [7199, 'min', '120 min', '0.5 min'],
    [7200, 'hr', '2 hr', '0.008 hr'],
  ])('uses one duration unit for every bar when the longest cell takes %s seconds', (seconds, unit, longestLabel, shorterLabel) => {
    render(<CellUsageChart metric="duration_seconds" loading={false} cells={[
      { ...cells[0], usage: { ...cells[0].usage!, duration_seconds: seconds } },
      { ...cells[1], usage: { ...cells[1].usage!, duration_seconds: 30 } },
    ]} />)
    expect(screen.getByText(`Execution time (${unit})`)).toBeInTheDocument()
    expect(screen.getByRole('img', { name: `Cell 7: ${longestLabel}` })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: `Cell 8: ${shorterLabel}` })).toHaveAttribute('title', 'Cell 8 · model=b\nAccumulated execution time: 30 sec')
  })

  it('shows an exact duration in the tooltip while rounding bar labels', () => {
    render(<CellUsageChart metric="duration_seconds" loading={false} cells={[
      { ...cells[0], usage: { ...cells[0].usage!, duration_seconds: 204 } },
    ]} />)
    expect(screen.getByRole('img', { name: 'Cell 7: 3.4 min' })).toHaveAttribute('title', 'Cell 7 · model=a\nAccumulated execution time: 3 min 24 sec')
  })

  it.each([
    [850, '850', 'Tokens'],
    [12500, '12.5K', 'Tokens (thousands)'],
    [1250000, '1.25M', 'Tokens (millions)'],
    [1250000000, '1.25B', 'Tokens (billions)'],
  ])('formats %s tokens compactly while keeping exact counts in tooltips', (tokens, compact, axisLabel) => {
    const usage = { ...cells[0].usage!, total_tokens: tokens }
    render(<ResultsProgress summary={{ ...summary, usage }} cells={[{ ...cells[0], usage }]} cellsLoading={false} />)
    const exact = tokens.toLocaleString('en-US')
    expect(within(screen.getByRole('button', { name: 'Show tokens so far by cell' })).getByText(compact)).toHaveAttribute('title', `${exact} tokens`)
    const chart = within(screen.getByRole('region', { name: 'Tokens so far by cell' }))
    expect(chart.getByText(axisLabel)).toBeInTheDocument()
    expect(chart.getByRole('img', { name: `Cell 7: ${exact}` })).toHaveAttribute('title', `Cell 7 · model=a\nTokens so far: ${exact}`)
    expect(chart.getByText(compact)).toBeInTheDocument()
  })
})

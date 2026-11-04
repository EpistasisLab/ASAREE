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
  it.each(['Input tokens', 'Output tokens', 'Total tokens'])('abbreviates millions in selected %s while keeping exact tooltip counts', (label) => {
    render(<ResultsProgress summary={{ ...summary, metric_labels: { selected: label },
      metric_types: { selected: 'number' }, metric_units: { selected: 'tokens' }, metric_aggregations: { selected: 'sum' },
    }} cells={[{ ...cells[0], metric_values: { selected: 1_250_000 }, metric_counts: { selected: 1 } }]} cellsLoading={false} />)
    const chart = within(screen.getByRole('region', { name: `${label} by cell` }))
    expect(chart.getByText('1.25M')).toBeVisible()
    expect(chart.queryByText('1.25M tokens')).not.toBeInTheDocument()
    expect(chart.getByLabelText(`Y-axis: ${label} (tokens)`)).toBeVisible()
    expect(chart.getByLabelText('X-axis: Cell')).toBeVisible()
    expect(chart.getByRole('img', { name: 'Cell 7: 1,250,000' })).toHaveAttribute('title', expect.stringContaining('1,250,000'))
  })

  it('offers only selected experiment metrics, without duplicate operational usage summaries', () => {
    render(<ResultsProgress summary={{ ...summary, metric_labels: {
      tokens: 'Total tokens', cost: 'Cost', duration: 'Duration', outcome: 'Custom outcome',
    } }} cells={cells} cellsLoading={false} />)
    const picker = screen.getByRole('combobox', { name: 'Metric by cell' })
    const options = within(picker).getAllByRole('option').filter(option => !option.hasAttribute('disabled'))
    expect(options.map(option => option.getAttribute('value'))).toEqual(['metric:cost', 'metric:outcome', 'metric:duration', 'metric:tokens'])
    expect(options.map(option => option.textContent)).toEqual(['Cost', 'Custom outcome', 'Duration', 'Total tokens'])
    expect(within(picker).queryByText('Select an experiment metric')).not.toBeInTheDocument()
    expect(within(picker).queryByRole('group')).not.toBeInTheDocument()
    expect(picker).toHaveValue('metric:cost')
    expect(within(picker).queryByRole('group', { name: 'Operational usage summaries' })).not.toBeInTheDocument()
    fireEvent.change(picker, { target: { value: 'metric:cost' } })
    expect(picker).toHaveValue('metric:cost')
    fireEvent.click(screen.getByRole('button', { name: 'Show tokens so far by cell' }))
    expect(picker).toHaveValue('')
    expect(screen.getByRole('region', { name: 'Tokens by cell' })).toBeVisible()
  })

  it('disables the metric dropdown when the experiment has no selected metrics', () => {
    render(<ResultsProgress summary={summary} cells={cells} cellsLoading={false} />)
    expect(screen.getByRole('combobox', { name: 'Metric by cell' })).toBeDisabled()
    expect(screen.getByRole('option', { name: 'No experiment metrics selected' })).toBeInTheDocument()
  })

  it('lists every selected built-in and custom metric even when no results exist', () => {
    render(<ResultsProgress summary={{ ...summary, design_spec: { metrics: [
      { id: 'tokens', name: 'Total tokens', kind: 'runtime', catalogKey: 'total_tokens' },
      { id: 'hash', name: 'Prompt SHA-256', kind: 'runtime', catalogKey: 'prompt_sha256', valueType: 'opaque', aggregation: 'none' },
      { id: 'custom', name: 'Custom outcome', kind: 'custom' },
    ] }, metric_labels: { total_tokens: 'Total tokens', hash: 'Prompt SHA-256', custom: 'Custom outcome' } }} cells={[]} cellsLoading={false} />)
    const group = within(screen.getByRole('combobox', { name: 'Metric by cell' }))
    expect(group.getAllByRole('option')).toHaveLength(3)
    expect(group.getByRole('option', { name: 'Prompt SHA-256' })).toBeEnabled()
    expect(group.getByRole('option', { name: 'Custom outcome' })).toBeEnabled()
  })

  it('lists selected metrics in row mode and directs users to cell details without aggregation', () => {
    render(<ResultsProgress summary={{ ...summary, unit: 'row', metric_labels: { outcome: 'Row outcome' } }}
      cells={cells} cellsLoading={false} />)
    fireEvent.change(screen.getByRole('combobox', { name: 'Metric by cell' }), { target: { value: 'metric:outcome' } })
    expect(screen.getByText(/Open a cell below to inspect its recorded values/)).toBeVisible()
    expect(screen.getByText(/Row outcomes are listed individually without cell aggregation/)).toBeVisible()
    expect(screen.queryByRole('region', { name: 'Row outcome by cell' })).not.toBeInTheDocument()
  })

  it('selects a published custom metric in the existing chart and preserves zero and missing values', () => {
    render(<ResultsProgress summary={{ ...summary,
      metric_labels: { score: 'Custom score', note: 'Explanation' }, metric_types: { score: 'number' },
      metric_aggregations: { score: 'mean' } }} cells={[
      { ...cells[0], metric_values: { score: -2 }, metric_counts: { score: 3 } },
      { ...cells[1], metric_values: { score: 0 }, metric_counts: { score: 2 } },
      { cell_id: 'missing', cell_label: 'model=c', cell_number: 9, factor_values: {} },
    ]} cellsLoading={false} />)
    fireEvent.change(screen.getByRole('combobox', { name: 'Metric by cell' }), { target: { value: 'metric:score' } })
    const chart = within(screen.getByRole('region', { name: 'Custom score by cell' }))
    expect(chart.getByRole('img', { name: 'Cell 7: -2' })).toHaveAttribute('title', expect.stringContaining('3 measured replicates'))
    expect(chart.getByRole('img', { name: 'Cell 8: 0' })).toBeInTheDocument()
    expect(chart.getByRole('img', { name: 'Cell 9: Not reported' })).toBeInTheDocument()
    expect(chart.getByText('Mean across measured replicates in each cell.')).toBeVisible()
    fireEvent.change(screen.getByRole('combobox', { name: 'Metric by cell' }), { target: { value: 'metric:note' } })
    expect(screen.getByText(/no numeric cell aggregation/)).toBeVisible()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('switches tile and chart labels when the experiment completes', () => {
    const view = render(<ResultsProgress summary={summary} cells={cells} cellsLoading={false} />)
    expect(screen.getByRole('button', { name: 'Show tokens so far by cell' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show cost so far by cell' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Show accumulated execution time by cell' }))
    expect(screen.getByRole('region', { name: 'Accumulated execution time by cell' })).toBeInTheDocument()
    expect(screen.getByText('Partial data')).toBeInTheDocument()
    view.rerender(<ResultsProgress summary={{ ...summary, counts: { completed: 2 } }} cells={cells} cellsLoading={false} />)
    expect(screen.getByRole('button', { name: 'Show total tokens by cell' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show final cost by cell' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show total execution time by cell' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('region', { name: 'Total execution time by cell' })).toBeInTheDocument()
    expect(screen.queryByText('Partial data')).not.toBeInTheDocument()
  })

  it('switches between cell metrics using the tiles and keeps stable cell numbers', () => {
    render(<ResultsProgress summary={summary} cells={cells} cellsLoading={false} />)
    expect(screen.getByRole('region', { name: 'Tokens by cell' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Cell 7: 1,200' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Show cost so far by cell' }))
    expect(screen.getByRole('button', { name: 'Show cost so far by cell' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Show tokens so far by cell' })).toHaveAttribute('aria-pressed', 'false')
    expect(within(screen.getByRole('region', { name: 'Cost by cell' })).getByRole('img', { name: 'Cell 7: $0.03' })).toBeInTheDocument()
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
    const chart = within(screen.getByRole('region', { name: 'Tokens by cell' }))
    expect(chart.getByText(axisLabel)).toBeInTheDocument()
    expect(chart.getByRole('img', { name: `Cell 7: ${exact}` })).toHaveAttribute('title', `Cell 7 · model=a\nTokens so far: ${exact}`)
    expect(chart.getByText(compact)).toBeInTheDocument()
  })
})

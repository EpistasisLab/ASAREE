import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ExecutionCell } from '@/types/executionResults'
import { ResultsCellsTable } from './ResultsCellsTable'

const cell: ExecutionCell = {
  cell_id: 'cell', cell_label: 'model=a', cell_number: 7, factor_values: { model: 'a' },
  replicate_count: 3, counts: { completed: 3 },
  usage: { total_tokens: 12500, cost_usd: 0.04, duration_seconds: 65, input_tokens: null, output_tokens: null, agent_run_count: null },
}

describe('Results Cells table', () => {
  it('shows completion and opens cell details from any part of the row', () => {
    const select = vi.fn()
    render(<ResultsCellsTable cells={[cell]} metric="total_tokens" attemptScope="latest" unit="replicate" onSelect={select} />)
    const table = within(screen.getByRole('table', { name: 'Cells' }))
    expect(table.getByRole('button', { name: 'Cell 7: model: a' })).toBeInTheDocument()
    expect(table.getByText('3/3')).toBeInTheDocument()
    expect(table.getByLabelText('Completed')).toBeInTheDocument()
    expect(table.getByText('12.5K')).toHaveAttribute('title', '12,500 tokens')
    fireEvent.click(table.getByText('12.5K'))
    expect(select).toHaveBeenCalledExactlyOnceWith(cell)
  })

  it('keeps incomplete cells unchecked and uses all-attempt values when selected', () => {
    render(<ResultsCellsTable cells={[{ ...cell, counts: { completed: 1 }, all_attempt_usage: { ...cell.usage!, total_tokens: 25000 } }]}
      metric="total_tokens" attemptScope="all" unit="replicate" onSelect={vi.fn()} />)
    expect(screen.getByText('1/3')).toBeInTheDocument()
    expect(screen.queryByLabelText('Completed')).not.toBeInTheDocument()
    expect(screen.getByText('25K')).toBeInTheDocument()
  })

  it('uses completed replicates rather than completed row counts in row mode', () => {
    render(<ResultsCellsTable cells={[{ ...cell, counts: { completed: 200 }, completed_replicate_count: 2 }]}
      metric="duration_seconds" attemptScope="latest" unit="row" onSelect={vi.fn()} />)
    expect(screen.getByText('2/3')).toBeInTheDocument()
    expect(screen.queryByLabelText('Completed')).not.toBeInTheDocument()
  })
})

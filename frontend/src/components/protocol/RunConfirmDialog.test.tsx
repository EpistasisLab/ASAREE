import { QueryClient } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RunConfirmDialog } from './RunConfirmDialog'

describe('RunConfirmDialog', () => {
  it('shows the published canvas version in the run summary', () => {
    render(
      <RunConfirmDialog
        scope={{ type: 'replicate', label: 'Replicate 1' }}
        nodes={[]}
        edges={[]}
        queryClient={new QueryClient()}
        publishedRevision={7}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    )

    expect(screen.getByText('Canvas version:')).toBeInTheDocument()
    expect(screen.getByText('Published v7')).toBeInTheDocument()
  })

  it('warns that a re-run will hit the same iteration limit', () => {
    render(
      <RunConfirmDialog
        scope={{ type: 'replicate', label: 'Replicate 1' }}
        nodes={[]}
        edges={[]}
        queryClient={new QueryClient()}
        truncationNotice="This replicate stopped at the iteration limit."
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    )

    expect(screen.getByText('Iteration limit reached last time')).toBeInTheDocument()
  })

  it('names the node whose cap ran out when the caller has the run', () => {
    const pattern = {
      id: 'pattern-1',
      type: 'pattern_reason_act',
      position: { x: 0, y: 0 },
      data: { label: 'Reason + Act', config: { max_iterations: 15, include_scratchpad: false } },
    }
    render(
      <RunConfirmDialog
        scope={{ type: 'graph' }}
        nodes={[pattern, { id: 'agent-1', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Analyst' } }]}
        edges={[{ id: 'e1', source: 'pattern-1', target: 'agent-1', targetHandle: 'architectural_pattern' }]}
        queryClient={new QueryClient()}
        truncatedCaps={new Map([['pattern-1', 15]])}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    )

    expect(screen.getByText(/The last run stopped at this iteration limit \(15\)/)).toBeInTheDocument()
  })

  it('drops that finding once the cap is above what failed', () => {
    const pattern = {
      id: 'pattern-1',
      type: 'pattern_reason_act',
      position: { x: 0, y: 0 },
      data: { label: 'Reason + Act', config: { max_iterations: 40, include_scratchpad: false } },
    }
    render(
      <RunConfirmDialog
        scope={{ type: 'graph' }}
        nodes={[pattern, { id: 'agent-1', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Analyst' } }]}
        edges={[{ id: 'e1', source: 'pattern-1', target: 'agent-1', targetHandle: 'architectural_pattern' }]}
        queryClient={new QueryClient()}
        truncatedCaps={new Map([['pattern-1', 15]])}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    )

    expect(screen.queryByText(/stopped at this iteration limit/)).not.toBeInTheDocument()
  })
})

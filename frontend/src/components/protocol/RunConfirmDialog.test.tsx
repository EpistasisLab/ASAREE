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
})

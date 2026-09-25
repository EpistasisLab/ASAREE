import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AddNodePanel } from './AddNodePanel'

describe('AddNodePanel', () => {
  it('groups node types by protocol role', () => {
    render(<AddNodePanel onAdd={vi.fn()} onClose={vi.fn()} />)

    expect(screen.getAllByRole('heading', { level: 3 }).map((heading) => heading.textContent)).toEqual([
      'Agents',
      'Models',
      'Execution patterns',
      'Knowledge & data',
      'Tools & output',
    ])
  })

  it('only shows categories containing search matches', () => {
    render(<AddNodePanel onAdd={vi.fn()} onClose={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText('Search node types…'), { target: { value: 'OpenAI' } })

    expect(screen.getByRole('heading', { name: 'Models' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Agents' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /OpenAI/ })).toBeInTheDocument()
  })

  it('shows where Sub-Agents are added and enables them in that connector picker', () => {
    const { rerender } = render(<AddNodePanel onAdd={vi.fn()} onClose={vi.fn()} />)

    expect(screen.getByRole('button', { name: /Sub-Agent/ })).toBeDisabled()
    expect(screen.getByText("Add from an Agent's Sub-Agents connector")).toBeInTheDocument()

    rerender(<AddNodePanel onAdd={vi.fn()} onClose={vi.fn()} allowedTypes={['sub_agent']} title="Add Sub-Agent" />)

    expect(screen.getByRole('button', { name: /Sub-Agent/ })).toBeEnabled()
  })
})

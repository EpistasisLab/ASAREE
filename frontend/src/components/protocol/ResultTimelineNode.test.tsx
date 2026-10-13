import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ResultTimelineNode } from './ResultTimelineNode'

describe('ResultTimelineNode', () => {
  it('shows bypassed even when the node had no upstream output', () => {
    render(<ResultTimelineNode node={{
      node_id: 'observer', node_label: 'Observer', status: 'completed', bypassed: true,
      output_text: null, error: null, agent_run_id: null,
      input_tokens: null, output_tokens: null, total_tokens: null, cost_usd: null,
    }} />)
    expect(screen.getByText('Bypassed')).toBeInTheDocument()
    expect(screen.queryByText('completed')).not.toBeInTheDocument()
  })
})

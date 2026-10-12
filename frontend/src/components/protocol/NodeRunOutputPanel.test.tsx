import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { NodeRunOutputPanel } from './NodeRunOutputPanel'

describe('NodeRunOutputPanel', () => {
  it('labels completed pass-through output as bypassed', () => {
    render(<NodeRunOutputPanel nodeRun={{ status: 'completed', bypassed: true, output_text: 'Upstream answer' }} />)
    expect(screen.getByText('Bypassed')).toBeInTheDocument()
    expect(screen.getByText('Upstream answer')).toBeInTheDocument()
    expect(screen.queryByText('Done')).not.toBeInTheDocument()
  })
})

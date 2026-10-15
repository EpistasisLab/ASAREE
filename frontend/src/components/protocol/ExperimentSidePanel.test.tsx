import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ExperimentSidePanel } from './ExperimentSidePanel'

vi.mock('./DesignTab', () => ({ DesignTab: () => null }))
vi.mock('./RunsTab', () => ({ RunsTab: () => null }))
vi.mock('./ResultsTab', () => ({ ResultsTab: () => null }))
vi.mock('./ExperimentVersionView', () => ({ ExperimentVersionDesign: () => null, ExperimentVersionRuns: () => null }))

function Navigation() {
  const location = useLocation()
  const navigate = useNavigate()
  return <><output aria-label="Current URL">{location.search}</output>
    <button onClick={() => navigate(-1)}>Back</button><button onClick={() => navigate(1)}>Forward</button></>
}

function mount(url: string) {
  return render(<MemoryRouter initialEntries={[url]}>
    <Navigation />
    <ExperimentSidePanel experiment={undefined} protocolId={undefined} protocol={undefined} canvasRef={{ current: null }}
      isLoading={false} needsInitialGeneration={false} regenerationRequired={false} unboundFactors={[]} onResultSelection={vi.fn()} />
  </MemoryRouter>)
}

describe('experiment panel URL navigation', () => {
  it.each([
    ['?tab=runs', 'Runs'], ['?tab=results', 'Results'], ['?tab=design', 'Design'], ['', 'Design'], ['?tab=invalid', 'Design'],
  ])('restores %s as the %s panel on mount', (query, panel) => {
    mount(`/experiments/experiment/protocol${query}`)
    expect(screen.getByRole('button', { name: `Open ${panel} panel` })).toHaveAttribute('aria-pressed', 'true')
  })

  it('preserves other query parameters and follows browser history', async () => {
    mount('/experiments/experiment/protocol?version=revision-1')
    fireEvent.click(screen.getByRole('button', { name: 'Open Runs panel' }))
    await waitFor(() => expect(screen.getByLabelText('Current URL')).toHaveTextContent('?version=revision-1&tab=runs'))
    fireEvent.click(screen.getByRole('button', { name: 'Open Results panel' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open Results panel' })).toHaveAttribute('aria-pressed', 'true'))
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open Runs panel' })).toHaveAttribute('aria-pressed', 'true'))
    fireEvent.click(screen.getByRole('button', { name: 'Forward' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open Results panel' })).toHaveAttribute('aria-pressed', 'true'))
  })
})

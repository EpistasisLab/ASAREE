import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { SamplingControls } from './SamplingControls'

vi.mock('./useSamplingPreview', () => ({
  useSamplingPreview: (_caps: unknown, _model: string, temperature?: number | null, effort?: string | null) => ({
    data: {
      temperature: temperature ?? null, effort: effort ?? 'none',
      temperature_source: temperature == null ? 'omitted' : 'explicit',
      effort_source: effort == null ? 'advertised_default' : 'explicit',
    },
  }),
}))

const capabilities = {
  supports_temperature: true, supports_effort: true,
  effort_levels: ['none', 'low', 'high'], default_effort: 'none',
}

it('offers both controls with defaults and preserves unset values until edited', () => {
  const onChange = vi.fn()
  render(<SamplingControls capabilities={capabilities} model="model" onChange={onChange} />)
  expect(screen.getByLabelText('Temperature')).toHaveValue(null)
  expect(screen.getByPlaceholderText('Provider chooses')).toBeInTheDocument()
  expect(screen.getByLabelText('Effort')).toHaveTextContent('Default (none)')
  expect(screen.getByText('Provider chooses')).toBeInTheDocument()
  expect(screen.getByText('Provider default')).toBeInTheDocument()
  expect(onChange).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('Temperature'), { target: { value: '0' } })
  expect(onChange).toHaveBeenCalledWith({ temperature: 0 })
})

it('preserves an explicit temperature and allows returning to the default', () => {
  const onChange = vi.fn()
  render(<SamplingControls capabilities={capabilities} model="model" temperature={0.7} onChange={onChange} />)
  expect(screen.getByLabelText('Temperature')).toHaveValue(0.7)
  fireEvent.click(screen.getByRole('button', { name: 'Use default temperature' }))
  expect(onChange).toHaveBeenCalledWith({ temperature: null })
})

it('allows explicit entry when support is unknown and hides unsupported unset controls', () => {
  render(<SamplingControls capabilities={{ supports_temperature: null, supports_effort: false, effort_levels: null }} model="model" onChange={vi.fn()} />)
  expect(screen.getByLabelText('Temperature')).toBeInTheDocument()
  expect(screen.queryByLabelText('Effort')).not.toBeInTheDocument()
})

it('lets users clear a saved effort that their model does not support', () => {
  const onChange = vi.fn()
  render(<SamplingControls capabilities={{ supports_temperature: true, supports_effort: false, effort_levels: [] }}
    model="model" effort="high" onChange={onChange} />)
  fireEvent.click(screen.getByRole('button', { name: 'Clear unsupported effort' }))
  expect(onChange).toHaveBeenCalledWith({ effort: null })
})

it('keeps an existing Haiku effort editable with unknown metadata', () => {
  const onChange = vi.fn()
  render(<SamplingControls capabilities={{ supports_temperature: null, supports_effort: null, effort_levels: null }}
    model="claude-haiku-5-5" effort="medium" onChange={onChange} />)
  expect(screen.getByLabelText('Effort')).toHaveValue('medium')
  fireEvent.change(screen.getByLabelText('Effort'), { target: { value: 'future-effort' } })
  expect(onChange).toHaveBeenCalledWith({ effort: 'future-effort' })
  expect(onChange).not.toHaveBeenCalledWith({ effort: 'low' })
})

it('offers partial effort suggestions without known unsupported levels', () => {
  render(<SamplingControls capabilities={{ supports_temperature: false, supports_effort: true,
    effort_levels: ['medium', 'future-effort'], effort_levels_complete: false, unsupported_effort_levels: ['excluded'] }}
    model="future-model" onChange={vi.fn()} />)
  expect(screen.getByLabelText('Effort').tagName).toBe('INPUT')
  const suggestions = screen.getAllByRole('option', { hidden: true }).map((option) => option.getAttribute('value'))
  expect(suggestions).toContain('future-effort')
  expect(suggestions).not.toContain('excluded')
})

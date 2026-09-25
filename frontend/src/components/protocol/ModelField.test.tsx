import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ModelField } from './ModelField'

describe('ModelField', () => {
  it('prompts for a model when no default is selected', () => {
    render(
      <ModelField
        value=""
        models={[{
          id: 'available-model',
          label: 'Available Model',
          supports_temperature: true,
          supports_effort: false,
          effort_levels: [],
          supports_tool_calling: true,
        }]}
        isLoading={false}
        onChange={vi.fn()}
      />,
    )

    expect(screen.getByRole('combobox')).toHaveTextContent('Select a model…')
  })
})

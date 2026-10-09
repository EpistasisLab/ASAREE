import { describe, expect, it } from 'vitest'
import { modelCapabilities } from './modelCapabilities'

describe('modelCapabilities', () => {
  it('does not guess sampling controls before backend metadata resolves', () => {
    expect(modelCapabilities()).toMatchObject({
      supports_temperature: false, supports_effort: false, default_effort: null,
    })
  })

  it('honors the discovered effort ladder and default', () => {
    expect(modelCapabilities({
      supports_temperature: false, supports_effort: true,
      effort_levels: ['low', 'high'], default_effort: 'high',
    })).toMatchObject({ effort_levels: ['low', 'high'], default_effort: 'high' })
  })

  it('keeps explicit temperature-only overrides', () => {
    expect(modelCapabilities(undefined, {
      supports_temperature: true, supports_effort: false, effort_levels: [], default_effort: null,
    })).toMatchObject({ supports_temperature: true, supports_effort: false, default_effort: null })
  })
})

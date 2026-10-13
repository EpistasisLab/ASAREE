import { afterEach, expect, it, vi } from 'vitest'
import { newRequestKey } from './requestKey'

afterEach(() => vi.unstubAllGlobals())

it('works without crypto.randomUUID, which plain-HTTP origins lack', () => {
  vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) })
  const keys = new Set(Array.from({ length: 1000 }, newRequestKey))
  expect(keys.size).toBe(1000)
  for (const key of keys) expect(key).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
})

import { afterEach, expect, it, vi } from 'vitest'
import { protocolsApi } from './client'

afterEach(() => vi.unstubAllGlobals())

it.each(['publish', 'updateRevision'] as const)('sends %s annotations as a JSON object accepted by the API', async operation => {
  const annotations = { name: 'Baseline', note: 'Keep for comparison' }
  const fetch = vi.fn(async (_url: string, options: RequestInit) => {
    const body: unknown = JSON.parse(options.body as string)
    // FastAPI's VersionAnnotationsRequest rejects a JSON string with 422.
    if (typeof body !== 'object' || body === null) {
      return new Response(JSON.stringify({ detail: [{ msg: 'Input should be a valid dictionary' }] }), { status: 422 })
    }
    return new Response(JSON.stringify({ id: 'p', ...body }), { status: 200 })
  })
  vi.stubGlobal('fetch', fetch)
  const result = operation === 'publish'
    ? protocolsApi.publish('p', annotations)
    : protocolsApi.updateRevision('p', 'v1', annotations)
  await expect(result).resolves.toMatchObject({ id: 'p', ...annotations })
  expect(JSON.parse(fetch.mock.calls[0][1].body as string)).toEqual(annotations)
})

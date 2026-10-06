import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { datasetsApi, experimentsApi, protocolsApi } from './client'

const fetchMock = vi.fn()
beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockImplementation(async () => new Response(JSON.stringify({ dataset_row: null, row_result_id: null }), { status: 200 }))
})
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks() })
const lastBody = () => {
  const body = fetchMock.mock.calls.at(-1)?.[1]?.body
  return body === undefined ? undefined : JSON.parse(body)
}
describe('row HTTP contracts', () => {
  it('retains omitted legacy bodies and explicit null', async () => {
    await protocolsApi.testRun('p')
    expect(lastBody()).toBeUndefined()
    await protocolsApi.runNode('p', 'a')
    expect(lastBody()).toBeUndefined()
    await protocolsApi.testRun('p', { row_index: null })
    expect(lastBody()).toEqual({ row_index: null })
  })
  it('sends zero-based selections on each execution interface', async () => {
    await protocolsApi.testRun('p', { row_index: 2 })
    expect(lastBody()).toEqual({ row_index: 2 })
    await protocolsApi.runNode('p', 'a', { row_index: 2 })
    expect(lastBody()).toEqual({ row_index: 2 })
    await protocolsApi.run('p', null, { row_index: 2 })
    expect(lastBody()).toEqual({ replicate_label: null, row_index: 2 })
    const graph = { nodes: [], edges: [] }
    await protocolsApi.promptPreview('p', 'a', graph, { row_index: 2 })
    expect(lastBody()).toEqual({ graph, row_index: 2 })
    expect(() => protocolsApi.testRun('p', { row_index: -1 })).toThrow(RangeError)
  })
  it('retries exactly one row without a replicate rerun payload', async () => {
    await protocolsApi.runCells('p', { retry_row_result_ids: ['slot'] })
    expect(lastBody()).toEqual({ retry_row_result_ids: ['slot'] })
  })
  it('uses identical encoded scope on projection and exports', async () => {
    const scope = { protocol_id: 'p /', design_revision_id: 'd', protocol_revision_id: 'r' }
    await experimentsApi.getRunResults('e', scope)
    const query = fetchMock.mock.calls.at(-1)?.[0].split('?')[1]
    await experimentsApi.downloadRunResultsCsv('e', scope)
    expect(fetchMock.mock.calls.at(-1)?.[0].split('?')[1]).toBe(query)
    await experimentsApi.getRunResultsSchema('e', scope)
    expect(fetchMock.mock.calls.at(-1)?.[0].split('?')[1]).toBe(query)
  })
  it('fetches row metadata and preserves returned identities', async () => {
    const schema = { dataset_id: 'id', raw_sha256: 'hash', columns: ['question'], row_count: 3 }
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(schema)))
    expect(await datasetsApi.getRowSchema('id')).toEqual(schema)
    expect(fetchMock.mock.calls.at(-1)?.[0]).toContain('/datasets/id/row-schema')
    expect(await protocolsApi.testRun('p')).toMatchObject({ dataset_row: null, row_result_id: null })
  })
})

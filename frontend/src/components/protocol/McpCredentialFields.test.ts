import { describe, expect, it } from 'vitest'
import { credentialsAreValid, credentialsRecord, type CredentialEntry } from '@/lib/mcpCredentials'

const entries = (...values: [string, string][]): CredentialEntry[] =>
  values.map(([name, value], id) => ({ id, name, value }))

describe('MCP credential fields', () => {
  it('builds a replacement map without trimming secret values', () => {
    expect(credentialsRecord(entries([' X-API-Key ', ' secret '], ['X-Tenant', 'lab']))).toEqual({
      'X-API-Key': ' secret ',
      'X-Tenant': 'lab',
    })
  })

  it('requires complete, case-insensitively unique names', () => {
    expect(credentialsAreValid(entries(['X-API-Key', 'secret']))).toBe(true)
    expect(credentialsAreValid(entries(['X-API-Key', 'one'], ['x-api-key', 'two']))).toBe(false)
    expect(credentialsAreValid(entries(['API_TOKEN', '']))).toBe(false)
  })
})

export type CredentialEntry = { id: number; name: string; value: string }

let nextEntryId = 1

export function emptyCredentialEntry(): CredentialEntry {
  return { id: nextEntryId++, name: '', value: '' }
}

export function credentialsRecord(entries: CredentialEntry[]): Record<string, string> {
  return Object.fromEntries(entries.map((entry) => [entry.name.trim(), entry.value]))
}

export function credentialsAreValid(entries: CredentialEntry[]): boolean {
  if (entries.length === 0 || entries.some((entry) => !entry.name.trim() || !entry.value)) return false
  const names = entries.map((entry) => entry.name.trim().toLowerCase())
  return new Set(names).size === names.length
}

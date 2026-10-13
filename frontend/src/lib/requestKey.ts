// Idempotency key for result-job commands (preview, cancel, export). Same
// secure-context problem as newNodeId in nodeId.ts: crypto.randomUUID() is
// undefined over plain HTTP via a LAN IP or hostname, which crashed the Runs
// tab there. crypto.getRandomValues() has no such restriction, and a repeated
// key would make the server return someone's earlier job, so this stays fully
// random rather than time-based.
export function newRequestKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40 // RFC 4122 version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80 // RFC 4122 variant
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

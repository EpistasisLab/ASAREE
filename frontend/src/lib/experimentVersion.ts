import type { ProtocolRevision } from '@/types/protocols'

export function versionLabel(version: ProtocolRevision) {
  return `Version ${version.revision}${version.name ? ` · ${version.name}` : ''}`
}

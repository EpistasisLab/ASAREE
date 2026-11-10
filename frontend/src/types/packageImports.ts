export interface PackageImport {
  id: string
  status: string
  sha256: string | null
  source_url?: string
  experiment_id: string | null
  configured_at: string | null
  created_at: string
  expires_at: string
  error: string | null
  progress: { phase?: string; bytes?: number }
  preflight: {
    manifest?: {
      schema_version: number
      experiment_id: string
      publication: { id: string; version: number; published_at: string; name?: string }
      results_included: boolean
    }
    categories?: Record<string, number>
    unresolved_count?: number
    external_requirements?: number
    warnings?: string[]
    compatibility?: string[]
    diagnostics?: { kind: string; id: string; status: string; reason?: string }[]
  }
}

export interface PackageFile {
  path: string
  sha256: string
  bytes: number
  provenance: string
  source_id: string
}

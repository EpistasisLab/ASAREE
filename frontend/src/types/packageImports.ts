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
  progress: { phase?: string; bytes?: number; scanned?: number }
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

export interface ImportReadiness {
  ready: boolean
  issues: { node_id?: string; message: string }[]
  mcp: { source_id: string; name: string; selected_id: string | null; required_tools?: string[]; candidates: { id: string; name: string }[] }[]
  providers: { provider: string; id: string; selected: boolean }[]
}

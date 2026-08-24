import type {
  ApiErrorDetail,
  LoginRequest,
  PasswordChangeRequest,
  RegisterRequest,
  TokenCreateRequest,
  TokenCreateResponse,
  TokenListResponse,
  TokenResponse,
  User,
  UserUpdate,
} from '@/types/auth'
import type { Agent } from '@/types/agents'
import type { Dataset } from '@/types/datasets'
import type { Cell, DesignSpec, Experiment, ExperimentResults, Trial } from '@/types/experiments'
import type { LLMConnectionCheck, LLMProvider, LLMSetting, LLMSettingModelsResponse } from '@/types/llmSettings'
import type { McpServer } from '@/types/mcpServers'
import type { OkfBrowseResponse, OkfBundle } from '@/types/okf'
import type { CellRunBatch, Protocol, ProtocolGraph, ProtocolRun } from '@/types/protocols'
import type { Run, RunStep } from '@/types/runs'
import type { Skill, SkillListResponse } from '@/types/skills'

const ACCESS_TOKEN_KEY = 'asaree_access_token'

export function getStoredAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY)
}

export function setStoredAccessToken(token: string | null): void {
  if (token) localStorage.setItem(ACCESS_TOKEN_KEY, token)
  else localStorage.removeItem(ACCESS_TOKEN_KEY)
}

/** Thrown on any non-2xx response. `detail` is ASAREE's raw `detail` field —
 * either a plain string or the richer `{message, code, ...}` shape the auth
 * endpoints use for rate-limiting/invalid-credentials/etc. */
export class ApiError extends Error {
  status: number
  detail: string | ApiErrorDetail

  constructor(status: number, detail: string | ApiErrorDetail) {
    super(typeof detail === 'string' ? detail : detail.message)
    this.status = status
    this.detail = detail
  }

  get code(): string | undefined {
    return typeof this.detail === 'object' ? this.detail.code : undefined
  }

  get retryAfterSeconds(): number | undefined {
    return typeof this.detail === 'object' ? this.detail.retry_after_seconds : undefined
  }
}

let isRefreshing = false
let refreshPromise: Promise<boolean> | null = null

/** Exchanges the httpOnly refresh cookie for a new access token. De-duplicated
 * so concurrent 401s don't each fire their own refresh. */
async function tryRefreshToken(): Promise<boolean> {
  if (isRefreshing && refreshPromise) return refreshPromise

  isRefreshing = true
  refreshPromise = (async () => {
    try {
      const res = await fetch('/api/auth/refresh', {
        method: 'POST',
        credentials: 'include',
      })
      if (!res.ok) {
        setStoredAccessToken(null)
        return false
      }
      const data = (await res.json()) as TokenResponse
      setStoredAccessToken(data.access_token)
      return true
    } catch {
      return false
    } finally {
      isRefreshing = false
    }
  })()
  return refreshPromise
}

interface RequestOptions extends Omit<RequestInit, 'body'> {
  /** A plain object is JSON-encoded (the common case); pass a `FormData`
   * directly (e.g. datasetsApi.create's multipart upload) to send it
   * as-is -- fetch sets its own `multipart/form-data; boundary=...`
   * Content-Type for a FormData body, which a hardcoded `application/json`
   * header here would otherwise stomp. */
  body?: unknown
  /** Skip the silent-refresh-and-retry dance — used by the refresh call
   * itself, so a failing refresh can't recurse into refreshing again. */
  skipAuthRetry?: boolean
}

/** The shared fetch/auth/retry/error-mapping dance -- `request` (JSON) and
 * `requestBlob` (a file download) both build on this, differing only in how
 * they read the (already ok) response body. */
async function authedFetch(path: string, options: RequestOptions = {}): Promise<Response> {
  const { body, skipAuthRetry, headers, ...rest } = options
  const token = getStoredAccessToken()
  const isFormData = body instanceof FormData

  const doFetch = () =>
    fetch(`/api${path}`, {
      ...rest,
      credentials: 'include',
      headers: {
        ...(body !== undefined && !isFormData ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...headers,
      },
      body: isFormData ? (body as FormData) : body !== undefined ? JSON.stringify(body) : undefined,
    })

  let res = await doFetch()

  if (res.status === 401 && token && !skipAuthRetry) {
    const refreshed = await tryRefreshToken()
    if (refreshed) {
      res = await doFetch()
    }
  }

  if (!res.ok) {
    let detail: string | ApiErrorDetail = res.statusText
    try {
      const data = await res.json()
      if (data?.detail !== undefined) detail = data.detail
    } catch {
      // No JSON body (e.g. a 204-adjacent error, or a network-layer failure) — keep statusText.
    }
    throw new ApiError(res.status, detail)
  }

  return res
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const res = await authedFetch(path, options)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** Same auth/retry/error handling as `request`, but for a non-JSON download
 * (e.g. a CSV export) -- returns the raw `Blob` for the caller to hand to
 * `URL.createObjectURL`. */
async function requestBlob(path: string, options: RequestOptions = {}): Promise<Blob> {
  const res = await authedFetch(path, options)
  return res.blob()
}

export const authApi = {
  register: (data: RegisterRequest) => request<User>('/auth/register', { method: 'POST', body: data }),
  login: (data: LoginRequest) => request<TokenResponse>('/auth/login', { method: 'POST', body: data }),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  getMe: () => request<User>('/auth/me'),
  updateMe: (data: UserUpdate) => request<User>('/auth/me', { method: 'PATCH', body: data }),
  changePassword: (data: PasswordChangeRequest) => request<void>('/auth/me/password', { method: 'POST', body: data }),
}

export const tokenApi = {
  list: (offset = 0, limit = 20) => request<TokenListResponse>(`/auth/me/tokens?offset=${offset}&limit=${limit}`),
  create: (data: TokenCreateRequest) => request<TokenCreateResponse>('/auth/me/tokens', { method: 'POST', body: data }),
  revoke: (id: string) => request<void>(`/auth/me/tokens/${id}`, { method: 'DELETE' }),
}

export const experimentsApi = {
  list: (opts?: { includeArchived?: boolean }) =>
    request<Experiment[]>(opts?.includeArchived ? '/experiments?include_archived=true' : '/experiments'),
  get: (id: string) => request<Experiment>(`/experiments/${id}`),
  // Omit `name` and the server allocates the next free "Untitled Experiment N"
  // atomically -- the one-click create in AppHeader relies on that, since a
  // name this client picks from a GET is a guess that another session (or an
  // archived experiment it can't see) can invalidate before the POST lands.
  create: (data: { name?: string; description?: string | null }) =>
    request<Experiment>('/experiments', { method: 'POST', body: data }),
  update: (
    id: string,
    data: {
      name?: string
      description?: string | null
      hypothesis?: string | null
      design_spec?: DesignSpec | null
      // A timestamp to archive, null to unarchive -- canvas menu's Archive/Unarchive action.
      archived_at?: string | null
      // The experiment's whole attached-dataset list, in canvas wiring order
      // -- a full replacement, not a merge ([] detaches everything). Sent
      // whenever the canvas's set of Dataset nodes changes (ProtocolCanvas's
      // syncExperimentDatasets effect), so the nodes' own configs and the
      // experiment_datasets rows can't drift apart.
      dataset_ids?: string[]
      // The one-dataset shorthand, kept for callers written before the
      // connector was uncapped -- the server turns it into a single-element
      // dataset_ids (or [] for null). Prefer dataset_ids.
      dataset_id?: string | null
    },
  ) => request<Experiment>(`/experiments/${id}`, { method: 'PATCH', body: data }),
  remove: (id: string) => request<void>(`/experiments/${id}`, { method: 'DELETE' }),
  listCells: (id: string) => request<Cell[]>(`/experiments/${id}/cells`),
  // One row per cell, one column per factor_values/metric_values key seen
  // anywhere in the experiment (see services.csv_export.cells_to_csv) --
  // a Blob, not JSON, so callers hand it straight to URL.createObjectURL.
  downloadCellsCsv: (id: string) => requestBlob(`/experiments/${id}/cells.csv`),
  // Materializes one FactorialCellResult per combination of the experiment's
  // declared factors -- safe to call again after widening a factor's levels,
  // existing cells are untouched (see services.design_generation).
  generateDesign: (id: string) => request<Cell[]>(`/experiments/${id}/generate-design`, { method: 'POST' }),
  // One row per cell (a "trial"), not per ProtocolRun -- a cell that's never
  // been run is still listed, with status "queued" (see TrialResponse /
  // services.protocol_runs.list_experiment_trials).
  listTrials: (id: string) => request<Trial[]>(`/experiments/${id}/runs`),
  // Derives analyze_factorial's own condition_factors/positive_levels/
  // reference_condition/primary_metric from this experiment's Design tab
  // declarations -- no request body needed (see
  // services.factorial_analysis.analyze_experiment_design).
  getResults: (id: string) => request<ExperimentResults>(`/experiments/${id}/results`),
}

export const protocolsApi = {
  create: (data: { name: string; description?: string | null; experiment_id?: string | null; graph?: ProtocolGraph }) =>
    request<Protocol>('/protocols', { method: 'POST', body: data }),
  get: (id: string) => request<Protocol>(`/protocols/${id}`),
  list: (experimentId?: string) =>
    request<Protocol[]>(experimentId ? `/protocols?experiment_id=${experimentId}` : '/protocols'),
  update: (id: string, data: { name?: string; description?: string | null; graph?: ProtocolGraph }) =>
    request<Protocol>(`/protocols/${id}`, { method: 'PATCH', body: data }),
  remove: (id: string) => request<void>(`/protocols/${id}`, { method: 'DELETE' }),
  // 422 if the graph is empty or has a cycle -- returns immediately with
  // status "pending"; poll getRun for progress. cellLabel runs that one
  // already-generated cell for real (its own factor_values substituted in)
  // instead of today's ad-hoc, un-substituted whole-graph run.
  run: (id: string, cellLabel?: string | null) =>
    request<ProtocolRun>(`/protocols/${id}/runs`, { method: 'POST', body: { cell_label: cellLabel ?? null } }),
  // The per-node Play icon -- 422 if the node has upstream input or isn't a
  // runnable Agent (see validate_single_node_runnable). Same polling shape
  // as a plain run (getRun), just with node_runs carrying only this one key.
  runNode: (id: string, nodeId: string) => request<ProtocolRun>(`/protocols/${id}/nodes/${nodeId}/run`, { method: 'POST' }),
  getRun: (id: string, runId: string) => request<ProtocolRun>(`/protocols/${id}/runs/${runId}`),
  // Only raises cancel_requested_at -- a no-op (200, unchanged row) once the
  // run is already terminal. run_protocol's own node loop is what actually
  // honors it (between nodes, not mid-node) and flips status to "cancelled".
  cancelRun: (id: string, runId: string) => request<ProtocolRun>(`/protocols/${id}/runs/${runId}/cancel`, { method: 'POST' }),
  listRuns: (id: string) => request<ProtocolRun[]>(`/protocols/${id}/runs`),
  // "Run all cells" -- 422 if there's no linked experiment or the graph
  // doesn't have exactly one final node; fans out one ProtocolRun per
  // not-yet-scored FactorialCellResult, each polled via listRuns.
  runCells: (id: string) => request<CellRunBatch>(`/protocols/${id}/cell-runs`, { method: 'POST' }),
}

export const datasetsApi = {
  // Owner-scoped, same convention as mcpServersApi.list -- backs the canvas's
  // dataset browser (DatasetBrowserPanel) and the Dataset node inspector's
  // read-out of whichever dataset the node is bound to.
  list: () => request<Dataset[]>('/datasets'),
  get: (id: string) => request<Dataset>(`/datasets/${id}`),
  // POST /datasets is a multipart upload -- stores ONLY the raw file,
  // verbatim (services.datasets.create_dataset); it never splits it.
  // 409s if `name` is already taken. Splitting is one of the two separate
  // actions below, once the dataset exists.
  create: (data: { name: string; file: File; targetColumn?: string; description?: string; dictionaryJson?: string }) => {
    const form = new FormData()
    form.set('name', data.name)
    form.set('file', data.file)
    if (data.targetColumn) form.set('target_column', data.targetColumn)
    if (data.description) form.set('description', data.description)
    if (data.dictionaryJson) form.set('dictionary_json', data.dictionaryJson)
    return request<Dataset>('/datasets', { method: 'POST', body: form })
  },
  // ASAREE's own built-in split (group-aware GroupShuffleSplit when
  // groupColumn is given and present, else stratified train_test_split on
  // targetColumn) -- covers the common case. Safe to call again (e.g. a
  // different seed): overwrites whichever split currently exists rather
  // than accumulating one per call.
  quickSplit: (id: string, data: { targetColumn?: string; groupColumn?: string; testSize?: number; seed?: number }) => {
    const form = new FormData()
    if (data.targetColumn) form.set('target_column', data.targetColumn)
    if (data.groupColumn) form.set('group_column', data.groupColumn)
    if (data.testSize != null) form.set('test_size', String(data.testSize))
    if (data.seed != null) form.set('seed', String(data.seed))
    return request<Dataset>(`/datasets/${id}/split/quick`, { method: 'POST', body: form })
  },
  // Registers an already-split train/test pair computed however the user
  // needed (k-fold, time-based, a custom cohort rule, ...) -- ASAREE only
  // validates that both parse as tabular data, the same "bring your own
  // code" precedent the Script node already established for scoring.
  manualSplit: (id: string, data: { trainFile: File; testFile: File }) => {
    const form = new FormData()
    form.set('train_file', data.trainFile)
    form.set('test_file', data.testFile)
    return request<Dataset>(`/datasets/${id}/split/manual`, { method: 'POST', body: form })
  },
  // Drops the row AND the uploaded files (services.datasets.delete_dataset) --
  // irreversible, unlike okfApi.remove, which only forgets a registration.
  // Offered from DatasetBrowserPanel, the one place the whole library is
  // listed.
  remove: (id: string) => request<void>(`/datasets/${id}`, { method: 'DELETE' }),
}

export const agentsApi = {
  list: () => request<Agent[]>('/agents'),
}

export const runsApi = {
  // No server-side experiment_id filter exists yet (runs.py only filters by
  // agent_id) -- callers filter client-side on run_metadata.experiment_id.
  list: () => request<Run[]>('/runs'),
  getSteps: (runId: string) => request<RunStep[]>(`/runs/${runId}/steps`),
}

export const mcpServersApi = {
  // Only the caller's own registered servers -- matches GET /mcp-servers'
  // existing scope (system servers like asaree-workspace aren't listed here
  // either; not something the MCP Tool node picker widens).
  list: () => request<McpServer[]>('/mcp-servers'),
  // Registers a connection the user typed in themselves -- backs the MCP
  // Client Tool node (ConnectMcpServerDialog). The response already carries
  // the discovered tools: core connects and lists them synchronously during
  // registration, so a 201 whose `status` is 'error' means "row saved, server
  // unreachable", not a failure to save. 409 on a duplicate `name`, 422 when
  // the stdio allowlist or the SSRF guard rejects it.
  create: (data: { name: string; transport: string; command?: string | null; url?: string | null; headers?: Record<string, string> | null }) =>
    request<McpServer>('/mcp-servers', { method: 'POST', body: data }),
  // Re-dials and re-discovers tools. The repair path for a server registered
  // while it happened to be down.
  reconnect: (id: string) => request<McpServer>(`/mcp-servers/${id}/reconnect`, { method: 'POST' }),
  remove: (id: string) => request<void>(`/mcp-servers/${id}`, { method: 'DELETE' }),
}

export const skillsApi = {
  // The caller's own skills plus any global system skill -- GET /skills
  // returns {items,total}, unwrapped here so callers get a plain array like
  // datasetsApi.list()/mcpServersApi.list() do.
  list: () => request<SkillListResponse>('/skills').then((r) => r.items),
  get: (id: string) => request<Skill>(`/skills/${id}`),
  // A skill is registered by uploading its .md file, not by filling in a
  // form: the file IS the skill (see SkillNodeData in types/protocols.ts),
  // and its frontmatter already carries the name/description. `name`/
  // `description` are overrides for a file whose frontmatter is missing or
  // wrong -- omit them for the normal path.
  create: (data: { file: File; name?: string; description?: string }) => {
    const form = new FormData()
    form.set('file', data.file)
    if (data.name) form.set('name', data.name)
    if (data.description) form.set('description', data.description)
    return request<Skill>('/skills/upload', { method: 'POST', body: form })
  },
  // The stored skill rendered back out as a SKILL.md document, so what a
  // user uploaded is also what they can read back and re-upload.
  markdown: (id: string) => request<{ markdown: string }>(`/skills/${id}/markdown`),
  update: (id: string, data: { name?: string; description?: string; body?: string }) =>
    request<Skill>(`/skills/${id}`, { method: 'PATCH', body: data }),
  replaceFromFile: (id: string, file: File) => {
    const form = new FormData()
    form.set('file', file)
    return request<Skill>(`/skills/${id}/markdown`, { method: 'PUT', body: form })
  },
  // Soft-deletes server-side: an agent still holding this id keeps running,
  // just without the skill (Motoro's resolve_skills skips and logs it).
  remove: (id: string) => request<void>(`/skills/${id}`, { method: 'DELETE' }),
}

export const okfApi = {
  // Browse the SERVER's disk, jailed to its configured bundle root. There is
  // no client-machine file access anywhere in this feature -- on the local
  // single-machine install this is built for, the server's disk IS the user's.
  browse: (path = '') => request<OkfBrowseResponse>(`/okf/browse?path=${encodeURIComponent(path)}`),
  list: () => request<OkfBundle[]>('/okf/bundles'),
  // Spawns an OKF MCP server jailed to `path` and persists the registration,
  // so a bad path fails here rather than mid-run. Idempotent per (user, path).
  create: (path: string) => request<OkfBundle>('/okf/bundles', { method: 'POST', body: { path } }),
  // Re-discover the bundle server's tools, and clear a stale connection error.
  refresh: (id: string) => request<OkfBundle>(`/okf/bundles/${id}/refresh`, { method: 'POST' }),
  // Forgets the registration only -- never touches the directory itself.
  remove: (id: string) => request<void>(`/okf/bundles/${id}`, { method: 'DELETE' }),
  // The bundle server's own list_concepts output, verbatim, for the inspector's
  // preview -- what's in there is the server's answer, not one reconstructed
  // from a directory listing.
  concepts: (id: string) => request<{ is_error: boolean; content: string }>(`/okf/bundles/${id}/concepts`),
}

export const llmSettingsApi = {
  list: () => request<LLMSetting[]>('/llm-settings'),
  // PUT, not POST: one row per (user, provider) -- a second call for the
  // same provider replaces it, it doesn't create a second credential.
  upsert: (data: { provider: LLMProvider; api_key: string; api_base?: string | null; azure_project_endpoint?: string | null }) =>
    request<LLMSetting>('/llm-settings', { method: 'PUT', body: data }),
  remove: (provider: LLMProvider) => request<void>(`/llm-settings/${provider}`, { method: 'DELETE' }),
  // `provider` is a plain string, not LLMProvider -- unlike credential
  // storage (scoped to azure_foundry only in the UI today), model listing
  // works for anthropic/openai too, and answers even with no credential
  // saved (see llm_model_discovery.py's static catalog fallback).
  listModels: (provider: string) => request<LLMSettingModelsResponse>(`/llm-settings/${provider}/models`),
  // Zero-token liveness check against the stored credential -- a free
  // authenticated GET per provider, never an inference call. On demand only
  // (a button, not an on-render fetch): it's free in tokens but it's still
  // one outbound request per provider against a rate-limited endpoint.
  // 404s when no credential is saved for the provider.
  testConnection: (provider: LLMProvider) => request<LLMConnectionCheck>(`/llm-settings/${provider}/connection`),
}

export { tryRefreshToken }

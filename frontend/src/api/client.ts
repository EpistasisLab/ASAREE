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
import type { Cell, DesignSpec, Experiment } from '@/types/experiments'
import type { LLMProvider, LLMSetting } from '@/types/llmSettings'
import type { McpServer } from '@/types/mcpServers'
import type { CellRunBatch, Protocol, ProtocolGraph, ProtocolRun } from '@/types/protocols'
import type { Run } from '@/types/runs'

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
  body?: unknown
  /** Skip the silent-refresh-and-retry dance — used by the refresh call
   * itself, so a failing refresh can't recurse into refreshing again. */
  skipAuthRetry?: boolean
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, skipAuthRetry, headers, ...rest } = options
  const token = getStoredAccessToken()

  const doFetch = () =>
    fetch(`/api${path}`, {
      ...rest,
      credentials: 'include',
      headers: {
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
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

  if (res.status === 204) return undefined as T
  return (await res.json()) as T
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
  create: (data: { name: string; description?: string | null }) =>
    request<Experiment>('/experiments', { method: 'POST', body: data }),
  update: (id: string, data: { name?: string; description?: string | null; design_spec?: DesignSpec | null }) =>
    request<Experiment>(`/experiments/${id}`, { method: 'PATCH', body: data }),
  listCells: (id: string) => request<Cell[]>(`/experiments/${id}/cells`),
  // Materializes one FactorialCellResult per combination of the experiment's
  // declared factors -- safe to call again after widening a factor's levels,
  // existing cells are untouched (see services.design_generation).
  generateDesign: (id: string) => request<Cell[]>(`/experiments/${id}/generate-design`, { method: 'POST' }),
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
  // status "pending"; poll getRun for progress.
  run: (id: string) => request<ProtocolRun>(`/protocols/${id}/runs`, { method: 'POST' }),
  getRun: (id: string, runId: string) => request<ProtocolRun>(`/protocols/${id}/runs/${runId}`),
  listRuns: (id: string) => request<ProtocolRun[]>(`/protocols/${id}/runs`),
  // "Run all cells" -- 422 if there's no linked experiment or the graph
  // doesn't have exactly one final node; fans out one ProtocolRun per
  // not-yet-scored FactorialCellResult, each polled via listRuns.
  runCells: (id: string) => request<CellRunBatch>(`/protocols/${id}/cell-runs`, { method: 'POST' }),
}

export const datasetsApi = {
  get: (id: string) => request<Dataset>(`/datasets/${id}`),
}

export const agentsApi = {
  list: () => request<Agent[]>('/agents'),
}

export const runsApi = {
  // No server-side experiment_id filter exists yet (runs.py only filters by
  // agent_id) -- callers filter client-side on run_metadata.experiment_id.
  list: () => request<Run[]>('/runs'),
}

export const mcpServersApi = {
  // Only the caller's own registered servers -- matches GET /mcp-servers'
  // existing scope (system servers like asaree-workspace aren't listed here
  // either; not something the MCP Tool node picker widens).
  list: () => request<McpServer[]>('/mcp-servers'),
}

export const llmSettingsApi = {
  list: () => request<LLMSetting[]>('/llm-settings'),
  // PUT, not POST: one row per (user, provider) -- a second call for the
  // same provider replaces it, it doesn't create a second credential.
  upsert: (data: { provider: LLMProvider; api_key: string; api_base?: string | null }) =>
    request<LLMSetting>('/llm-settings', { method: 'PUT', body: data }),
}

export { tryRefreshToken }

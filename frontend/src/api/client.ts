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
import type { Cell, Experiment } from '@/types/experiments'
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
  list: () => request<Experiment[]>('/experiments'),
  get: (id: string) => request<Experiment>(`/experiments/${id}`),
  listCells: (id: string) => request<Cell[]>(`/experiments/${id}/cells`),
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

export { tryRefreshToken }

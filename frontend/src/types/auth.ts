export interface User {
  id: string
  email: string
  display_name: string
  is_active: boolean
  is_admin: boolean
  created_at: string
}

export interface LoginRequest {
  email: string
  password: string
}

export interface RegisterRequest {
  email: string
  password: string
  display_name: string
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: User
}

export interface UserUpdate {
  display_name?: string
  email?: string
}

export interface PasswordChangeRequest {
  current_password: string
  new_password: string
}

export interface TokenCreateRequest {
  name: string
  expires_in_days?: number | null
}

export interface TokenCreateResponse {
  id: string
  name: string
  token: string
  token_prefix: string | null
  expires_at: string | null
  created_at: string
}

export interface TokenListItem {
  id: string
  name: string
  token_prefix: string | null
  last_used_at: string | null
  expires_at: string | null
  is_revoked: boolean
  created_at: string
}

export interface TokenListResponse {
  items: TokenListItem[]
  total: number
  offset: number
  limit: number
}

/** ASAREE's structured error shape for auth endpoints — `detail` is a plain
 * string for most FastAPI HTTPExceptions, but login/register/rate-limit
 * responses use this richer shape so the UI can react to a stable `code`
 * instead of matching on message text. */
export interface ApiErrorDetail {
  message: string
  code: string
  retry_after_seconds?: number
}

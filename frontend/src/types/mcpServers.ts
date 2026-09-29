export interface McpToolCapability {
  name: string
  description?: string | null
  input_schema?: unknown
  // Standard MCP tool annotations are hints supplied by the server. Treat a
  // missing hint as unknown, never as read-only.
  annotations?: {
    readOnlyHint?: boolean
    destructiveHint?: boolean
    idempotentHint?: boolean
    openWorldHint?: boolean
  } | null
}

export interface McpServer {
  id: string
  name: string
  transport: string
  command: string | null
  url: string | null
  status: string
  error_message: string | null
  // Everything the server said about itself when it connected. `instructions`
  // is the server's own description of what it is -- optional in MCP, sent
  // once during the initialize handshake, and absent both when the server
  // omits it and on rows registered before Motoro captured it (refreshing
  // such a row fills it in). Not a column of its own for the same reason
  // `tools` isn't: it's protocol-shaped data, not schema.
  capabilities: { tools?: McpToolCapability[]; instructions?: string } | null
  authentication: {
    auth_mode: 'none' | 'static_headers' | 'stdio_env' | 'oauth'
    configured: boolean
    authorization_required: boolean
    static_headers_configured: boolean
    stdio_env_configured: boolean
    stdio_env_names: string[]
  }
  credential_management_allowed: boolean
  created_at: string
}

export interface McpOAuthAuthorization {
  authorization_url: string
  expires_at: string
  transaction_id: string
}

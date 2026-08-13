export interface McpToolCapability {
  name: string
  description?: string | null
  input_schema?: unknown
}

export interface McpServer {
  id: string
  name: string
  transport: string
  command: string | null
  url: string | null
  status: string
  error_message: string | null
  capabilities: { tools?: McpToolCapability[] } | null
  created_at: string
}

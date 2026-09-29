import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError, mcpServersApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { authorizeMcpServer } from '@/lib/mcpOAuth'
import { credentialsAreValid, credentialsRecord, emptyCredentialEntry, type CredentialEntry } from '@/lib/mcpCredentials'
import { HUD_ACCENT_RING_CLASSNAME } from '@/lib/utils'
import type { McpServer } from '@/types/mcpServers'
import { McpCredentialFields } from './McpCredentialFields'

type Mode = 'bearer' | 'headers' | 'env' | 'oauth'

export function ManageMcpCredentialsDialog({
  server,
  open,
  onOpenChange,
  onUpdated,
}: {
  server: McpServer
  open: boolean
  onOpenChange: (open: boolean) => void
  onUpdated: (server: McpServer) => void
}) {
  const initialMode: Mode = server.transport === 'stdio'
    ? 'env'
    : server.authentication.auth_mode === 'oauth' ? 'oauth' : 'bearer'
  const [mode, setMode] = useState<Mode>(initialMode)
  const [bearerToken, setBearerToken] = useState('')
  const [entries, setEntries] = useState<CredentialEntry[]>([emptyCredentialEntry()])
  const [scope, setScope] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const isHttp = server.transport === 'http'
  const canSave = mode === 'oauth'
    || (mode === 'bearer' && bearerToken.length > 0)
    || ((mode === 'headers' || mode === 'env') && credentialsAreValid(entries))
  const lockedToOAuth = server.authentication.auth_mode === 'oauth'
  const lockedToStatic = server.authentication.auth_mode === 'static_headers'
  const hasCredentials = server.authentication.configured || server.authentication.authorization_required

  async function finish(updated: McpServer) {
    await queryClient.invalidateQueries({ queryKey: ['mcp-servers'] })
    onUpdated(updated)
    onOpenChange(false)
  }

  async function save() {
    if (!canSave || pending) return
    setPending(true)
    setError(null)
    try {
      if (mode === 'oauth') {
        await authorizeMcpServer(server.id, scope.trim() || undefined)
        await finish(await mcpServersApi.get(server.id))
      } else if (mode === 'env') {
        await finish(await mcpServersApi.update(server.id, { server_env: credentialsRecord(entries) }))
      } else {
        const headers = mode === 'bearer'
          ? { Authorization: `Bearer ${bearerToken}` }
          : credentialsRecord(entries)
        await finish(await mcpServersApi.update(server.id, { headers }))
      }
    } catch (caught) {
      setError(caught instanceof ApiError && typeof caught.detail === 'string'
        ? caught.detail
        : caught instanceof Error ? caught.message : 'Could not update credentials.')
    } finally {
      setPending(false)
    }
  }

  async function clear() {
    if (pending) return
    setPending(true)
    setError(null)
    try {
      await finish(await mcpServersApi.clearCredentials(server.id, server.authentication.auth_mode === 'oauth'))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not clear credentials.')
    } finally {
      setPending(false)
    }
  }

  const modes: { value: Mode; label: string }[] = server.transport === 'stdio'
    ? [{ value: 'env', label: 'Environment' }]
    : lockedToOAuth
      ? [{ value: 'oauth', label: 'OAuth' }]
      : lockedToStatic
        ? [{ value: 'bearer', label: 'Bearer token' }, { value: 'headers', label: 'Headers' }]
        : [{ value: 'bearer', label: 'Bearer token' }, { value: 'headers', label: 'Headers' }, { value: 'oauth', label: 'OAuth' }]

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={HUD_ACCENT_RING_CLASSNAME}>
        <DialogHeader>
          <DialogTitle>Manage MCP credentials</DialogTitle>
          <DialogDescription>
            Saved secret values are never displayed. Enter replacements, reauthorize, or clear them.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Method</Label>
            <div className="grid grid-cols-2 gap-2">
              {modes.map((candidate) => (
                <button
                  key={candidate.value}
                  type="button"
                  className={`cursor-pointer rounded-lg border px-3 py-2 text-left text-sm shadow-[0_0_16px_-6px_var(--primary)] ${mode === candidate.value ? 'border-primary bg-primary/10 ring-1 ring-primary/40' : 'bg-background hover:bg-muted'}`}
                  onClick={() => setMode(candidate.value)}
                >
                  {candidate.label}
                </button>
              ))}
            </div>
            {(lockedToOAuth || lockedToStatic) && (
              <p className="text-xs text-muted-foreground">Clear the current credentials before switching to a different authentication family.</p>
            )}
          </div>
          {mode === 'bearer' && (
            <Input
              aria-label="Replacement bearer token"
              type="password"
              autoComplete="off"
              className="font-mono"
              placeholder="New bearer token"
              value={bearerToken}
              onChange={(event) => setBearerToken(event.target.value)}
            />
          )}
          {(mode === 'headers' || mode === 'env') && (
            <McpCredentialFields
              entries={entries}
              onChange={setEntries}
              namePlaceholder={mode === 'env' ? (server.authentication.stdio_env_names[0] ?? 'API_TOKEN') : 'X-API-Key'}
            />
          )}
          {mode === 'oauth' && isHttp && (
            <div className="space-y-1.5">
              <Input
                aria-label="OAuth scopes"
                className="font-mono"
                placeholder="Optional scopes"
                value={scope}
                onChange={(event) => setScope(event.target.value)}
              />
              <p className="text-xs text-muted-foreground">Saving opens the provider authorization window.</p>
            </div>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter className="sm:justify-between">
          <Button type="button" variant="destructive" disabled={pending || !hasCredentials} onClick={clear}>
            Clear credentials
          </Button>
          <Button type="button" disabled={pending || !canSave} onClick={save}>
            {pending ? 'Saving…' : mode === 'oauth' ? 'Authorize' : 'Replace credentials'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cloud, X } from 'lucide-react'
import { llmSettingsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { PasswordInput } from '@/components/ui/password-input'
import { LLM_PROVIDER_CATALOG, type LLMProvider } from '@/types/llmSettings'

// The search box stays even at one entry so this doesn't need reshaping
// once more providers are added -- see LLM_PROVIDER_CATALOG's own comment
// for why only Azure Foundry is listed today.
const PROVIDER_CATALOG = LLM_PROVIDER_CATALOG

export function CreateCredentialDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [query, setQuery] = useState('')
  const [provider, setProvider] = useState<LLMProvider | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [apiBase, setApiBase] = useState('')
  const queryClient = useQueryClient()

  const settingsQuery = useQuery({
    queryKey: ['llm-settings'],
    queryFn: () => llmSettingsApi.list(),
    enabled: open,
  })
  const existing = settingsQuery.data?.find((s) => s.provider === provider)

  const saveMutation = useMutation({
    mutationFn: () => llmSettingsApi.upsert({ provider: provider!, api_key: apiKey, api_base: apiBase || null }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llm-settings'] })
      reset()
      onOpenChange(false)
    },
  })

  function reset() {
    setQuery('')
    setProvider(null)
    setApiKey('')
    setApiBase('')
    saveMutation.reset()
  }

  const filtered = PROVIDER_CATALOG.filter((p) => p.label.toLowerCase().includes(query.trim().toLowerCase()))

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (!next) reset()
      }}
    >
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New credential</DialogTitle>
          <DialogDescription>
            {provider ? 'Enter your provider settings.' : 'Search for an LLM provider to connect.'}
          </DialogDescription>
        </DialogHeader>

        {!provider ? (
          <div className="space-y-3">
            <Input autoFocus placeholder="Search providers…" value={query} onChange={(e) => setQuery(e.target.value)} />
            <div className="flex flex-col gap-1.5">
              {filtered.length === 0 && <p className="py-4 text-center text-sm text-muted-foreground">No matching providers.</p>}
              {filtered.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => setProvider(p.id)}
                  className="flex cursor-pointer items-center gap-2.5 rounded-lg border bg-background px-3 py-2.5 text-left text-sm shadow-[0_0_16px_-6px_var(--primary)] ring-1 ring-primary/20 transition-colors hover:bg-muted"
                >
                  <Cloud className="size-4 shrink-0 text-primary" />
                  <div className="min-w-0">
                    <p className="font-medium">{p.label}</p>
                    <p className="truncate text-xs text-muted-foreground">{p.description}</p>
                  </div>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center justify-between rounded-lg border bg-muted/30 px-3 py-2">
              <span className="text-sm font-medium">{PROVIDER_CATALOG.find((p) => p.id === provider)?.label}</span>
              <Button variant="ghost" size="icon-sm" aria-label="Change provider" onClick={() => setProvider(null)}>
                <X className="size-4" />
              </Button>
            </div>

            {existing && (
              <p className="text-xs text-muted-foreground">
                You already have a saved {PROVIDER_CATALOG.find((p) => p.id === provider)?.label} credential — saving will
                replace it.
              </p>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="credential-api-key">API key</Label>
              <PasswordInput id="credential-api-key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="credential-api-base">Resource name or endpoint URL</Label>
              <Input
                id="credential-api-base"
                placeholder="https://my-resource.openai.azure.com"
                value={apiBase}
                onChange={(e) => setApiBase(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Your Azure Foundry resource name, or its full endpoint URL.
              </p>
            </div>

            {saveMutation.isError && <p className="text-sm text-destructive">Could not save this credential. Please try again.</p>}

            <DialogFooter>
              <Button
                onClick={() => saveMutation.mutate()}
                disabled={!apiKey.trim() || !apiBase.trim() || saveMutation.isPending}
              >
                {saveMutation.isPending ? 'Saving…' : 'Save credential'}
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

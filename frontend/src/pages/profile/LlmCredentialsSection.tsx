import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil, ShieldCheck, TriangleAlert, Trash2 } from 'lucide-react'
import { llmSettingsApi } from '@/api/client'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { CreateCredentialDialog } from '@/components/CreateCredentialDialog'
import { PROVIDER_META } from '@/components/protocol/nodes/LlmNode'
import { LLM_PROVIDER_LABELS, type LLMProvider, type LLMSetting } from '@/types/llmSettings'

function DeleteCredentialDialog({ setting }: { setting: LLMSetting }) {
  const [open, setOpen] = useState(false)
  const queryClient = useQueryClient()
  const label = LLM_PROVIDER_LABELS[setting.provider]

  const deleteMutation = useMutation({
    mutationFn: () => llmSettingsApi.remove(setting.provider),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llm-settings'] })
      setOpen(false)
    },
  })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button size="icon" variant="ghost" aria-label={`Delete ${label} credential`}>
            <Trash2 className="size-4 text-destructive" />
          </Button>
        }
      />
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete &ldquo;{label}&rdquo; credential?</DialogTitle>
          <DialogDescription>
            Agents using this provider will fail to run until a new credential is saved. This can&apos;t be undone.
          </DialogDescription>
        </DialogHeader>
        {deleteMutation.isError && <p className="text-sm text-destructive">Could not delete this credential. Please try again.</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={() => deleteMutation.mutate()} disabled={deleteMutation.isPending}>
            {deleteMutation.isPending ? 'Deleting…' : 'Delete'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function LlmCredentialsSection() {
  const [credentialDialogOpen, setCredentialDialogOpen] = useState(false)
  const [editingProvider, setEditingProvider] = useState<LLMProvider | null>(null)
  const { data, isLoading } = useQuery({ queryKey: ['llm-settings'], queryFn: () => llmSettingsApi.list() })

  function openForNew() {
    setEditingProvider(null)
    setCredentialDialogOpen(true)
  }

  function openForEdit(provider: LLMProvider) {
    setEditingProvider(provider)
    setCredentialDialogOpen(true)
  }

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle>LLM credentials</CardTitle>
          <CardDescription>Per-provider API keys used when running agents.</CardDescription>
        </div>
        <Button size="sm" onClick={openForNew}>
          Add credential
        </Button>
      </CardHeader>
      <CardContent>
        <div className="mb-4 space-y-2">
          <Alert>
            <ShieldCheck />
            <AlertTitle>How your API keys are secured</AlertTitle>
            <AlertDescription>
              Your API keys are encrypted at rest (Fernet symmetric encryption) before being stored, and only
              decrypted at the moment a run actually needs them -- never logged or cached in plaintext.
            </AlertDescription>
          </Alert>
          <Alert>
            <TriangleAlert className="text-[color:var(--chart-4)]" />
            <AlertTitle className="text-[color:var(--chart-4)]">Important</AlertTitle>
            <AlertDescription>
              Storing API keys in any application carries inherent risk -- if this server's encryption key were
              ever compromised, stored credentials could be exposed. Use scoped, provider-specific keys with
              minimal permissions, and rotate them regularly.
            </AlertDescription>
          </Alert>
        </div>

        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : !data || data.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No credentials saved yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Provider</TableHead>
                <TableHead>Project endpoint</TableHead>
                <TableHead className="w-20" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((setting) => {
                const meta = PROVIDER_META[setting.provider]
                const Icon = meta?.icon
                return (
                  <TableRow key={setting.provider}>
                    <TableCell className="font-medium">
                      <span className="flex items-center gap-2">
                        {Icon && <Icon className="size-4 text-muted-foreground" />}
                        {LLM_PROVIDER_LABELS[setting.provider]}
                      </span>
                    </TableCell>
                    <TableCell
                      className="max-w-64 truncate font-mono text-xs text-muted-foreground"
                      title={setting.azure_project_endpoint ?? undefined}
                    >
                      {setting.azure_project_endpoint ?? '—'}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        <Button
                          size="icon"
                          variant="ghost"
                          aria-label={`Edit ${LLM_PROVIDER_LABELS[setting.provider]} credential`}
                          onClick={() => openForEdit(setting.provider)}
                        >
                          <Pencil className="size-4" />
                        </Button>
                        <DeleteCredentialDialog setting={setting} />
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
      <CreateCredentialDialog
        open={credentialDialogOpen}
        onOpenChange={setCredentialDialogOpen}
        defaultProvider={editingProvider}
      />
    </Card>
  )
}

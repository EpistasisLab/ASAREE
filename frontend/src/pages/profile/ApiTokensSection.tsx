import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Check, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { tokenApi } from '@/api/client'
import type { TokenListItem } from '@/types/auth'

const EXPIRY_OPTIONS = [
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
  { value: '180', label: '180 days' },
  { value: '365', label: '1 year' },
  { value: 'never', label: 'No expiration' },
]

function tokenStatus(token: TokenListItem): { label: string; variant: 'default' | 'secondary' | 'destructive' } {
  if (token.is_revoked) return { label: 'Revoked', variant: 'destructive' }
  if (token.expires_at && new Date(token.expires_at) < new Date()) return { label: 'Expired', variant: 'secondary' }
  return { label: 'Active', variant: 'default' }
}

function CreateTokenDialog({ onCreated }: { onCreated: (token: string) => void }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [expiresIn, setExpiresIn] = useState('90')
  const queryClient = useQueryClient()

  const createMutation = useMutation({
    mutationFn: () =>
      tokenApi.create({ name, expires_in_days: expiresIn === 'never' ? null : Number(expiresIn) }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['tokens'] })
      onCreated(result.token)
      setName('')
      setOpen(false)
    },
  })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button size="sm">New token</Button>} />
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create API token</DialogTitle>
          <DialogDescription>You'll only be able to see the full token once, right after creating it.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="token-name">Name</Label>
            <Input id="token-name" placeholder="e.g. laptop, CI pipeline" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="token-expiry">Expires</Label>
            <Select value={expiresIn} onValueChange={(value) => value !== null && setExpiresIn(value)}>
              <SelectTrigger id="token-expiry" className="w-full">
                <SelectValue>
                  {(value: string) => EXPIRY_OPTIONS.find((opt) => opt.value === value)?.label ?? value}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {EXPIRY_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {createMutation.isError && <p className="text-sm text-destructive">Could not create the token. Please try again.</p>}
        </div>
        <DialogFooter>
          <Button
            onClick={() => createMutation.mutate()}
            disabled={!name.trim() || createMutation.isPending}
          >
            {createMutation.isPending ? 'Creating…' : 'Create token'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function NewTokenReveal({ token, onDismiss }: { token: string; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(token)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard API blocked (e.g. insecure context) — the token is still selectable text.
    }
  }

  return (
    <Alert className="mb-4">
      <AlertDescription className="space-y-2">
        <p className="font-medium text-foreground">
          Copy this token now — you won&apos;t be able to see it again.
        </p>
        <div className="flex items-center gap-2">
          <code className="flex-1 select-all overflow-x-auto rounded bg-muted px-2 py-1.5 text-xs">{token}</code>
          <Button type="button" size="icon" variant="outline" onClick={copy} aria-label="Copy token">
            {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
          </Button>
        </div>
        <Button type="button" variant="link" className="h-auto p-0" onClick={onDismiss}>
          Done
        </Button>
      </AlertDescription>
    </Alert>
  )
}

function RevokeTokenDialog({ token }: { token: TokenListItem }) {
  const [open, setOpen] = useState(false)
  const queryClient = useQueryClient()

  const revokeMutation = useMutation({
    mutationFn: () => tokenApi.revoke(token.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tokens'] })
      setOpen(false)
    },
  })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button size="icon" variant="ghost" aria-label={`Revoke ${token.name}`}>
            <Trash2 className="size-4 text-destructive" />
          </Button>
        }
      />
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Revoke &ldquo;{token.name}&rdquo;?</DialogTitle>
          <DialogDescription>
            Anything using this token will immediately lose access. This can&apos;t be undone.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={() => revokeMutation.mutate()} disabled={revokeMutation.isPending}>
            {revokeMutation.isPending ? 'Revoking…' : 'Revoke'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function ApiTokensSection() {
  const [revealedToken, setRevealedToken] = useState<string | null>(null)
  const { data, isLoading } = useQuery({ queryKey: ['tokens'], queryFn: () => tokenApi.list() })

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle>API tokens</CardTitle>
          <CardDescription>Tokens for scripts and the SDK to authenticate as you.</CardDescription>
        </div>
        <CreateTokenDialog onCreated={setRevealedToken} />
      </CardHeader>
      <CardContent>
        {revealedToken && <NewTokenReveal token={revealedToken} onDismiss={() => setRevealedToken(null)} />}

        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : !data || data.items.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No tokens yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Token</TableHead>
                <TableHead>Last used</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((token) => {
                const status = tokenStatus(token)
                return (
                  <TableRow key={token.id}>
                    <TableCell className="font-medium">{token.name}</TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {token.token_prefix ? `${token.token_prefix}…` : '—'}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {token.last_used_at ? new Date(token.last_used_at).toLocaleDateString() : 'Never'}
                    </TableCell>
                    <TableCell>
                      <Badge variant={status.variant}>{status.label}</Badge>
                    </TableCell>
                    <TableCell>{!token.is_revoked && <RevokeTokenDialog token={token} />}</TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
      {data && data.total > data.items.length && (
        <CardFooter>
          <p className="text-xs text-muted-foreground">
            Showing {data.items.length} of {data.total} tokens.
          </p>
        </CardFooter>
      )}
    </Card>
  )
}

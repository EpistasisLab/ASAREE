import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import type { Edge, Node } from '@xyflow/react'
import { Archive, ArchiveRestore, Download, Lock, LockOpen, MoreVertical, Pencil, Text } from 'lucide-react'
import { experimentsApi, protocolsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { applyExperimentRenameToProtocolCache, protocolForExperimentQueryKey, toPersistedGraph } from '@/lib/protocolGraph'
import { sanitizeFilename } from '@/lib/utils'

// Every menu item that opens a follow-up surface renders it as a SEPARATE
// controlled Dialog (open/onOpenChange state), not nested inside
// DropdownMenuContent -- the dropdown closes itself on item click (same as
// AppHeader's own "+" menu opening CreateCredentialDialog this same way), so
// a Popover anchored to the (now-closed) menu item wouldn't have anywhere to
// anchor to.
function RenameDialog({
  open,
  onOpenChange,
  experimentId,
  currentName,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  experimentId: string
  currentName: string
}) {
  const [name, setName] = useState(currentName)
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: () => experimentsApi.update(experimentId, { name }),
    onSuccess: (updated) => {
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId] })
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
      applyExperimentRenameToProtocolCache(queryClient, experimentId, updated.name)
      onOpenChange(false)
    },
  })

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (next) setName(currentName)
      }}
    >
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Rename experiment</DialogTitle>
        </DialogHeader>
        <div className="space-y-1.5">
          <Label htmlFor="rename-experiment-name">Name</Label>
          <Input
            id="rename-experiment-name"
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && name.trim()) mutation.mutate()
            }}
          />
        </div>
        {mutation.isError && <p className="text-sm text-destructive">Could not rename this experiment.</p>}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={!name.trim() || mutation.isPending}>
            {mutation.isPending ? 'Saving…' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function EditDescriptionDialog({
  open,
  onOpenChange,
  experimentId,
  currentDescription,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  experimentId: string
  currentDescription: string | null
}) {
  const [description, setDescription] = useState(currentDescription ?? '')
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: () => experimentsApi.update(experimentId, { description: description.trim() || null }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId] })
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
      onOpenChange(false)
    },
  })

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (next) setDescription(currentDescription ?? '')
      }}
    >
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Edit description</DialogTitle>
        </DialogHeader>
        <div className="space-y-1.5">
          <Label htmlFor="edit-experiment-description">Description</Label>
          <Textarea
            id="edit-experiment-description"
            autoFocus
            rows={4}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        {mutation.isError && <p className="text-sm text-destructive">Could not save this description.</p>}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={mutation.isPending}>
            {mutation.isPending ? 'Saving…' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// The canvas-level "⋮" menu, next to Run/+ -- acts on the EXPERIMENT for
// identity/lifecycle (rename/description/archive, matching this app's own
// "experiments instead of workflows" framing), and on the PROTOCOL's own
// graph for Download. Importing always creates a separate experiment from the
// global Create menu, so a canvas can never be changed by accident here.
// Deliberately no Delete here (or anywhere in the GUI) -- Archive is the
// only lifecycle-destructive action end users get, so a misclick can't
// silently lose an experiment's runs/cells. DELETE /experiments/{id} still
// exists on the backend/SDK for scripted cleanup; experimentsApi.remove is
// intentionally unused by any GUI surface.
export function ProtocolCanvasMenu({
  protocolId,
  experimentId,
  nodes,
  edges,
}: {
  protocolId: string
  experimentId: string | null
  nodes: Node[]
  edges: Edge[]
}) {
  const [renameOpen, setRenameOpen] = useState(false)
  const [descriptionOpen, setDescriptionOpen] = useState(false)
  const [lockConfirmOpen, setLockConfirmOpen] = useState(false)
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const experimentQuery = useQuery({
    queryKey: ['experiments', experimentId],
    queryFn: () => experimentsApi.get(experimentId!),
    enabled: !!experimentId,
  })
  const experiment = experimentQuery.data

  const archiveMutation = useMutation({
    mutationFn: (archive: boolean) =>
      experimentsApi.update(experimentId!, { archived_at: archive ? new Date().toISOString() : null }),
    onSuccess: (_data, archive) => {
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId] })
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
      if (archive) navigate('/experiments')
    },
  })
  const lockMutation = useMutation({
    mutationFn: async (lock: boolean) => {
      if (!lock) return { experiment: await experimentsApi.unlock(experimentId!), protocol: null }

      // The canvas autosave is intentionally debounced. Locking must capture
      // what is visibly on screen even when the user clicks it inside that
      // quiet window, so compare the live graph to the server draft, save it
      // if needed, then publish that exact draft as the lock's revision.
      const liveGraph = toPersistedGraph(nodes, edges)
      const current = await protocolsApi.get(protocolId)
      let publishable = current
      if (JSON.stringify(current.graph) !== JSON.stringify(liveGraph)) {
        publishable = await protocolsApi.update(protocolId, { graph: liveGraph })
      }
      if (publishable.has_unpublished_changes) {
        publishable = await protocolsApi.publish(protocolId)
      }
      return { experiment: await experimentsApi.lock(experimentId!), protocol: publishable }
    },
    onSuccess: ({ experiment: updated, protocol }) => {
      queryClient.setQueryData(['experiments', experimentId], updated)
      if (protocol && experimentId) queryClient.setQueryData(protocolForExperimentQueryKey(experimentId), protocol)
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
      setLockConfirmOpen(false)
    },
  })

  async function handleDownload() {
    const protocol = await protocolsApi.get(protocolId)
    // Protocol.name is "Protocol: {experiment name} [{shortid}]" -- an
    // internal, collision-proofed label (renames now keep it in sync, but the
    // shape stays). What the user actually calls this thing is the
    // experiment's own name, already loaded above for the Archive/Delete/etc.
    // actions, so that's what the exported payload and the downloaded
    // filename use.
    const displayName = experiment?.name ?? protocol.name
    // Serializes the canvas's own LIVE nodes/edges (props passed down from
    // ProtocolCanvas.tsx), not protocolsApi.get's possibly-stale graph --
    // reuses that file's own toPersistedGraph, the same helper its autosave uses.
    // design_spec travels alongside the graph -- a factor is meaningless
    // without both halves of its binding (design_spec.factors' declaration
    // AND the node's own factor_bindings, already inside the graph), so an
    // export/import round-trip that dropped one would silently orphan the
    // other.
    const payload = {
      name: displayName,
      description: protocol.description,
      graph: toPersistedGraph(nodes, edges),
      design_spec: experiment?.design_spec ?? null,
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${sanitizeFilename(displayName, 'protocol')}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  const isArchived = !!experiment?.archived_at
  const isLocked = !!experiment?.locked_at

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="outline" size="icon-sm" aria-label="Experiment menu" />}>
          <MoreVertical className="size-4" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem disabled={!experimentId} onClick={() => setRenameOpen(true)}>
            <Pencil className="size-4" />
            Rename
          </DropdownMenuItem>
          <DropdownMenuItem disabled={!experimentId} onClick={() => setDescriptionOpen(true)}>
            <Text className="size-4" />
            Edit description
          </DropdownMenuItem>
          <DropdownMenuItem disabled={!experimentId || lockMutation.isPending} onClick={() => setLockConfirmOpen(true)}>
            {isLocked ? <LockOpen className="size-4" /> : <Lock className="size-4" />}
            {isLocked ? 'Unlock for editing…' : 'Lock experiment…'}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            disabled={!experimentId || archiveMutation.isPending}
            onClick={() => archiveMutation.mutate(!isArchived)}
          >
            {isArchived ? <ArchiveRestore className="size-4" /> : <Archive className="size-4" />}
            {isArchived ? 'Unarchive' : 'Archive'}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => void handleDownload()}>
            <Download className="size-4" />
            Download
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      {experimentId && experiment && (
        <>
          <RenameDialog open={renameOpen} onOpenChange={setRenameOpen} experimentId={experimentId} currentName={experiment.name} />
          <EditDescriptionDialog
            open={descriptionOpen}
            onOpenChange={setDescriptionOpen}
            experimentId={experimentId}
            currentDescription={experiment.description}
          />
          <Dialog open={lockConfirmOpen} onOpenChange={setLockConfirmOpen}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>{isLocked ? 'Unlock experiment for editing?' : 'Lock experiment?'}</DialogTitle>
              </DialogHeader>
              <p className="text-sm text-muted-foreground">
                {isLocked
                  ? 'Canvas and design changes will be allowed again. Publishing a changed canvas or regenerating a changed design will make earlier runs obsolete.'
                  : 'This saves and publishes the current canvas, then records it with the design as the approved experiment. Only the replicate count can be changed while locked.'}
              </p>
              {lockMutation.isError && <p className="text-sm text-destructive">{lockMutation.error instanceof Error ? lockMutation.error.message : 'Could not update the experiment lock.'}</p>}
              <DialogFooter>
                <Button variant="outline" onClick={() => setLockConfirmOpen(false)}>Cancel</Button>
                <Button onClick={() => lockMutation.mutate(!isLocked)}>{lockMutation.isPending ? 'Saving…' : isLocked ? 'Unlock for editing' : 'Lock experiment'}</Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </>
      )}
    </>
  )
}

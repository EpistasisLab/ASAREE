import { useRef, useState, type ChangeEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import type { Edge, Node } from '@xyflow/react'
import { Archive, ArchiveRestore, Download, MoreVertical, Pencil, Text, Trash2, Upload } from 'lucide-react'
import { experimentsApi, protocolsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { toPersistedGraph } from '@/lib/protocolGraph'
import type { ProtocolGraph } from '@/types/protocols'

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

function DeleteConfirmDialog({
  open,
  onOpenChange,
  experimentId,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  experimentId: string
}) {
  const navigate = useNavigate()
  const mutation = useMutation({
    mutationFn: () => experimentsApi.remove(experimentId),
    onSuccess: () => navigate('/experiments'),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete this experiment?</DialogTitle>
          <DialogDescription>
            This permanently deletes the experiment, its protocol runs, and all cell results. This cannot be undone.
            Consider Archive instead if you just want it out of your active list.
          </DialogDescription>
        </DialogHeader>
        {mutation.isError && <p className="text-sm text-destructive">Could not delete this experiment.</p>}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={() => mutation.mutate()} disabled={mutation.isPending}>
            {mutation.isPending ? 'Deleting…' : 'Delete'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function sanitizeFilename(name: string): string {
  return name.trim().replace(/[^a-z0-9-_]+/gi, '-').replace(/^-+|-+$/g, '') || 'protocol'
}

// The canvas-level "⋮" menu, next to Run/+ -- acts on the EXPERIMENT for
// identity/lifecycle (rename/description/archive/delete, matching this
// app's own "experiments instead of workflows" framing), and on the
// PROTOCOL's own graph for Download/Import, since that's the only part with
// an actual JSON-able shape. Import only parses -- the actual merge-into-
// canvas (id remapping, position offsetting) happens in ProtocolCanvas.tsx
// itself via onImport, since that's where nodes/edges/findFreePosition live.
export function ProtocolCanvasMenu({
  protocolId,
  experimentId,
  nodes,
  edges,
  onImport,
}: {
  protocolId: string
  experimentId: string | null
  nodes: Node[]
  edges: Edge[]
  onImport: (graph: ProtocolGraph) => void
}) {
  const [renameOpen, setRenameOpen] = useState(false)
  const [descriptionOpen, setDescriptionOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
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

  async function handleDownload() {
    const protocol = await protocolsApi.get(protocolId)
    // Protocol.name is set once at creation ("Protocol: {experiment name
    // at the time}") and never kept in sync when the experiment is renamed
    // afterward -- the experiment's CURRENT name (already loaded above for
    // the Archive/Delete/etc. actions) is what the user actually calls this
    // thing, so that's what both the exported payload's name and the
    // downloaded filename should reflect, not the stale internal one.
    const displayName = experiment?.name ?? protocol.name
    // Serializes the canvas's own LIVE nodes/edges (props passed down from
    // ProtocolCanvas.tsx), not protocolsApi.get's possibly-stale graph --
    // reuses that file's own toPersistedGraph, the same helper its autosave uses.
    const payload = { name: displayName, description: protocol.description, graph: toPersistedGraph(nodes, edges) }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${sanitizeFilename(displayName)}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  function handleFileSelected(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = '' // allow re-selecting the same file later
    if (!file) return
    setImportError(null)
    file
      .text()
      .then((text) => {
        const parsed = JSON.parse(text)
        if (!Array.isArray(parsed?.graph?.nodes) || !Array.isArray(parsed?.graph?.edges)) {
          setImportError('This file is not a valid protocol export.')
          return
        }
        onImport(parsed.graph as ProtocolGraph)
      })
      .catch(() => setImportError('Could not read this file as JSON.'))
  }

  const isArchived = !!experiment?.archived_at

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
          <DropdownMenuSeparator />
          <DropdownMenuItem
            disabled={!experimentId || archiveMutation.isPending}
            onClick={() => archiveMutation.mutate(!isArchived)}
          >
            {isArchived ? <ArchiveRestore className="size-4" /> : <Archive className="size-4" />}
            {isArchived ? 'Unarchive' : 'Archive'}
          </DropdownMenuItem>
          <DropdownMenuItem disabled={!experimentId} variant="destructive" onClick={() => setDeleteOpen(true)}>
            <Trash2 className="size-4" />
            Delete
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => void handleDownload()}>
            <Download className="size-4" />
            Download
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => fileInputRef.current?.click()}>
            <Upload className="size-4" />
            Import from file…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <input ref={fileInputRef} type="file" accept="application/json" className="hidden" onChange={handleFileSelected} />

      {importError && (
        <span
          className="absolute top-14 right-3 z-10 max-w-64 truncate rounded-md bg-destructive/10 px-2 py-1 text-xs text-destructive"
          title={importError}
        >
          {importError}
        </span>
      )}

      {experimentId && experiment && (
        <>
          <RenameDialog open={renameOpen} onOpenChange={setRenameOpen} experimentId={experimentId} currentName={experiment.name} />
          <EditDescriptionDialog
            open={descriptionOpen}
            onOpenChange={setDescriptionOpen}
            experimentId={experimentId}
            currentDescription={experiment.description}
          />
          <DeleteConfirmDialog open={deleteOpen} onOpenChange={setDeleteOpen} experimentId={experimentId} />
        </>
      )}
    </>
  )
}

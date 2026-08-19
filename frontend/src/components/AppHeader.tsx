import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, KeyRound, SquarePlus, UserRound } from 'lucide-react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { experimentsApi } from '@/api/client'
import { CreateCredentialDialog } from '@/components/CreateCredentialDialog'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { useAuth } from '@/hooks/useAuth'
import { cn } from '@/lib/utils'

const NAV_LINKS = [{ to: '/experiments', label: 'Experiments' }]

export function AppHeader() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [credentialDialogOpen, setCredentialDialogOpen] = useState(false)

  // Global "+" menu (not page-local) so creating an experiment
  // doesn't require first navigating to the Experiments list.
  //
  // No name is sent: the server allocates the placeholder "Untitled Experiment
  // N" inside the insert's own transaction. This used to compute the name here
  // from a GET /experiments, which cannot be made correct from the browser --
  // the list omitted archived experiments whose names are still reserved, and
  // even a perfect list goes stale between the GET and the POST. Both showed up
  // as a silent 409.
  const createExperimentMutation = useMutation({
    mutationFn: () => experimentsApi.create({}),
    onSuccess: (experiment) => {
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
      navigate(`/experiments/${experiment.id}/protocol`)
    },
  })

  async function handleLogout() {
    await logout()
    navigate('/login')
  }

  return (
    <header className="border-b bg-background">
      <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <div className="flex items-center gap-6">
          <Link to="/experiments" className="flex items-center gap-3">
            <div className="flex size-8 items-center justify-center rounded-lg bg-primary font-mono text-sm font-semibold text-primary-foreground shadow-[0_0_14px_-3px_var(--primary)]">
              A
            </div>
            <span className="font-semibold tracking-wide">ASAREE</span>
          </Link>
          {/* Split button: the main segment is the primary,
              one-click action; the chevron segment opens a menu for
              secondary create actions -- replaces the old icon-only "+"
              that put both behind an equal-weight menu. */}
          <div className="flex items-center">
            <Button
              size="sm"
              className="rounded-r-none"
              onClick={() => createExperimentMutation.mutate()}
              disabled={createExperimentMutation.isPending}
            >
              <SquarePlus className="size-4" />
              {createExperimentMutation.isPending ? 'Creating…' : 'Create experiment'}
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <Button
                    size="sm"
                    aria-label="More create options"
                    className="rounded-l-none border-l border-l-primary-foreground/20 px-1.5"
                  />
                }
              >
                <ChevronDown className="size-4" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuItem onClick={() => setCredentialDialogOpen(true)}>
                  <KeyRound className="size-4" />
                  Create credential
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
          {/* Without this the mutation failed silently -- the name-conflict 409
              only ever reached the browser console, so the button just looked
              dead. That specific failure is gone now the server names the
              experiment, but a create can still fail (network, session expiry),
              and none of those should be invisible either. Clears on the next
              mutate() call. */}
          {createExperimentMutation.isError && (
            <p className="text-sm text-destructive">Could not create the experiment. Please try again.</p>
          )}
          <nav className="flex items-center gap-4">
            {NAV_LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                className={({ isActive }) =>
                  cn(
                    'text-sm font-medium text-muted-foreground transition-colors hover:text-foreground',
                    isActive && 'text-primary drop-shadow-[0_0_6px_var(--primary)]',
                  )
                }
              >
                {link.label}
              </NavLink>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-4">
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <button
                  type="button"
                  className="flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground aria-expanded:text-foreground"
                />
              }
            >
              {user?.display_name}
              <ChevronDown className="size-3.5" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => navigate('/profile')}>
                <UserRound className="size-4" />
                Profile
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button variant="outline" size="sm" onClick={handleLogout}>
            Log out
          </Button>
        </div>
      </div>
      <CreateCredentialDialog open={credentialDialogOpen} onOpenChange={setCredentialDialogOpen} />
    </header>
  )
}

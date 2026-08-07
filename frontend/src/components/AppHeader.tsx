import { NavLink, useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/hooks/useAuth'
import { cn } from '@/lib/utils'

const NAV_LINKS = [
  { to: '/experiments', label: 'Experiments' },
  { to: '/profile', label: 'Account' },
]

export function AppHeader() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  async function handleLogout() {
    await logout()
    navigate('/login')
  }

  return (
    <header className="border-b bg-background">
      <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-3">
            <div className="flex size-8 items-center justify-center rounded-lg bg-primary font-mono text-sm font-semibold text-primary-foreground shadow-[0_0_14px_-3px_var(--primary)]">
              A
            </div>
            <span className="font-semibold tracking-wide">ASAREE</span>
          </div>
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
          <span className="text-sm text-muted-foreground">{user?.display_name}</span>
          <Button variant="outline" size="sm" onClick={handleLogout}>
            Log out
          </Button>
        </div>
      </div>
    </header>
  )
}

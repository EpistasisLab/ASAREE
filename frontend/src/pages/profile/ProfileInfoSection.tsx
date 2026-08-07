import { useState, type FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { useAuth } from '@/hooks/useAuth'
import { authApi, ApiError } from '@/api/client'

export function ProfileInfoSection() {
  const { user, refreshUser } = useAuth()
  const [displayName, setDisplayName] = useState(user?.display_name ?? '')
  const [email, setEmail] = useState(user?.email ?? '')
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  if (!user) return null
  // A stable non-null reference for the closures below — TS can't carry the
  // guard above across a function boundary, since `user` could in principle
  // change by the time one of them actually runs.
  const currentUser = user

  function startEditing() {
    setDisplayName(currentUser.display_name)
    setEmail(currentUser.email)
    setError(null)
    setSuccess(false)
    setEditing(true)
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await authApi.updateMe({ display_name: displayName.trim(), email })
      await refreshUser()
      setEditing(false)
      setSuccess(true)
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setError('That email is already registered to another account.')
      else setError('Could not update your profile. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Profile</CardTitle>
        <CardDescription>Your account information.</CardDescription>
      </CardHeader>
      <CardContent>
        {success && !editing && (
          <Alert className="mb-4">
            <AlertDescription>Profile updated.</AlertDescription>
          </Alert>
        )}
        {editing ? (
          <form onSubmit={handleSubmit} className="space-y-4" id="profile-form">
            {error && (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <div className="space-y-2">
              <Label htmlFor="profile-name">Name</Label>
              <Input id="profile-name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="profile-email">Email</Label>
              <Input id="profile-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
          </form>
        ) : (
          <dl className="space-y-3 text-sm">
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Name</dt>
              <dd className="font-medium">{user.display_name}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Email</dt>
              <dd className="font-medium">{user.email}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Member since</dt>
              <dd className="font-medium">{new Date(user.created_at).toLocaleDateString()}</dd>
            </div>
          </dl>
        )}
      </CardContent>
      <CardFooter className="justify-end gap-2">
        {editing ? (
          <>
            <Button type="button" variant="ghost" onClick={() => setEditing(false)} disabled={submitting}>
              Cancel
            </Button>
            <Button type="submit" form="profile-form" disabled={submitting}>
              {submitting ? 'Saving…' : 'Save changes'}
            </Button>
          </>
        ) : (
          <Button type="button" variant="outline" onClick={startEditing}>
            Edit profile
          </Button>
        )}
      </CardFooter>
    </Card>
  )
}

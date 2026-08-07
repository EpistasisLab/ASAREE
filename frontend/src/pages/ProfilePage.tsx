import { AppHeader } from '@/components/AppHeader'
import { ProfileInfoSection } from '@/pages/profile/ProfileInfoSection'
import { ChangePasswordSection } from '@/pages/profile/ChangePasswordSection'
import { ApiTokensSection } from '@/pages/profile/ApiTokensSection'

export function ProfilePage() {
  return (
    <div className="min-h-svh bg-muted/30">
      <AppHeader />

      <main className="mx-auto max-w-3xl space-y-6 px-6 py-10">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Account settings</h1>
          <p className="text-sm text-muted-foreground">Manage your profile, password, and API tokens.</p>
        </div>
        <ProfileInfoSection />
        <ChangePasswordSection />
        <ApiTokensSection />
      </main>
    </div>
  )
}

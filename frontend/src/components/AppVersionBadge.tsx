import { useQuery } from '@tanstack/react-query'
import { versionApi } from '@/api/client'

/** Turns the backend's PEP 440 version into the short form the corner shows.
 *
 * `0.3.0a0` -> `v0.3.0a`: hatch-vcs derives the version from the git tag and
 * PEP 440 normalisation pads a bare alpha marker with an implicit `0`, so the
 * tag `v0.3.0a` comes back as `0.3.0a0`. Dropping that padding shows the tag
 * that was actually cut rather than a number nobody typed.
 *
 * `0.2.1.dev50+g8366ca1` -> `v0.2.1-dev50`: between tags hatch-vcs guesses the
 * next patch release and counts the commits since. Kept (rather than rounded to
 * the last real release) because "which build am I looking at" is the whole
 * point of the badge, and a dev build is exactly the case where the tag alone
 * would be a lie. The `+g<sha>` local part is dropped from the badge and kept
 * in its tooltip -- it's the part you only want when you're already suspicious.
 */
function formatVersion(raw: string): string {
  const [publicPart] = raw.split('+')
  return `v${publicPart.replace(/\.dev/, '-dev').replace(/(a|b|rc)0$/, '$1')}`
}

/** The running build's version, pinned to the very top-right of the viewport.
 *
 * Rendered once at the app root rather than inside AppHeader so it also shows
 * on the login/register screens (AuthLayout) -- "which version is this server
 * running" is a question you can have before you're logged in, and it's one
 * place instead of two.
 *
 * `top-0` puts it in the header's own top padding, ABOVE the vertically
 * centred Log out button rather than on top of it -- which matters at viewport
 * widths near AppHeader's max-w-5xl, where that button is itself at the right
 * edge.
 */
export function AppVersionBadge() {
  const { data } = useQuery({
    queryKey: ['app-version'],
    queryFn: versionApi.get,
    // A build can't change under a running tab, so this is fetched once per
    // session and never revalidated. Failure is silent: a missing badge is a
    // better outcome than an error toast over the login form.
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
  })

  if (!data?.version) return null

  return (
    <span
      title={`ASAREE ${data.version}`}
      className="fixed top-0 right-2 z-50 font-mono text-[10px] leading-4 text-muted-foreground/60"
    >
      {formatVersion(data.version)}
    </span>
  )
}

import { useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

const SDK_GIT_URL = 'git+https://github.com/EpistasisLab/ASAREE.git@v0.1.0#subdirectory=sdk'

function CodeBlock({ label, code }: { label: string; code: string }) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard API blocked (e.g. insecure context) — the command is still selectable text.
    }
  }

  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</p>
      <div className="flex items-start gap-2">
        <code className="flex-1 overflow-x-auto rounded bg-muted px-2.5 py-2 font-mono text-xs whitespace-pre select-all">
          {code}
        </code>
        <Button type="button" size="icon" variant="outline" onClick={copy} aria-label={`Copy ${label} command`}>
          {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
        </Button>
      </div>
    </div>
  )
}

export function SdkInstallSection() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>SDK access</CardTitle>
        <CardDescription>Drive experiments from your own machine with the Python SDK.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          ASAREE is a private repository — a plain <code className="font-mono text-xs">https://</code> install needs
          your GitHub credentials available to git. One-time setup if you haven't already:
        </p>
        <CodeBlock label="One-time auth setup" code={'gh auth login\ngh auth setup-git'} />
        <CodeBlock label="pip" code={`pip install "${SDK_GIT_URL}"`} />
        <CodeBlock label="uv" code={`uv add "${SDK_GIT_URL}"`} />
        <p className="text-sm text-muted-foreground">
          Then point the SDK at this server and an API token — create one below, then export:
        </p>
        <CodeBlock
          label="Environment"
          code={'export ASAREE_BASE_URL="https://<your-asaree-host>"\nexport ASAREE_API_KEY="<paste your token>"'}
        />
      </CardContent>
    </Card>
  )
}

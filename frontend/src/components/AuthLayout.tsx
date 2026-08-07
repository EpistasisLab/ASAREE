import type { ReactNode } from 'react'

export function AuthLayout({
  title,
  description,
  children,
  footer,
}: {
  title: string
  description?: string
  children: ReactNode
  footer?: ReactNode
}) {
  return (
    <div className="flex min-h-svh flex-col items-center justify-center bg-gradient-to-b from-background to-muted/50 p-6">
      <div className="mb-8 flex flex-col items-center gap-2 text-center">
        <div className="flex size-10 items-center justify-center rounded-xl bg-primary font-mono font-semibold text-primary-foreground shadow-[0_0_16px_-3px_var(--primary)]">
          A
        </div>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">{title}</h1>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      <div className="relative w-full max-w-sm rounded-xl border bg-card p-6 shadow-[0_0_28px_-14px_var(--primary)] before:pointer-events-none before:absolute before:top-1 before:left-1 before:size-3 before:border-t-2 before:border-l-2 before:border-primary/60 before:content-[''] after:pointer-events-none after:absolute after:right-1 after:bottom-1 after:size-3 after:border-r-2 after:border-b-2 after:border-primary/60 after:content-['']">
        {children}
      </div>
      {footer && <div className="mt-6 text-sm text-muted-foreground">{footer}</div>}
    </div>
  )
}

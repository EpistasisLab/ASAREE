import { useCallback, useEffect, useRef, useState } from 'react'

export const DIALOG_AUTOSAVE_DELAY_MS = 800

export type DialogAutosaveStatus = 'idle' | 'waiting' | 'saving' | 'saved' | 'error'

export function useDialogAutosave<T>({
  draft,
  signature,
  onSave,
  delay = DIALOG_AUTOSAVE_DELAY_MS,
}: {
  draft: T | null
  signature: string
  onSave: (draft: T) => void | Promise<unknown>
  delay?: number
}) {
  const [status, setStatus] = useState<DialogAutosaveStatus>('idle')
  const saveRef = useRef(onSave)
  const latestRef = useRef<{ draft: T; signature: string } | null>(null)
  const submittedSignatureRef = useRef<string | null>(null)
  const failedSignatureRef = useRef<string | null>(null)
  const queueRef = useRef<Promise<void> | null>(null)
  const mountedRef = useRef(true)
  saveRef.current = onSave

  const pending = draft && submittedSignatureRef.current !== signature ? { draft, signature } : null
  const candidate = pending && failedSignatureRef.current !== signature ? pending : null
  latestRef.current = pending

  const submit = useCallback((next: { draft: T; signature: string }, reportStatus = true) => {
    if (submittedSignatureRef.current === next.signature) {
      if (latestRef.current?.signature === next.signature) latestRef.current = null
      return
    }

    submittedSignatureRef.current = next.signature
    failedSignatureRef.current = null
    if (latestRef.current?.signature === next.signature) latestRef.current = null
    if (reportStatus && mountedRef.current) setStatus('saving')

    const run = () => saveRef.current(next.draft)
    let save: Promise<unknown>
    if (queueRef.current) {
      save = queueRef.current.then(run, run)
    } else {
      try {
        // Start an idle save synchronously. In particular, a close-time flush
        // must begin before its parent dialog unmounts this hook.
        save = Promise.resolve(run())
      } catch (error) {
        save = Promise.reject(error)
      }
    }
    const settled = save.then(() => undefined, () => undefined)
    queueRef.current = settled
    void settled.then(() => {
      if (queueRef.current === settled) queueRef.current = null
    })
    void save.then(
      () => {
        if (reportStatus && mountedRef.current && submittedSignatureRef.current === next.signature) setStatus('saved')
      },
      () => {
        if (submittedSignatureRef.current === next.signature) {
          submittedSignatureRef.current = null
          failedSignatureRef.current = next.signature
          latestRef.current = next
          if (reportStatus && mountedRef.current) setStatus('error')
        }
      },
    )
  }, [])

  const flush = useCallback(() => {
    const next = latestRef.current
    if (next) submit(next)
  }, [submit])

  useEffect(() => {
    if (!candidate) {
      // A pending draft can become invalid or be moved into a nested editor
      // before its debounce fires. Do not leave the parent dialog claiming a
      // save is still queued after that timer has been cancelled.
      setStatus((current) => current === 'waiting' ? 'idle' : current)
      return
    }
    setStatus('waiting')
    const timer = window.setTimeout(() => submit(candidate), delay)
    return () => window.clearTimeout(timer)
    // The signature is the canonical identity of the draft. Depending on the
    // object itself would restart the timer on unrelated parent renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [delay, signature, submit, Boolean(candidate)])

  useEffect(() => {
    return () => {
      mountedRef.current = false
      const next = latestRef.current
      if (next) submit(next, false)
    }
  }, [submit])

  return { flush, status }
}

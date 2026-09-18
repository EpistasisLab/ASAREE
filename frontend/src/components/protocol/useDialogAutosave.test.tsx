import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useDialogAutosave } from './useDialogAutosave'

describe('useDialogAutosave', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('saves the latest draft after the debounce', async () => {
    vi.useFakeTimers()
    const save = vi.fn().mockResolvedValue(undefined)
    const { rerender } = renderHook(
      ({ value, signature }) => useDialogAutosave({ draft: value, signature, onSave: save }),
      { initialProps: { value: null as { name: string } | null, signature: 'initial' } },
    )

    rerender({ value: { name: 'First' }, signature: 'first' })
    rerender({ value: { name: 'Latest' }, signature: 'latest' })

    await act(() => vi.advanceTimersByTimeAsync(799))
    expect(save).not.toHaveBeenCalled()
    await act(() => vi.advanceTimersByTimeAsync(1))
    expect(save).toHaveBeenCalledTimes(1)
    expect(save).toHaveBeenCalledWith({ name: 'Latest' })
  })

  it('flushes a pending valid draft immediately on close', async () => {
    vi.useFakeTimers()
    const save = vi.fn().mockResolvedValue(undefined)
    const { result, rerender } = renderHook(
      ({ value, signature }) => useDialogAutosave({ draft: value, signature, onSave: save }),
      { initialProps: { value: null as { name: string } | null, signature: 'initial' } },
    )

    rerender({ value: { name: 'Close me' }, signature: 'close' })
    act(() => result.current.flush())

    expect(save).toHaveBeenCalledTimes(1)
    expect(save).toHaveBeenCalledWith({ name: 'Close me' })
  })

  it('does not persist an incomplete draft', async () => {
    vi.useFakeTimers()
    const save = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => useDialogAutosave({ draft: null, signature: 'invalid', onSave: save }))

    await act(async () => {
      result.current.flush()
      await vi.runAllTimersAsync()
    })
    expect(save).not.toHaveBeenCalled()
  })

  it('returns to idle when a queued draft is cancelled before saving', () => {
    vi.useFakeTimers()
    const save = vi.fn().mockResolvedValue(undefined)
    const { result, rerender } = renderHook(
      ({ value, signature }) => useDialogAutosave({ draft: value, signature, onSave: save }),
      { initialProps: { value: null as { name: string } | null, signature: 'initial' } },
    )

    rerender({ value: { name: 'Pending' }, signature: 'pending' })
    expect(result.current.status).toBe('waiting')

    rerender({ value: null, signature: 'pending' })
    expect(result.current.status).toBe('idle')
    expect(save).not.toHaveBeenCalled()
  })
})

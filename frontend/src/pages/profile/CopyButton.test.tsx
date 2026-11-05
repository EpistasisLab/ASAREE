import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { CopyButton } from './CopyButton'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('shows Copied in a tooltip only after the clipboard write succeeds', async () => {
  let finish!: () => void
  const writeText = vi.fn(() => new Promise<void>(resolve => { finish = resolve }))
  vi.stubGlobal('navigator', { clipboard: { writeText } })
  render(<CopyButton text="test-token" label="Copy token" />)
  fireEvent.click(screen.getByRole('button', { name: 'Copy token' }))
  expect(writeText).toHaveBeenCalledWith('test-token')
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  finish()
  expect(await screen.findByRole('tooltip')).toHaveTextContent('Copied')
  expect(screen.getByRole('status')).toHaveTextContent('Copied')
})

it('copies on HTTP installations without the Clipboard API', async () => {
  vi.stubGlobal('navigator', {})
  const execCommand = vi.fn(() => true)
  vi.stubGlobal('document', document)
  Object.defineProperty(document, 'execCommand', { configurable: true, value: execCommand })
  render(<CopyButton text="test-token" label="Copy token" />)
  fireEvent.click(screen.getByRole('button', { name: 'Copy token' }))
  expect(execCommand).toHaveBeenCalledWith('copy')
  expect(await screen.findByRole('tooltip')).toHaveTextContent('Copied')
  expect(document.querySelector('textarea')).toBeNull()
  Reflect.deleteProperty(document, 'execCommand')
})

it('reports clipboard rejection instead of silently failing or claiming success', async () => {
  vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('Denied')) } })
  render(<CopyButton text="test-token" label="Copy token" />)
  fireEvent.click(screen.getByRole('button', { name: 'Copy token' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Select and copy the text manually')
  await waitFor(() => expect(screen.getByRole('status')).toBeEmptyDOMElement())
})

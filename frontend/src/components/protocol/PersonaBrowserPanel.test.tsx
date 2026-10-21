import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { personasApi } from '@/api/client'
import type { Persona } from '@/types/personas'
import { PersonaBrowserPanel } from './PersonaBrowserPanel'

const saved: Persona = {
  id: 'saved', name: 'Careful analyst', description: 'Check the evidence', prompt: 'Think carefully',
  source_filename: 'careful.md', revision: 2, prompt_sha256: 'hash', traits: {}, created_at: '', updated_at: '',
}

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const pick = vi.fn()
  render(<QueryClientProvider client={client}><PersonaBrowserPanel onPick={pick} onBack={vi.fn()} onClose={vi.fn()} /></QueryClientProvider>)
  return pick
}

it('picks a single saved persona snapshot with its provenance', async () => {
  vi.spyOn(personasApi, 'list').mockResolvedValue([saved])
  const pick = setup()
  fireEvent.click(await screen.findByRole('button', { name: /Careful analyst/ }))
  expect(pick).toHaveBeenCalledWith(expect.objectContaining({
    persona_ids: ['saved'], persona_text: 'Think carefully',
    persona_sources: [{ id: 'saved', revision: 2, prompt_sha256: 'hash' }],
  }))
})

it('uploads to the library without placing a node until the user selects it', async () => {
  vi.spyOn(personasApi, 'list').mockResolvedValue([])
  const upload = vi.spyOn(personasApi, 'upload').mockImplementation(async () => {
    vi.mocked(personasApi.list).mockResolvedValue([saved])
    return saved
  })
  const pick = setup()
  const file = new File(['Think carefully'], 'careful.md', { type: 'text/markdown' })
  fireEvent.change(screen.getByLabelText('Upload persona (.md)'), { target: { files: [file] } })
  await screen.findByRole('button', { name: /Careful analyst/ })
  expect(upload).toHaveBeenCalledWith(file, expect.anything())
  expect(pick).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: /Careful analyst/ }))
  expect(pick).toHaveBeenCalledTimes(1)
})

it('keeps built-ins and upload available if the saved library fails', async () => {
  vi.spyOn(personasApi, 'list').mockRejectedValue(new Error('Offline'))
  const pick = setup()
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Could not load saved personas'))
  expect(screen.getByRole('button', { name: 'Upload persona (.md)' })).toBeEnabled()
  fireEvent.click(screen.getByRole('button', { name: /Baseline/ }))
  expect(pick).toHaveBeenCalledWith(expect.objectContaining({ persona_text: '', persona_ids: ['00-baseline'] }))
})

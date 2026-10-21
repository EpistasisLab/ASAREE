import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { personasApi } from '@/api/client'
import type { Persona } from '@/types/personas'
import type { PersonaNodeConfig } from '@/types/protocols'
import { PersonaLibraryPicker } from './PersonaLibraryPicker'
import { configForSavedPersona } from './usePersonaLibrary'

const saved: Persona = {
  id: 'saved-persona', name: 'Careful analyst', description: null, prompt: 'Original instructions',
  source_filename: 'careful.md', traits: {}, revision: 1, prompt_sha256: 'original-hash', created_at: '', updated_at: '',
}

function renderPicker(config: PersonaNodeConfig) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  client.setQueryData(['personas'], [saved])
  function Harness() {
    const [value, setValue] = useState(config)
    return <><PersonaLibraryPicker value={value} onChange={setValue} /><output aria-label="Persona snapshot">{JSON.stringify(value)}</output></>
  }
  render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>)
  return client
}

it('retains snapshotted text and provenance when a library entry changes or is deleted', async () => {
  const client = renderPicker(configForSavedPersona(saved))
  client.setQueryData(['personas'], [{ ...saved, prompt: 'New instructions', revision: 2, prompt_sha256: 'new-hash' }])
  await waitFor(() => expect(screen.getByLabelText('Persona snapshot')).toHaveTextContent('original-hash'))
  expect(screen.getByLabelText('Persona snapshot')).toHaveTextContent('Original instructions')
  client.setQueryData(['personas'], [])
  await screen.findByText(/A saved entry is unavailable/)
  expect(screen.getByLabelText('Persona snapshot')).toHaveTextContent('Original instructions')
})

it('copies a saved persona into the node without a live library dependency', async () => {
  renderPicker({ mode: 'text', persona_text: 'Custom instructions' })
  fireEvent.click(screen.getByRole('button', { name: 'Manage personas / Save current' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Use Careful analyst' }))
  const snapshot = JSON.parse(screen.getByLabelText('Persona snapshot').textContent!)
  expect(snapshot.persona_text).toBe(saved.prompt)
  expect(snapshot.persona_sources).toEqual([{ id: saved.id, revision: 1, prompt_sha256: 'original-hash' }])
})

it('saves plain text with optional metadata and edits the reusable entry separately from the snapshot', async () => {
  vi.spyOn(personasApi, 'create').mockResolvedValue({ ...saved, prompt: 'Custom instructions' })
  vi.spyOn(personasApi, 'list').mockResolvedValue([saved])
  renderPicker({ mode: 'text', persona_text: 'Custom instructions' })
  fireEvent.click(screen.getByRole('button', { name: 'Manage personas / Save current' }))
  fireEvent.click(screen.getByRole('button', { name: 'Save to library' }))
  await waitFor(() => expect(personasApi.create).toHaveBeenCalledWith({ prompt: 'Custom instructions', name: undefined, description: null }))
  expect(screen.getByLabelText('Persona snapshot')).toHaveTextContent('Custom instructions')
  expect(JSON.parse(screen.getByLabelText('Persona snapshot').textContent!).persona_id).toBeUndefined()
})

it('edits and deletes a library record without changing a node already using it', async () => {
  const changed = { ...saved, prompt: 'Revised library instructions', revision: 2 }
  vi.spyOn(personasApi, 'update').mockResolvedValue(changed)
  vi.spyOn(personasApi, 'remove').mockResolvedValue(undefined)
  vi.spyOn(personasApi, 'list').mockResolvedValue([changed])
  renderPicker(configForSavedPersona(saved))
  fireEvent.click(screen.getByRole('button', { name: 'Manage personas / Save current' }))
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
  fireEvent.change(screen.getByLabelText('Instructions'), { target: { value: changed.prompt } })
  fireEvent.click(screen.getByRole('button', { name: 'Save library changes' }))
  await waitFor(() => expect(personasApi.update).toHaveBeenCalledWith(saved.id, {
    expected_revision: 1, name: saved.name, description: null, prompt: changed.prompt,
  }))
  expect(screen.getByLabelText('Persona snapshot')).toHaveTextContent(saved.prompt)
  fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
  fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }))
  await waitFor(() => expect(personasApi.remove).toHaveBeenCalledWith(saved.id, expect.anything()))
  expect(screen.getByLabelText('Persona snapshot')).toHaveTextContent(saved.prompt)
})

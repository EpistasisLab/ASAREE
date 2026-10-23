import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { personasApi } from '@/api/client'
import type { Persona } from '@/types/personas'
import { PERSONA_LIBRARY } from './personaLibrary'
import { PersonaBrowserPanel } from './PersonaBrowserPanel'

const saved: Persona = {
  id: 'saved', name: 'Careful analyst', description: 'Check the evidence', prompt: 'Think carefully',
  source_filename: 'careful.md', revision: 2, prompt_sha256: 'hash', traits: {}, created_at: '', updated_at: '',
}

const prompt = (id: string) => PERSONA_LIBRARY.find(entry => entry.id === id)!.prompt

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const pick = vi.fn()
  render(<QueryClientProvider client={client}><PersonaBrowserPanel onPick={pick} onBack={vi.fn()} onClose={vi.fn()} /></QueryClientProvider>)
  return pick
}

const confirmButton = () => screen.getByRole('button', { name: 'Add persona node' })

it('groups built-ins into Personas and Traits, and saved personas separately', async () => {
  vi.spyOn(personasApi, 'list').mockResolvedValue([saved])
  setup()
  const personas = screen.getByRole('region', { name: 'Personas' })
  const traits = screen.getByRole('region', { name: 'Traits' })
  expect(within(personas).getAllByRole('button')).toHaveLength(5)
  expect(within(traits).getAllByRole('button')).toHaveLength(10)
  expect(within(personas).getByRole('button', { name: /Analyst/ })).toBeInTheDocument()
  expect(within(traits).getByRole('button', { name: /High Openness/ })).toBeInTheDocument()
  const savedGroup = await screen.findByRole('region', { name: 'Saved' })
  expect(within(savedGroup).getByRole('button', { name: /Careful analyst/ })).toBeInTheDocument()
})

it('does not place a node until the selection is confirmed', async () => {
  vi.spyOn(personasApi, 'list').mockResolvedValue([saved])
  const pick = setup()
  expect(confirmButton()).toBeDisabled()
  fireEvent.click(await screen.findByRole('button', { name: /Careful analyst/ }))
  expect(pick).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: /Careful analyst/ })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(confirmButton())
  expect(pick).toHaveBeenCalledTimes(1)
  expect(pick).toHaveBeenCalledWith(expect.objectContaining({
    persona_ids: ['saved'], persona_text: 'Think carefully', persona_description: 'Check the evidence',
    persona_sources: [{ id: 'saved', revision: 2, prompt_sha256: 'hash' }],
  }))
})

it('combines several selections into one node in click order, with no cap', async () => {
  vi.spyOn(personasApi, 'list').mockResolvedValue([])
  const pick = setup()
  fireEvent.click(screen.getByRole('button', { name: /High Openness/ }))
  fireEvent.click(screen.getByRole('button', { name: /Low Agreeableness/ }))
  expect(screen.getByRole('button', { name: /High Openness/ })).toHaveTextContent('1')
  expect(screen.getByRole('button', { name: /Low Agreeableness/ })).toHaveTextContent('2')
  expect(screen.getByText(/2 selected .* High Openness \+ Low Agreeableness/)).toBeInTheDocument()
  fireEvent.click(confirmButton())
  expect(pick).toHaveBeenCalledWith(expect.objectContaining({
    persona_ids: ['05-high-openness', '12-low-agreeableness'],
    persona_label: 'High Openness + Low Agreeableness',
    persona_text: `${prompt('05-high-openness')}\n\n${prompt('12-low-agreeableness')}`,
  }))

  for (const name of [/Analyst/, /Critic/, /High Neuroticism/, /Low Extraversion/, /High Conscientiousness/]) fireEvent.click(screen.getByRole('button', { name }))
  fireEvent.click(confirmButton())
  expect(pick).toHaveBeenLastCalledWith(expect.objectContaining({
    persona_ids: ['05-high-openness', '12-low-agreeableness', '01-analyst', '03-critic', '13-high-neuroticism', '10-low-extraversion', '07-high-conscientiousness'],
  }))
})

it('treats Baseline as exclusive and lets Clear reset the selection', async () => {
  vi.spyOn(personasApi, 'list').mockResolvedValue([])
  const pick = setup()
  fireEvent.click(screen.getByRole('button', { name: /High Openness/ }))
  fireEvent.click(screen.getByRole('button', { name: /^Baseline/ }))
  expect(screen.getByRole('button', { name: /High Openness/ })).toHaveAttribute('aria-pressed', 'false')
  expect(screen.getByRole('button', { name: /^Baseline/ })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(screen.getByRole('button', { name: /Low Openness/ }))
  expect(screen.getByRole('button', { name: /^Baseline/ })).toHaveAttribute('aria-pressed', 'false')
  fireEvent.click(screen.getByRole('button', { name: /^Baseline/ }))
  fireEvent.click(confirmButton())
  expect(pick).toHaveBeenCalledWith(expect.objectContaining({ persona_text: '', persona_ids: ['00-baseline'] }))
  fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
  expect(confirmButton()).toBeDisabled()
  expect(screen.getByRole('button', { name: /^Baseline/ })).toHaveAttribute('aria-pressed', 'false')
})

it('uploads to the library without placing a node until the user selects and confirms it', async () => {
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
  expect(pick).not.toHaveBeenCalled()
  fireEvent.click(confirmButton())
  expect(pick).toHaveBeenCalledTimes(1)
})

it('keeps built-ins and upload available if the saved library fails', async () => {
  vi.spyOn(personasApi, 'list').mockRejectedValue(new Error('Offline'))
  const pick = setup()
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Could not load saved personas'))
  expect(screen.getByRole('button', { name: 'Upload persona (.md)' })).toBeEnabled()
  expect(screen.queryByRole('region', { name: 'Saved' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /^Baseline/ }))
  fireEvent.click(confirmButton())
  expect(pick).toHaveBeenCalledWith(expect.objectContaining({ persona_text: '', persona_ids: ['00-baseline'] }))
})

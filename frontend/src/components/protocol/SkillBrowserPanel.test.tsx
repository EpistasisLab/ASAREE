import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, skillsApi } from '@/api/client'
import { SkillBrowserPanel } from './SkillBrowserPanel'

vi.mock('./RegisterSkillDialog', () => ({ RegisterSkillDialog: () => null }))
afterEach(() => vi.restoreAllMocks())

it('explains retained usage and lets the user dismiss the deletion error', async () => {
  vi.spyOn(skillsApi, 'list').mockResolvedValue([{
    id: 'skill', name: 'checking', description: 'Check calculations', body: 'Instructions',
    frontmatter: {}, files: [], is_system: false, source_filename: null,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  }])
  const message = 'This skill is used by published version: Experiment A. Remove those references before deleting the skill.'
  const remove = vi.spyOn(skillsApi, 'remove').mockRejectedValue(new ApiError(409, message))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={client}>
    <SkillBrowserPanel onPick={vi.fn()} onBack={vi.fn()} onClose={vi.fn()} />
  </QueryClientProvider>)
  fireEvent.click(await screen.findByRole('button', { name: 'Delete checking' }))
  expect(screen.getByText(/cannot be deleted/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(message)
  expect(remove).toHaveBeenCalledExactlyOnceWith('skill')
  expect(screen.getByRole('button', { name: 'Delete checking' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss deletion error' }))
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  expect(screen.getByRole('button', { name: 'Delete checking' })).toBeInTheDocument()
  expect(remove).toHaveBeenCalledTimes(1)
})

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, skillsApi } from '@/api/client'
import { SkillBrowserPanel } from './SkillBrowserPanel'

vi.mock('./RegisterSkillDialog', () => ({ RegisterSkillDialog: () => null }))
afterEach(() => vi.restoreAllMocks())

it('explains retained usage when deleting a referenced skill is refused', async () => {
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
})

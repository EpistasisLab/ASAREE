import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { expect, it, vi } from 'vitest'
import { llmSettingsApi } from '@/api/client'
import { useSamplingPreview } from './useSamplingPreview'

vi.mock('@/api/client', () => ({ llmSettingsApi: { sampling: vi.fn() } }))

it('sends only the shared contract and keeps literal none distinct from unset', async () => {
  vi.mocked(llmSettingsApi.sampling).mockResolvedValue({
    temperature: 0, effort: 'none', temperature_source: 'advertised_default', effort_source: 'explicit',
    temperature_omission_reason: null, effort_omission_reason: null,
  })
  const client = new QueryClient()
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const caps = { id: 'model', label: 'Model', supports_temperature: true, supports_effort: true, default_temperature: 0,
    effort_levels: ['none', 'low'], default_effort: 'low' }
  const { result } = renderHook(() => useSamplingPreview(caps, 'model', null, 'none'), { wrapper })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(llmSettingsApi.sampling).toHaveBeenCalledWith({
    model: 'model', temperature: null, effort: 'none', capabilities: {
      supports_temperature: true, supports_effort: true, default_temperature: 0, effort_levels: ['none', 'low'],
      default_effort: 'low', temperature_effort_levels: null,
    },
  })
})

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { llmSettingsApi } from '@/api/client'
import { CreateCredentialDialog } from './CreateCredentialDialog'

describe('CreateCredentialDialog', () => {
  it('masks an Azure AI Foundry API key by default', async () => {
    vi.spyOn(llmSettingsApi, 'list').mockResolvedValue([
      {
        provider: 'azure_foundry',
        api_base: 'https://example.services.ai.azure.com',
        azure_project_endpoint: 'https://example.services.ai.azure.com/api/projects/example',
      },
    ])
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={client}>
        <CreateCredentialDialog open onOpenChange={vi.fn()} defaultProvider="azure_foundry" />
      </QueryClientProvider>,
    )

    const apiKey = await screen.findByPlaceholderText('**********')

    expect(apiKey).toHaveAttribute('type', 'password')
    expect(apiKey).toHaveAttribute('placeholder', '**********')
    expect(apiKey).toHaveValue('')
  })
})

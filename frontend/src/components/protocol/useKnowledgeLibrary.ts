import { useQuery } from '@tanstack/react-query'
import { okfApi } from '@/api/client'

export function useKnowledgeLibrary(enabled = true) {
  return useQuery({ queryKey: ['knowledge-factor-library'], enabled, queryFn: async () => {
    const [bundles, documents] = await Promise.all([okfApi.list(), okfApi.listDocuments()])
    return [...bundles.map((item) => ({ id: item.id, name: item.name })), ...documents.map((item) => ({ id: item.id, name: item.title || item.name }))]
  } })
}

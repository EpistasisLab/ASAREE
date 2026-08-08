export interface Dataset {
  id: string
  name: string
  train_path: string
  test_path: string
  train_sha256: string
  test_sha256: string
  target_column: string | null
  description: string | null
  dictionary_json: string | null
  created_at: string | null
}

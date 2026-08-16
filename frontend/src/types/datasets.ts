export interface Dataset {
  id: string
  name: string
  raw_path: string | null
  raw_sha256: string | null
  // Null until a split is actually produced (datasetsApi.quickSplit/
  // manualSplit) -- registration itself only stores the raw file, it never
  // splits (see RegisteredDataset's own comment in the backend model).
  train_path: string | null
  test_path: string | null
  train_sha256: string | null
  test_sha256: string | null
  target_column: string | null
  description: string | null
  dictionary_json: string | null
  created_at: string | null
}

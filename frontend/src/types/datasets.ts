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
  // How that split was produced (DatasetNodeInspector reads these out next to
  // the hashes). 'quick' = ASAREE's own splitter, so the three below describe
  // it; 'manual' = an already-split pair the user uploaded, so they're null
  // (ASAREE didn't compute it). All four null means either never split, or
  // split before these columns existed -- a permanent state for those rows,
  // not a loading gap.
  //
  // split_group_column is the column actually grouped on, not the one
  // requested: null means the split was stratified.
  split_method: string | null
  split_group_column: string | null
  split_test_size: number | null
  split_seed: number | null
  target_column: string | null
  description: string | null
  dictionary_json: string | null
  created_at: string | null
}

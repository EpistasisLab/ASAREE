import type { Dataset } from '@/types/datasets'
import type { DatasetNodeData } from '@/types/protocols'

// Not a node type -- a sentinel AddNodePanel carries so its "Datasets" entry
// drills into the dataset browser instead of creating a blank node, exactly
// as SKILL_BROWSE, OKF_BUNDLE_BROWSE and MCP_SERVER_BROWSE do. Which dataset
// you want is a question the static catalog can't answer: datasets are
// registered per user, at runtime, by uploading a file.
export const DATASET_BROWSE = 'datasets_browse'

// A Dataset node picked from the browser is created with that dataset already
// bound, the same way a Skill/OKF Bundle/MCP server node is -- picking it IS
// how you add the node, rather than dropping a blank node and then hunting
// for the dataset in its inspector.
//
// There's no picker in the inspector either, same as Skill -- it reads out
// the bound dataset (description, split state, data dictionary) and offers
// the actions that are the NODE's, not the binding's. Swapping which dataset
// a branch runs on means adding the node for that dataset, which keeps the
// canvas honest about what changed.
//
// dataset_name is cached onto the node so the card renders without a fetch;
// dataset_id is the only part a run reads (see _resolve_dataset_configs in
// services/protocol_execution.py).
export function nodeDataForDataset(dataset: Dataset): DatasetNodeData {
  return {
    label: dataset.name,
    config: { dataset_id: dataset.id, dataset_name: dataset.name, enabled: true },
  }
}

import type { OkfBundle } from '@/types/okf'
import type { OkfBundleNodeData } from '@/types/protocols'

// Not a node type -- a sentinel AddNodePanel carries so its "OKF Bundle" entry
// drills into the bundle browser instead of creating a node, exactly as
// SKILL_BROWSE and MCP_SERVER_BROWSE do. Which bundle you want is a question
// the static catalog can't answer: bundles are registered per user, at runtime,
// against directories on the server's disk.
export const OKF_BUNDLE_BROWSE = 'okf_bundle_browse'

// An OKF Bundle node is never created blank -- the bundle IS the node. Same
// model as Skill: picking one in the browser is how you add it, and there's no
// picker in the inspector to repoint it afterwards.
//
// server_name is the only field a run actually reads (_resolve_knowledge_config
// namespaces this bundle's tools against it); tool_names is cached here from
// the registration's own discovery so the graph is self-contained at run time,
// the same way McpToolNodeConfig caches its allow-list. The label defaults to
// the folder name rather than the generated okf-bundle-<hash> server name --
// the folder is what the user recognises.
export function nodeDataForBundle(bundle: OkfBundle): OkfBundleNodeData {
  const folder = bundle.path?.split('/').filter(Boolean).pop() ?? bundle.name
  return {
    label: folder,
    config: {
      bundle_id: bundle.id,
      server_name: bundle.name,
      bundle_path: bundle.path,
      bundle_label: folder,
      tool_names: bundle.tool_names,
      enabled: true,
    },
  }
}

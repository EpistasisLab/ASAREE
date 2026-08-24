import { FlaskConical, Wrench } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { McpServer } from '@/types/mcpServers'
import type { McpToolNodeData } from '@/types/protocols'

// The sentinel AddNodePanel emits instead of a node type when its "MCP
// Servers" entry is picked. ProtocolCanvas intercepts it and opens the
// server browser rather than creating a node -- which server you want is a
// question the node catalog can't answer, since the answer comes from the
// API.
export const MCP_SERVER_BROWSE = 'mcp_servers'

// Every node type in the MCP-tool family, mirroring services/
// protocol_execution.py's own _MCP_TOOL_NODE_TYPES. They all carry an
// identical McpToolNodeData; the type only records HOW the server was
// chosen (see PRESETS below).
export const MCP_TOOL_NODE_TYPES = ['mcp_tool', 'mcp_scikit_learn', 'mcp_client_tool']

// The node for a server the USER connected, rather than one the deployment
// had already registered: the MCP Servers browser pins a "Connect an MCP
// server" row above its list, which registers a stdio or streamable-HTTP
// connection (ConnectMcpServerDialog) and drops one of these on the canvas
// bound to it.
//
// A type of its own, even though it carries the same McpToolNodeData and
// runs through the same code path as `mcp_tool`, because the two answer
// different questions on a canvas you come back to a month later: an
// `mcp_tool` node points at infrastructure that was already there, while
// this one points at a connection that only exists because this protocol
// asked for it -- and whose endpoint is therefore part of the experiment's
// record. Its inspector shows that endpoint; the generic one has none to
// show.
export const MCP_CLIENT_TOOL_NODE_TYPE = 'mcp_client_tool'

export interface McpServerPreset {
  // The xyflow node type a node for this server is created as.
  nodeType: string
  // What the node is called on the canvas -- the server's product name
  // rather than its registered id ("Scikit-learn MCP", not
  // "scikit-learn-mcp").
  label: string
  description: string
  icon: LucideIcon
}

// Servers that get a NODE TYPE OF THEIR OWN, keyed by registered server
// name. A dedicated type is what lets the node stand for one specific
// server end to end: it's created with that server already bound, so its
// inspector drops the server dropdown entirely and is purely "which of this
// server's tools may the agent call".
//
// This is deliberately a small, curated map rather than a type per row in
// GET /mcp-servers. Anything not listed here still shows up in the browser
// and still works -- it just becomes a generic `mcp_tool` node (with the
// server pinned all the same, see presetForServer), because a node type
// only earns its own entry when there's a real name and identity to give
// it.
const PRESETS: Record<string, McpServerPreset> = {
  'scikit-learn-mcp': {
    nodeType: 'mcp_scikit_learn',
    label: 'Scikit-learn MCP',
    description: 'Profile a dataset, define a split, and fit logistic regression for AUC + metrics',
    icon: FlaskConical,
  },
}

// The preset for *server*, falling back to a generic MCP Tool node named
// after the server itself.
export function presetForServer(server: McpServer): McpServerPreset {
  return (
    PRESETS[server.name] ?? {
      nodeType: 'mcp_tool',
      label: server.name,
      description: `${server.capabilities?.tools?.length ?? 0} tools`,
      icon: Wrench,
    }
  )
}

// A node for *server*, with the server already bound and every one of its
// tools allowed. "All tools on" is the same friendly default the generic
// node's server dropdown applies (pickToolNamesForServer) -- an empty
// allow-list silently does nothing until the user discovers they have to
// switch each tool on one at a time.
export function nodeDataForServer(server: McpServer): McpToolNodeData {
  return {
    label: presetForServer(server).label,
    config: {
      server_id: server.id,
      server_name: server.name,
      tool_names: server.capabilities?.tools?.map((t) => t.name) ?? [],
      enabled: true,
    },
  }
}

// Same data as nodeDataForServer, but labelled by the registered name rather
// than a preset: a just-connected server has no preset by definition, and the
// name is what the user themselves just typed.
export function nodeDataForClientTool(server: McpServer): McpToolNodeData {
  return {
    label: server.name,
    config: {
      server_id: server.id,
      server_name: server.name,
      tool_names: server.capabilities?.tools?.map((t) => t.name) ?? [],
      enabled: true,
    },
  }
}

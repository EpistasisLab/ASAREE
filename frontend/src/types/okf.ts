// OKF = Open Knowledge Format: a directory of Markdown "concept" files with
// YAML frontmatter that agents read and continuously write back to. A user
// registers one such directory (on the SERVER's disk -- see below) and the
// canvas's Knowledge connector wires it into an agent.
//
// Mirrors asaree.api.okf's response models.

// One sub-directory in the server-side folder picker.
export interface OkfDirectoryEntry {
  name: string
  // Relative to the server's configured bundle root -- what you send back to
  // browse into it or register it. Never absolute: the client doesn't get to
  // name paths the server didn't offer.
  path: string
  // Whether it contains OKF's reserved root files (index.md / log.md). A hint
  // for the picker only -- an empty directory is a perfectly valid place to
  // start a new bundle, so this never blocks registration.
  is_bundle: boolean
}

export interface OkfBrowseResponse {
  path: string
  // Absolute, display-only. Shown because the whole question this screen
  // answers is "is the server looking at the same disk I am?".
  absolute_path: string
  // null at the root -- there is no "up" out of the jail.
  parent: string | null
  entries: OkfDirectoryEntry[]
}

// A registered bundle. Really an MCP server row: one server process per
// bundle, since the OKF server jails itself to a single directory read from
// its own environment.
export interface OkfBundle {
  id: string
  // The generated okf-bundle-* server name -- what a run's tool allow-list is
  // namespaced against.
  name: string
  // Absolute path on the server; null only if the stored command was
  // hand-edited into something unparseable.
  path: string | null
  // The MCP connection status -- "error" here means the server failed to
  // spawn, and the bundle will contribute no tools to a run.
  status: string
  error_message: string | null
  // Bare tool names discovered at registration.
  tool_names: string[]
  created_at: string
}

// One UPLOADED single-concept document (POST /okf/documents). The other way
// to fill the Knowledge connector: a bundle is a folder the SERVER already
// has and the user points at, a document is one .md file from the user's own
// machine that ASAREE stores -- the same distinction, and the same upload
// flow, as registering an Agent Skill.
//
// It's still a real OKF bundle underneath (one directory, one concept, its
// own MCP server), so the agent gets the same read/write concept tools and
// can edit it during a run. That's why title/description/tags are re-read
// from the stored file on every request rather than cached at upload -- see
// DocumentResponse in api/okf.py.
export interface OkfDocument {
  id: string
  // The generated okf-doc-* server name -- what a run's tool allow-list is
  // namespaced against.
  name: string
  // From the file's own YAML frontmatter. `title` is required at upload, so
  // null here means the stored file has since been rewritten into something
  // unparseable (or lost) -- the row still lists so it can be deleted.
  title: string | null
  description: string | null
  // The spec's `type` field, renamed to dodge the JS reserved-ish `type`
  // clash on a node config -- matches DocumentResponse.concept_type.
  concept_type: string | null
  tags: string[]
  // Absolute path to the stored .md on the server; null if the file is gone.
  path: string | null
  status: string
  error_message: string | null
  tool_names: string[]
  created_at: string
}

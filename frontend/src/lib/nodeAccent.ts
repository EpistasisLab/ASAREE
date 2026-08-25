// One node kind, one color -- the single source of truth for what hue a
// protocol-canvas node is drawn in, read by both the node card and its
// inspector so the two can never drift.
//
// This replaces hashToChartHue(nodeKind) for canvas nodes. Hashing was fine
// while the palette was wider than the set of things being colored, but there
// are thirteen node kinds and five --chart-* hues, so repeats weren't a risk,
// they were arithmetic -- and they landed on exactly the pairs a reader
// confuses: Skill and AI, Dataset and Knowledge, Pattern and Script all came
// out the same color. Color on this canvas answers "what kind of node is
// this", which is a closed, small, slow-changing set, so it gets a table.
// hashToChartHue stays for the open-ended cases it was written for (a model
// name, a factor name -- where no table could stay current).
//
// The table is not a fresh palette, though: each of the five hash buckets
// keeps ONE kind on the --chart-* hue it already had, and only its bucket-mates
// move to a --node-* slot (see index.css). Recoloring every node to fix a
// collision would make a canvas someone already knows unrecognizable, so the
// kind that anchors each bucket is the one whose color is most load-bearing --
// the agent (the canvas's protagonist), the dataset and the reason+act pattern
// (the two collisions were reported against them), the critic gate (red, and
// it's the only node whose job is to stop a run), and the LLM family.
const NODE_ACCENTS: Record<string, string> = {
  agent: 'var(--chart-3)',
  dataset: 'var(--chart-2)',
  pattern_reason_act: 'var(--chart-1)',
  critic_gate: 'var(--chart-5)',
  // One hue for the whole LLM family (llm_anthropic/llm_openai/...), which is
  // a change: it used to hash the PROVIDER, giving each its own color. That
  // read as five unrelated node kinds, and it's what put an AI node on Skill's
  // color. Providers stay distinguishable by icon and label (LlmNode's
  // PROVIDER_META) -- color here means "this is the model", not "which
  // vendor". --chart-4 because that's what the hash gave the Azure/local
  // providers, so the most common AI nodes keep the color they had.
  llm: 'var(--chart-4)',
  // The movers. Adjacent slots go to related kinds on purpose (the two MCP
  // kinds, the two OKF kinds): where two colors are closest, the things they
  // mark are most alike, so a mix-up costs the least.
  skill: 'var(--node-1)',
  script: 'var(--node-2)',
  pattern_single_agent_baseline: 'var(--node-3)',
  mcp_tool: 'var(--node-4)',
  mcp_client_tool: 'var(--node-5)',
  memory: 'var(--node-6)',
  okf_document: 'var(--node-7)',
  okf_bundle: 'var(--node-8)',
}

/** The accent color for a canvas node kind — pass the same key the node card
 * and its inspector both use (`'skill'`, `'okf_bundle'`, `'llm'`). An unknown
 * kind falls back to `--primary` rather than to a hashed hue: a new node type
 * showing up in the theme's own accent is a visible prompt to give it a slot
 * here, where a plausible-looking hash collision would just look intentional. */
export function nodeAccent(kind: string): string {
  return NODE_ACCENTS[kind] ?? 'var(--primary)'
}

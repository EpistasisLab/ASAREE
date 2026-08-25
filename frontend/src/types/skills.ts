// A registered Agent Skill -- a skill *directory*, parsed into its parts.
// The format's own required frontmatter is `name` (lowercase kebab-case, <=64
// chars) and `description` (what the skill does AND when to use it, <=1024);
// `body` is everything after the frontmatter, the instructions themselves.
// Enforced server-side by Motoro's skill_service, which is also what ASAREE's
// /api/skills routes are a thin layer over -- these types just mirror its
// SkillResponse.
export interface Skill {
  id: string
  name: string
  description: string
  body: string
  is_system: boolean
  // The folder the skill was uploaded from, or the .md file for a single-file
  // upload. Shown back to the user, never used to resolve anything.
  source_filename: string | null
  // Paths of the bundled level-3 files, relative to the skill directory --
  // `FORMS.md`, `references/schema.md`. Paths only, never contents: the agent
  // reads those through Motoro's `read_skill_file` pseudo-tool, and a list
  // endpoint carrying every byte of every bundle would be the wrong trade.
  files: string[]
  created_at: string
  updated_at: string
}

export interface SkillListResponse {
  items: Skill[]
  total: number
}

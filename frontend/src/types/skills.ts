// A registered Agent Skill -- one SKILL.md document, parsed into its parts.
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
  source_filename: string | null
  created_at: string
  updated_at: string
}

export interface SkillListResponse {
  items: Skill[]
  total: number
}

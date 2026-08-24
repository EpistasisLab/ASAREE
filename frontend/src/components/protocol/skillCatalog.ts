import type { Skill } from '@/types/skills'
import type { SkillNodeData } from '@/types/protocols'

// Not a node type -- a sentinel AddNodePanel carries so its "Skill" entry
// can drill into the skill browser instead of creating a node, exactly as
// MCP_SERVER_BROWSE does for servers. Which skill you want is a question the
// static catalog can't answer, since the answer comes from the API.
export const SKILL_BROWSE = 'skills_browse'

// A Skill node is never created blank: the skill IS the choice, so picking
// one in the browser is how you add the node, rather than adding an empty
// node and then hunting for the skill in a dropdown inside its inspector.
// There is no picker in the inspector either -- a node IS one skill, and
// repointing it afterwards would contradict that; you add a second Skill
// node instead. Same model as the server-dedicated MCP node types.
//
// name/description are cached onto the node so the card and inspector header
// render without a fetch; the id is the only part a run reads (see
// _resolve_skill_config in services/protocol_execution.py), so a later edit
// to the skill itself is picked up automatically.
export function nodeDataForSkill(skill: Skill): SkillNodeData {
  return {
    label: skill.name,
    config: {
      skill_id: skill.id,
      skill_name: skill.name,
      skill_description: skill.description,
      enabled: true,
    },
  }
}

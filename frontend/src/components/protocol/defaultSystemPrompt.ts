// Mirrors services.protocol_execution's own _default_system_prompt exactly
// (same "You are {label or placeholder}." formula) -- shown here as the
// System Prompt field's own placeholder so a user can see, without reading
// backend code, exactly what a run will actually use when the field is left
// blank. `placeholder` matches the node's own canvas-card fallback text when
// unlabeled (AgentNode.tsx's "Agent", CriticGateNode.tsx's "Critic Gate").
export function defaultSystemPrompt(label: string | undefined, placeholder: string): string {
  return `You are ${label || placeholder}.`
}

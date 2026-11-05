/** Presentation only. Stored names remain keys for bindings and workspace identity. */
export function displayFactorName(name: string): string {
  return name.replace(/\s*:\s*/g, ' › ')
}

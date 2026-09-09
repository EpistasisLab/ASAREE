import type { DesignSpec } from '@/types/experiments'

/** Which prompt format an experiment's agents are assembled under.
 *
 * Mirrors `services/prompt_contract.py`. Two contracts, not a version ladder:
 * the legacy one is frozen forever because published numbers were produced
 * under its exact text, and the current one evolves in place. An experiment is
 * stamped with one at creation and never moved, which is why nothing here
 * writes -- this module only reads and names.
 */
export const LEGACY_PROMPT_CONTRACT = 1
export const CURRENT_PROMPT_CONTRACT = 2

/** The recorded version, defaulting the way the backend does: an absent or
 *  unusable value is the legacy contract, because everything created before
 *  contracts existed ran under it. */
export function promptContractVersion(designSpec: DesignSpec | null | undefined): number {
  const raw = designSpec?.prompt_contract_version
  return typeof raw === 'number' && Number.isInteger(raw) && raw > 0 ? raw : LEGACY_PROMPT_CONTRACT
}

/** What a contract version means, in one line each.
 *
 * The difference users actually hit is who decides what reaches an agent: the
 * wiring (legacy) or the prompt (current). A version number alone answers
 * nothing, so it never appears in the UI without this next to it.
 */
export function promptContractSummary(version: number): string {
  if (version === LEGACY_PROMPT_CONTRACT) {
    return 'Every upstream node’s output is appended to the receiving agent’s prompt automatically, whether or not the prompt asks for it. Frozen so that already-published results keep the prompt they were produced under.'
  }
  if (version === CURRENT_PROMPT_CONTRACT) {
    return 'An edge makes an upstream output available; a {{reference}} in the prompt is what passes it along. Nothing reaches an agent that its own prompt did not ask for.'
  }
  // An experiment stamped by a newer build than this frontend. Better to say so
  // than to describe the wrong contract.
  return 'This experiment is pinned to a prompt format this version of the app does not know about.'
}

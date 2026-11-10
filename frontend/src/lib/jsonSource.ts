import { applyEdits, format, visit } from 'jsonc-parser'

/** Format whitespace without reserializing numbers, strings, or duplicate keys. */
export function formatJsonSource(source: string): { text: string; formatted: boolean } {
  try {
    let valid = true
    visit(source, { onError: () => { valid = false } }, { disallowComments: true, allowTrailingComma: false })
    if (!valid) return { text: source, formatted: false }
    return { text: applyEdits(source, format(source, undefined, { tabSize: 2, insertSpaces: true, eol: '\n' })), formatted: true }
  } catch {
    return { text: source, formatted: false }
  }
}

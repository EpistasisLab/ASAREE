import CodeMirror, { EditorView } from '@uiw/react-codemirror'
import { python } from '@codemirror/lang-python'
import { syntaxHighlighting } from '@codemirror/language'
import { oneDarkHighlightStyle } from '@codemirror/theme-one-dark'

// A dark theme built from this app's own CSS custom properties (index.css's
// .dark block) for the editor's CHROME (background, gutter, active line,
// selection, cursor) -- matches every other input's own bg-input look
// instead of CodeMirror's default light-gray frame. Token COLORS are a
// separate concern: One Dark's own oneDarkHighlightStyle (imported by
// itself, not its accompanying background theme) is used verbatim for
// those -- a code editor's multi-hue token coloring is its own established
// visual language, not something this app's single-accent color convention
// (CLAUDE.md's "Color -- meaningful variation, not decoration") is meant to
// constrain, the same way a syntax-highlighted code block in a chat UI
// doesn't reskin its colors to match the surrounding chrome either.
const editorTheme = EditorView.theme(
  {
    '&': {
      backgroundColor: 'var(--input)',
      color: 'var(--foreground)',
      fontSize: '0.75rem',
    },
    '&.cm-focused': {
      outline: 'none',
    },
    '.cm-content': {
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      caretColor: 'var(--primary)',
      padding: '0.5rem 0',
    },
    '.cm-gutters': {
      backgroundColor: 'transparent',
      color: 'var(--muted-foreground)',
      border: 'none',
    },
    '.cm-activeLine': {
      backgroundColor: 'color-mix(in oklch, var(--primary), transparent 93%)',
    },
    '.cm-activeLineGutter': {
      backgroundColor: 'transparent',
    },
    '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': {
      backgroundColor: 'color-mix(in oklch, var(--primary), transparent 75%) !important',
    },
    '.cm-cursor': {
      borderLeftColor: 'var(--primary)',
    },
  },
  { dark: true },
)

// Python-only for v1 (see ScriptNodeData's own comment in types/protocols.ts)
// -- a `language` prop isn't worth adding until a second language actually
// exists. The outer wrapper carries the same rounded-lg/border-input/
// focus-ring look every other field in this app has (Textarea, Input) via
// `focus-within` rather than CodeMirror's own theme, since the real focus
// target is the contenteditable region deep inside, not this wrapper.
export function PythonCodeEditor({
  value,
  onChange,
  rows = 16,
}: {
  value: string
  onChange: (value: string) => void
  rows?: number
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-input transition-colors focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50">
      <CodeMirror
        value={value}
        onChange={onChange}
        theme={editorTheme}
        height={`${rows * 1.35}em`}
        extensions={[python(), syntaxHighlighting(oneDarkHighlightStyle)]}
        basicSetup={{ foldGutter: false, highlightActiveLineGutter: false }}
      />
    </div>
  )
}

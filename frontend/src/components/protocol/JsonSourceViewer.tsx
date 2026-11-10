import { useMemo, useState } from 'react'
import CodeMirror, { EditorView } from '@uiw/react-codemirror'
import { json } from '@codemirror/lang-json'
import { syntaxHighlighting } from '@codemirror/language'
import { oneDarkHighlightStyle } from '@codemirror/theme-one-dark'
import { Button } from '@/components/ui/button'
import { formatJsonSource } from '@/lib/jsonSource'

const viewerTheme = EditorView.theme({
  '&': { backgroundColor: 'var(--background)', color: 'var(--foreground)', fontSize: '0.75rem' },
  '.cm-content': { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' },
  '.cm-gutters': { backgroundColor: 'var(--muted)', color: 'var(--muted-foreground)', border: 'none' },
  '&.cm-focused': { outline: 'none' },
}, { dark: true })

const extensions = [json(), syntaxHighlighting(oneDarkHighlightStyle), EditorView.lineWrapping,
  EditorView.contentAttributes.of({ role: 'textbox', 'aria-label': 'JSON source', 'aria-readonly': 'true', tabindex: '0' })]

export function JsonSourceViewer({ source }: { source: string }) {
  const formatted = useMemo(() => formatJsonSource(source), [source])
  const [original, setOriginal] = useState(false)
  return <div className="min-h-0 overflow-hidden rounded-lg border">
    <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-muted/30 px-3 py-2">
      <p className="text-xs text-muted-foreground">Read-only JSON · expand or collapse sections in the gutter</p>
      {formatted.formatted && <div className="flex gap-1">
        <Button size="xs" variant={original ? 'ghost' : 'secondary'} aria-pressed={!original} onClick={() => setOriginal(false)}>Formatted</Button>
        <Button size="xs" variant={original ? 'secondary' : 'ghost'} aria-pressed={original} onClick={() => setOriginal(true)}>Original</Button>
      </div>}
    </div>
    {!formatted.formatted && <p className="border-b px-3 py-2 text-xs text-amber-400">Could not format this file as JSON. Showing the original text.</p>}
    <CodeMirror value={original ? source : formatted.text} theme={viewerTheme} extensions={extensions}
      readOnly editable={false} maxHeight="55svh" basicSetup={{ highlightActiveLine: false, highlightActiveLineGutter: false }} />
  </div>
}

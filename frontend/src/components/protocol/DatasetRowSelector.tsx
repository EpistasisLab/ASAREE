import { Input } from '@/components/ui/input'

export function DatasetRowSelector({ rowIndex, onChange, rowCount, disabled = false }: { rowIndex: number; onChange: (zeroBased: number) => void; rowCount: number; disabled?: boolean }) {
  const valid = Number.isInteger(rowIndex) && rowIndex >= 0 && rowIndex < rowCount
  return <label className="flex items-center gap-2 text-xs">Source row<Input type="number" className="w-24 font-mono" aria-label="Source row" aria-invalid={!valid} min={1} max={rowCount} value={rowIndex + 1} disabled={disabled || rowCount < 1} onChange={event => onChange(event.target.value === '' ? -1 : Number(event.target.value) - 1)} /><span className="font-mono text-muted-foreground">of {rowCount}</span></label>
}

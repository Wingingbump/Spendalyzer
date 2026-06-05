import { RANGE_OPTIONS } from '../context/FilterContext'

/**
 * Compact, self-contained time-range control for a single view. Owns the custom
 * from/to date inputs. Pages keep their own range state and default, so range is
 * contextual rather than a global mode.
 */
export default function RangeSelect({
  value,
  onChange,
}: {
  value: string
  onChange: (v: string) => void
}) {
  const isCustom = value.startsWith('custom:') || value === 'custom'
  const today = new Date().toISOString().slice(0, 10)
  const thirtyDaysAgo = new Date(Date.now() - 30 * 86400_000).toISOString().slice(0, 10)
  const parts = value.startsWith('custom:') ? value.split(':') : []
  const customStart = parts[1] ?? thirtyDaysAgo
  const customEnd = parts[2] ?? today

  const fieldStyle = {
    fontSize: 12,
    padding: '5px 8px',
    borderRadius: 6,
    background: 'var(--color-surface)',
    border: '1px solid var(--color-border)',
    color: 'var(--color-text-primary)',
  } as const

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <select
        value={isCustom ? 'custom' : value}
        onChange={(e) =>
          onChange(e.target.value === 'custom' ? `custom:${thirtyDaysAgo}:${today}` : e.target.value)
        }
        title="Time range"
        style={{ ...fieldStyle, cursor: 'pointer' }}
      >
        {RANGE_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      {isCustom && (
        <>
          <input
            type="date"
            value={customStart}
            max={customEnd}
            onChange={(e) => onChange(`custom:${e.target.value}:${customEnd}`)}
            style={{ ...fieldStyle, fontSize: 11 }}
          />
          <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>→</span>
          <input
            type="date"
            value={customEnd}
            min={customStart}
            max={today}
            onChange={(e) => onChange(`custom:${customStart}:${e.target.value}`)}
            style={{ ...fieldStyle, fontSize: 11 }}
          />
        </>
      )}
    </div>
  )
}

import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { TrendingUp, Zap, RotateCcw, DollarSign, AlertTriangle, ChevronLeft, ChevronRight, X } from 'lucide-react'
import { nudgesApi, type Nudge } from '../lib/api'

// ── Icon + colour per nudge type ─────────────────────────────────────────────

const TYPE_META: Record<Nudge['type'], { icon: React.ElementType; label: string }> = {
  category_spike:    { icon: TrendingUp,    label: 'Spending spike' },
  monthly_pace:      { icon: Zap,           label: 'Monthly pace' },
  new_recurring:     { icon: RotateCcw,     label: 'New recurring' },
  price_change:      { icon: DollarSign,    label: 'Price change' },
  large_transaction: { icon: AlertTriangle, label: 'Unusual charge' },
}

const SEVERITY_BG: Record<Nudge['severity'], string> = {
  info:    'var(--color-accent-soft)',
  warning: 'rgba(224,115,64,0.10)',
  alert:   'rgba(239,68,68,0.10)',
}

const SEVERITY_BORDER: Record<Nudge['severity'], string> = {
  info:    'color-mix(in srgb, var(--color-accent) 20%, transparent)',
  warning: 'rgba(224,115,64,0.30)',
  alert:   'rgba(239,68,68,0.30)',
}

const SEVERITY_ICON: Record<Nudge['severity'], string> = {
  info:    'var(--color-accent)',
  warning: '#e07340',
  alert:   '#ef4444',
}

const SEVERITY_LABEL: Record<Nudge['severity'], string> = {
  info:    'Info',
  warning: 'Heads up',
  alert:   'Alert',
}

// ── Banner ────────────────────────────────────────────────────────────────────

export default function NudgesFeed() {
  const qc = useQueryClient()
  const [index, setIndex] = useState(0)

  const { data: nudges = [], isLoading } = useQuery({
    queryKey: ['nudges'],
    queryFn: nudgesApi.list,
    staleTime: 60_000,
  })

  // Mark all as read when feed mounts
  const markRead = useMutation({
    mutationFn: nudgesApi.markRead,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['nudges'] }),
  })

  useEffect(() => {
    if (nudges.some((n) => !n.read)) markRead.mutate()
  }, [nudges.length]) // eslint-disable-line react-hooks/exhaustive-deps

  // Keep index in bounds when a nudge is dismissed
  useEffect(() => {
    if (index >= nudges.length && nudges.length > 0) setIndex(nudges.length - 1)
  }, [nudges.length, index])

  const dismiss = useMutation({
    mutationFn: (id: number) => nudgesApi.dismiss(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['nudges'] }),
  })

  if (isLoading || nudges.length === 0) return null

  const nudge = nudges[index]
  const meta = TYPE_META[nudge.type]
  const Icon = meta.icon
  const total = nudges.length

  const prev = () => setIndex((i) => (i - 1 + total) % total)
  const next = () => setIndex((i) => (i + 1) % total)

  return (
    <div
      className="rounded-xl px-4 py-3 flex items-center gap-3"
      style={{
        background: SEVERITY_BG[nudge.severity],
        border: `1px solid ${SEVERITY_BORDER[nudge.severity]}`,
        minHeight: 64,
      }}
    >
      {/* Icon */}
      <div
        className="flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center"
        style={{
          background: `${SEVERITY_ICON[nudge.severity]}18`,
          border: `1px solid ${SEVERITY_ICON[nudge.severity]}30`,
        }}
      >
        <Icon size={14} style={{ color: SEVERITY_ICON[nudge.severity] }} />
      </div>

      {/* Text */}
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2">
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              textTransform: 'uppercase',
              letterSpacing: '0.07em',
              color: SEVERITY_ICON[nudge.severity],
              flexShrink: 0,
            }}
          >
            {SEVERITY_LABEL[nudge.severity]}
          </span>
          <span
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: 'var(--color-text-primary)',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {nudge.title}
          </span>
        </div>
        <p
          style={{
            fontSize: 12,
            color: 'var(--color-text-secondary)',
            lineHeight: 1.5,
            marginTop: 1,
          }}
        >
          {nudge.body}
        </p>
      </div>

      {/* Navigation + counter */}
      {total > 1 && (
        <div className="flex items-center gap-1 flex-shrink-0">
          <button
            onClick={prev}
            className="rounded-md flex items-center justify-center hover:opacity-70 transition-opacity"
            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 4, color: 'var(--color-text-muted)' }}
          >
            <ChevronLeft size={14} />
          </button>
          <span style={{ fontSize: 11, color: 'var(--color-text-muted)', minWidth: 28, textAlign: 'center' }}>
            {index + 1}/{total}
          </span>
          <button
            onClick={next}
            className="rounded-md flex items-center justify-center hover:opacity-70 transition-opacity"
            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 4, color: 'var(--color-text-muted)' }}
          >
            <ChevronRight size={14} />
          </button>
        </div>
      )}

      {/* Dismiss current */}
      <button
        onClick={() => dismiss.mutate(nudge.id)}
        title="Dismiss"
        className="flex-shrink-0 hover:opacity-70 transition-opacity"
        style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 4, color: 'var(--color-text-muted)' }}
      >
        <X size={14} />
      </button>
    </div>
  )
}

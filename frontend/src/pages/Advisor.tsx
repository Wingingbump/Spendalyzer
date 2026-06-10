import React, { useState, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Send, Bot, Loader2, Target, Pencil, Check, X, ThumbsUp, ThumbsDown, RotateCcw } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { advisorApi } from '../lib/api'
import type { Goal, AdviceRecord, UserFinancialProfile } from '../lib/api'
import { formatCurrency } from '../lib/utils'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Action {
  label: string
  message: string
}

interface Message {
  role: 'user' | 'advisor'
  content: string
  advice_id?: number
  options?: string[]   // onboarding quick-pick choices
  actions?: Action[]   // post-response actionable suggestions
}

// ── Constants ─────────────────────────────────────────────────────────────────

const WELCOME =
  "Hi! I'm your personal financial advisor. I can review your transactions, analyze your spending patterns, check progress on your goals, or answer any financial question. What's on your mind?"

const ONBOARD_WELCOME =
  "Hi! I'm your personal financial advisor. Before I can give you advice that's actually relevant to you, I'd love to get to know your situation a little — it only takes a few minutes. Mind if I ask you a few questions to start?"

const QUICK_PROMPTS = [
  { label: 'Review this month', q: "Review my spending this month and tell me how I'm doing." },
  { label: 'Check my goals', q: 'How am I tracking toward my financial goals?' },
  { label: 'Spending habits', q: 'Based on my transactions, what spending habits should I be aware of?' },
  { label: 'Saving tips', q: 'What are some concrete ways I could increase my savings rate based on what you see in my data?' },
]

const LIFE_STAGE_LABELS: Record<string, string> = {
  student: 'Student',
  early_career: 'Early Career',
  mid_career: 'Mid Career',
  pre_retirement: 'Pre-Retirement',
  retirement: 'Retirement',
}

const RISK_LABELS: Record<string, string> = {
  conservative: 'Conservative',
  moderate: 'Moderate',
  aggressive: 'Aggressive',
}

const STYLE_LABELS: Record<string, string> = {
  direct: 'Direct',
  detailed: 'Detailed',
  encouraging: 'Encouraging',
  analytical: 'Analytical',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function historyToMessages(records: AdviceRecord[]): Message[] {
  const messages: Message[] = []
  for (const r of [...records].reverse()) {
    if (r.user_message) {
      messages.push({ role: 'user', content: r.user_message })
    }
    messages.push({ role: 'advisor', content: r.response_text, advice_id: r.id })
  }
  return messages
}

// ── GoalChip ──────────────────────────────────────────────────────────────────

function GoalChip({ goal }: { goal: Goal }) {
  const pct = goal.target_amount
    ? Math.min((goal.current_amount / goal.target_amount) * 100, 100)
    : null
  const daysLeft = goal.deadline
    ? Math.ceil((new Date(goal.deadline).getTime() - Date.now()) / 86_400_000)
    : null

  return (
    <div
      className="flex-shrink-0 rounded-xl px-3 py-2"
      style={{
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        width: 160,
      }}
    >
      <div className="flex items-center gap-1.5 mb-1.5">
        <Target size={11} style={{ color: 'var(--color-accent)', flexShrink: 0 }} />
        <p style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-primary)', lineHeight: 1.35 }}>
          {goal.title}
        </p>
      </div>
      {pct !== null && goal.target_amount !== null && (
        <>
          <div style={{ height: 3, borderRadius: 2, background: 'var(--color-border)', marginBottom: 4 }}>
            <div
              style={{
                height: 3,
                borderRadius: 2,
                width: `${pct}%`,
                background: 'var(--color-accent)',
                transition: 'width 0.3s',
              }}
            />
          </div>
          <div className="flex justify-between items-center">
            <span style={{ fontSize: 10, color: 'var(--color-text-muted)', fontFamily: 'monospace' }}>
              {formatCurrency(goal.current_amount)}
            </span>
            <span style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>{pct.toFixed(0)}%</span>
          </div>
        </>
      )}
      {daysLeft !== null && (
        <p style={{ fontSize: 10, color: daysLeft < 30 ? '#e8c17a' : 'var(--color-text-muted)', marginTop: 2 }}>
          {daysLeft > 0 ? `${daysLeft}d left` : 'Deadline passed'}
        </p>
      )}
    </div>
  )
}

// ── ProfilePanel ──────────────────────────────────────────────────────────────

// Colors per field — subtle tinted badges
const BADGE_COLORS = {
  life_stage:          { bg: 'rgba(139, 92, 246, 0.12)', border: 'rgba(139, 92, 246, 0.35)', text: '#a78bfa', dot: '#8b5cf6' },
  income_estimate:     { bg: 'rgba(34, 197, 94, 0.12)',  border: 'rgba(34, 197, 94, 0.35)',  text: '#4ade80', dot: '#22c55e' },
  risk_tolerance:      { bg: 'rgba(249, 115, 22, 0.12)', border: 'rgba(249, 115, 22, 0.35)', text: '#fb923c', dot: '#f97316' },
  communication_style: { bg: 'rgba(20, 184, 166, 0.12)', border: 'rgba(20, 184, 166, 0.35)', text: '#2dd4bf', dot: '#14b8a6' },
} as const

type BadgeField = keyof typeof BADGE_COLORS

function ProfileBadge({ field, label, value }: { field: BadgeField; label: string; value: string }) {
  const c = BADGE_COLORS[field]
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        fontSize: 11,
        fontWeight: 500,
        padding: '3px 9px',
        borderRadius: 20,
        background: c.bg,
        border: `1px solid ${c.border}`,
        whiteSpace: 'nowrap',
      }}
    >
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: c.dot, flexShrink: 0 }} />
      <span style={{ color: 'var(--color-text-muted)', fontWeight: 400 }}>{label}</span>
      <span style={{ color: c.text }}>{value}</span>
    </span>
  )
}

function ProfileField({
  label,
  color,
  children,
}: {
  label: string
  color: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1">
      <span style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em', color }}>{label}</span>
      {children}
    </div>
  )
}

function ProfilePanel({
  profile,
  onSaved,
}: {
  profile: UserFinancialProfile
  onSaved: (updated: UserFinancialProfile) => void
}) {
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({
    life_stage: profile.life_stage ?? '',
    risk_tolerance: profile.risk_tolerance ?? '',
    income_estimate: profile.income_estimate != null ? String(Math.round(profile.income_estimate)) : '',
    communication_style: profile.communication_style ?? '',
  })

  const handleSave = async () => {
    setSaving(true)
    try {
      const updates = {
        life_stage: form.life_stage || undefined,
        risk_tolerance: form.risk_tolerance || undefined,
        income_estimate: form.income_estimate ? parseFloat(form.income_estimate) : undefined,
        communication_style: form.communication_style || undefined,
      }
      await advisorApi.updateProfile(updates)
      onSaved({ ...profile, ...updates })
      setEditing(false)
    } finally {
      setSaving(false)
    }
  }

  const handleCancel = () => {
    setForm({
      life_stage: profile.life_stage ?? '',
      risk_tolerance: profile.risk_tolerance ?? '',
      income_estimate: profile.income_estimate != null ? String(Math.round(profile.income_estimate)) : '',
      communication_style: profile.communication_style ?? '',
    })
    setEditing(false)
  }

  const selectStyle: React.CSSProperties = {
    fontSize: 12,
    padding: '4px 8px',
    borderRadius: 6,
    background: 'var(--color-surface)',
    border: '1px solid var(--color-border)',
    color: 'var(--color-text-primary)',
    outline: 'none',
    width: '100%',
  }

  if (editing) {
    return (
      <div
        className="rounded-xl p-3"
        style={{ background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.07)' }}
      >
        <p style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 10 }}>
          Edit your profile
        </p>
        <div className="grid gap-3" style={{ gridTemplateColumns: '1fr 1fr' }}>
          <ProfileField label="Life Stage" color={BADGE_COLORS.life_stage.dot}>
            <select value={form.life_stage} onChange={(e) => setForm((f) => ({ ...f, life_stage: e.target.value }))} style={{ ...selectStyle, borderColor: BADGE_COLORS.life_stage.border }}>
              <option value="">Select…</option>
              {Object.entries(LIFE_STAGE_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </ProfileField>

          <ProfileField label="Annual Income" color={BADGE_COLORS.income_estimate.dot}>
            <input
              type="number"
              placeholder="e.g. 75000"
              value={form.income_estimate}
              onChange={(e) => setForm((f) => ({ ...f, income_estimate: e.target.value }))}
              style={{ ...selectStyle, borderColor: BADGE_COLORS.income_estimate.border }}
            />
          </ProfileField>

          <ProfileField label="Risk Tolerance" color={BADGE_COLORS.risk_tolerance.dot}>
            <select value={form.risk_tolerance} onChange={(e) => setForm((f) => ({ ...f, risk_tolerance: e.target.value }))} style={{ ...selectStyle, borderColor: BADGE_COLORS.risk_tolerance.border }}>
              <option value="">Select…</option>
              {Object.entries(RISK_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </ProfileField>

          <ProfileField label="Advisor Style" color={BADGE_COLORS.communication_style.dot}>
            <select value={form.communication_style} onChange={(e) => setForm((f) => ({ ...f, communication_style: e.target.value }))} style={{ ...selectStyle, borderColor: BADGE_COLORS.communication_style.border }}>
              <option value="">Select…</option>
              {Object.entries(STYLE_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </ProfileField>
        </div>

        <div className="flex gap-2 mt-3">
          <button
            onClick={handleSave}
            disabled={saving}
            style={{
              display: 'flex', alignItems: 'center', gap: 4,
              fontSize: 12, padding: '5px 12px', borderRadius: 8,
              background: 'var(--color-accent)', color: 'var(--color-accent-contrast)',
              border: 'none', cursor: 'pointer', fontWeight: 500,
            }}
          >
            <Check size={12} />
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button
            onClick={handleCancel}
            style={{
              display: 'flex', alignItems: 'center', gap: 4,
              fontSize: 12, padding: '5px 12px', borderRadius: 8,
              background: 'transparent', color: 'var(--color-text-muted)',
              border: '1px solid var(--color-border)', cursor: 'pointer',
            }}
          >
            <X size={12} />
            Cancel
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2 flex-wrap">
      {profile.life_stage && (
        <ProfileBadge field="life_stage" label="Stage" value={LIFE_STAGE_LABELS[profile.life_stage] ?? profile.life_stage} />
      )}
      {profile.income_estimate != null && (
        <ProfileBadge field="income_estimate" label="Income" value={`$${Math.round(profile.income_estimate / 1000)}k/yr`} />
      )}
      {profile.risk_tolerance && (
        <ProfileBadge field="risk_tolerance" label="Risk" value={RISK_LABELS[profile.risk_tolerance] ?? profile.risk_tolerance} />
      )}
      {profile.communication_style && (
        <ProfileBadge field="communication_style" label="Style" value={STYLE_LABELS[profile.communication_style] ?? profile.communication_style} />
      )}
      <button
        onClick={() => setEditing(true)}
        title="Edit profile"
        style={{
          display: 'flex', alignItems: 'center', gap: 3,
          fontSize: 11, padding: '3px 8px', borderRadius: 20,
          background: 'transparent', color: 'var(--color-text-muted)',
          border: '1px solid var(--color-border)', cursor: 'pointer',
        }}
      >
        <Pencil size={9} />
        Edit
      </button>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function Advisor() {
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()

  const [messages, setMessages] = useState<Message[]>([])
  const [profile, setProfile] = useState<UserFinancialProfile | null>(null)
  const [isOnboarding, setIsOnboarding] = useState(false)
  const [loaded, setLoaded] = useState(false) // profile + history both fetched
  const [reactions, setReactions] = useState<Record<number, string>>({})

  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [isStreaming, setIsStreaming] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const autoSubmitted = useRef(false)

  const MAX_HISTORY = 10

  const { data: goals = [] } = useQuery({
    queryKey: ['goals', 'active'],
    queryFn: () => advisorApi.listGoals('active'),
    staleTime: 30_000,
  })

  // Load profile + conversation history in parallel on mount
  useEffect(() => {
    Promise.all([
      advisorApi.getProfile(),
      advisorApi.history(50),
    ]).then(([prof, records]) => {
      setProfile(prof)
      if (!prof.has_profile) {
        // New user — start onboarding
        setIsOnboarding(true)
        setMessages([{ role: 'advisor', content: ONBOARD_WELCOME }])
      } else {
        const prior = historyToMessages(records)
        setMessages([
          { role: 'advisor', content: WELCOME },
          ...prior,
        ])
      }
      setLoaded(true)
    }).catch(() => {
      setMessages([{ role: 'advisor', content: WELCOME }])
      setLoaded(true)
    })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-submit ?q= param once ready
  useEffect(() => {
    if (!loaded || isOnboarding) return
    const q = searchParams.get('q')
    if (q && !autoSubmitted.current) {
      autoSubmitted.current = true
      setSearchParams({}, { replace: true })
      sendMessage(q)
    }
  }, [loaded, isOnboarding]) // eslint-disable-line react-hooks/exhaustive-deps

  // Scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isLoading])

  const handleReaction = async (adviceId: number, reaction: 'followed' | 'ignored') => {
    setReactions((prev) => ({ ...prev, [adviceId]: reaction }))
    try {
      await advisorApi.reactToAdvice(adviceId, reaction)
    } catch { /* best-effort */ }
  }

  const handleReset = () => {
    setMessages([{ role: 'advisor', content: WELCOME }])
    setReactions({})
  }

  const sendMessage = async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || isLoading) return

    const history = messages
      .slice(1)
      .slice(-MAX_HISTORY)
      .map((m) => ({
        role: (m.role === 'user' ? 'user' : 'assistant') as 'user' | 'assistant',
        content: m.content,
      }))

    setMessages((prev) => [...prev, { role: 'user', content: trimmed }])
    setInput('')
    setIsLoading(true)

    try {
      if (isOnboarding) {
        const result = await advisorApi.onboard(trimmed, history)
        setMessages((prev) => {
          const cleared = prev.map((m) => m.options ? { ...m, options: undefined } : m)
          return [
            ...cleared,
            {
              role: 'advisor' as const,
              content: result.response,
              options: result.options?.length ? result.options : undefined,
            },
          ]
        })
        if (result.completed) {
          setIsOnboarding(false)
          const [newProfile] = await Promise.all([
            advisorApi.getProfile(),
            queryClient.invalidateQueries({ queryKey: ['goals', 'active'] }),
          ])
          setProfile(newProfile)
        }
      } else {
        // Add empty placeholder for the streaming response
        setMessages((prev) => [...prev, { role: 'advisor', content: '' }])
        setIsStreaming(true)

        const result = await advisorApi.chatStream(
          trimmed,
          history,
          (delta) => {
            setMessages((prev) => {
              const updated = [...prev]
              const last = updated[updated.length - 1]
              if (last.role === 'advisor') {
                updated[updated.length - 1] = { ...last, content: last.content + delta }
              }
              return updated
            })
          },
          (correction) => {
            setMessages((prev) => {
              const updated = [...prev]
              const last = updated[updated.length - 1]
              if (last.role === 'advisor') {
                updated[updated.length - 1] = { ...last, content: correction }
              }
              return updated
            })
          },
        )

        setIsStreaming(false)
        setMessages((prev) => {
          const updated = [...prev]
          const last = updated[updated.length - 1]
          if (last.role === 'advisor') {
            updated[updated.length - 1] = {
              ...last,
              advice_id: result.advice_id,
              actions: result.actions?.length ? result.actions : undefined,
            }
          }
          return updated
        })
      }
    } catch {
      setIsStreaming(false)
      setMessages((prev) => {
        // Replace empty placeholder or append error
        const last = prev[prev.length - 1]
        if (last?.role === 'advisor' && last.content === '') {
          return [...prev.slice(0, -1), { role: 'advisor', content: 'Sorry, something went wrong. Please try again.' }]
        }
        return [...prev, { role: 'advisor', content: 'Sorry, something went wrong. Please try again.' }]
      })
    } finally {
      setIsLoading(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage(input)
    }
  }

  // How many real user messages (excluding the welcome)
  const userMessageCount = messages.filter((m) => m.role === 'user').length
  const showQuickPrompts = loaded && !isOnboarding && userMessageCount === 0

  return (
    <div className="flex flex-col fade-in" style={{ height: 'calc(100vh - 48px)' }}>

      {/* Header block — title + profile context bar */}
      <div
        className="flex-shrink-0 mb-3 rounded-xl px-4 pt-4 pb-3"
        style={{
          background: 'linear-gradient(160deg, var(--color-surface-raise, #1a1c20) 0%, var(--color-surface) 100%)',
        }}
      >
        <div className="flex items-start justify-between mb-1">
          <div>
            <h1 style={{ fontSize: 22, fontWeight: 700, color: 'var(--color-text-primary)', letterSpacing: '-0.01em' }}>
              {isOnboarding ? 'Getting to know you' : 'Advisor'}
            </h1>
            <p style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2 }}>
              {isOnboarding
                ? 'A few quick questions to personalize your experience'
                : 'Powered by your data — not generic advice'}
            </p>
          </div>
          {!isOnboarding && loaded && (
            <button
              onClick={handleReset}
              title="New conversation"
              style={{
                display: 'flex', alignItems: 'center', gap: 4,
                fontSize: 11, padding: '5px 10px', borderRadius: 20,
                background: 'transparent', color: 'var(--color-text-muted)',
                border: '1px solid var(--color-border)', cursor: 'pointer', flexShrink: 0,
              }}
            >
              <RotateCcw size={10} />
              New chat
            </button>
          )}
        </div>

        {/* Profile context bar — anchored inside header */}
        {!isOnboarding && profile?.has_profile && (
          <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid rgba(255,255,255,0.05)' }}>
            <ProfilePanel
              profile={profile}
              onSaved={(updated) => setProfile(updated)}
            />
          </div>
        )}
      </div>

      {/* Goals bar */}
      {!isOnboarding && goals.length > 0 && (
        <div
          className="flex-shrink-0 flex gap-2 mb-3 overflow-x-auto pb-1"
          style={{ scrollbarWidth: 'none' }}
        >
          {goals.map((g) => (
            <GoalChip key={g.id} goal={g} />
          ))}
        </div>
      )}

      {/* Quick prompts */}
      {showQuickPrompts && (
        <div className="flex-shrink-0 flex flex-wrap gap-2 mb-3">
          {QUICK_PROMPTS.map((p) => (
            <button
              key={p.label}
              onClick={() => sendMessage(p.q)}
              className="rounded-lg px-3 py-2"
              style={{
                fontSize: 12,
                fontWeight: 500,
                background: 'var(--color-surface-raise, #1a1c20)',
                border: '1px solid rgba(255,255,255,0.06)',
                color: 'var(--color-text-secondary)',
                cursor: 'pointer',
                transition: 'background 0.15s, color 0.15s',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = 'var(--color-accent)'
                e.currentTarget.style.color = 'var(--color-accent-contrast)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'var(--color-surface-raise, #1a1c20)'
                e.currentTarget.style.color = 'var(--color-text-secondary)'
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto space-y-4 pb-2" style={{ scrollbarWidth: 'thin' }}>
        {messages.map((msg, i) => (
          <div key={i} className={`flex flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
            <div className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'} w-full`}>
              {msg.role === 'advisor' && (
                <div
                  className="flex-shrink-0 flex items-center justify-center rounded-full mr-2 mt-1"
                  style={{ width: 28, height: 28, background: 'var(--color-accent)' }}
                >
                  <Bot size={14} color="var(--color-accent-contrast)" />
                </div>
              )}
              <div
                className="rounded-2xl px-4 py-3"
                style={{
                  maxWidth: '78%',
                  background: msg.role === 'user' ? 'var(--color-accent)' : 'var(--color-surface)',
                  border: msg.role === 'advisor' ? '1px solid var(--color-border)' : 'none',
                  color: msg.role === 'user' ? 'var(--color-accent-contrast)' : 'var(--color-text-primary)',
                  fontSize: 14,
                  lineHeight: 1.65,
                }}
              >
                {msg.role === 'user' ? (
                  <span style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</span>
                ) : (
                  <div className="advisor-md">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                  </div>
                )}
              </div>
            </div>

            {/* Onboarding option buttons — only on the last advisor message */}
            {msg.options && msg.options.length > 0 && !isLoading && (
              <div className="flex flex-wrap gap-2 mt-2 ml-9">
                {msg.options.map((opt) => (
                  <button
                    key={opt}
                    onClick={() => sendMessage(opt)}
                    style={{
                      fontSize: 13,
                      padding: '6px 14px',
                      borderRadius: 20,
                      background: 'var(--color-surface)',
                      border: '1px solid var(--color-accent)',
                      color: 'var(--color-accent-text)',
                      cursor: 'pointer',
                      transition: 'background 0.15s, color 0.15s',
                      fontWeight: 500,
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = 'var(--color-accent)'
                      e.currentTarget.style.color = 'var(--color-accent-contrast)'
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = 'var(--color-surface)'
                      e.currentTarget.style.color = 'var(--color-accent-text)'
                    }}
                  >
                    {opt}
                  </button>
                ))}
              </div>
            )}

            {/* Action chips — specific next steps the user can take */}
            {msg.role === 'advisor' && msg.actions && msg.actions.length > 0 && !isLoading && (
              <div className="flex flex-wrap gap-2 mt-3 ml-9">
                {msg.actions.map((action, ai) => (
                  <button
                    key={ai}
                    onClick={() => sendMessage(action.message)}
                    style={{
                      fontSize: 12,
                      fontWeight: 500,
                      padding: '6px 13px',
                      borderRadius: 20,
                      background: 'var(--color-accent-soft)',
                      border: '1px solid color-mix(in srgb, var(--color-accent) 35%, transparent)',
                      color: 'var(--color-accent-text)',
                      cursor: 'pointer',
                      transition: 'background 0.15s, border-color 0.15s, color 0.15s',
                      textAlign: 'left',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.background = 'var(--color-accent)'
                      e.currentTarget.style.color = 'var(--color-accent-contrast)'
                      e.currentTarget.style.borderColor = 'var(--color-accent)'
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = 'var(--color-accent-soft)'
                      e.currentTarget.style.color = 'var(--color-accent-text)'
                      e.currentTarget.style.borderColor = 'color-mix(in srgb, var(--color-accent) 35%, transparent)'
                    }}
                  >
                    {action.label}
                  </button>
                ))}
              </div>
            )}

            {/* Reaction buttons — advisor messages with a saved advice_id */}
            {msg.role === 'advisor' && msg.advice_id && !isLoading && (
              <div className="flex gap-1 mt-1 ml-9">
                {(['followed', 'ignored'] as const).map((r) => {
                  const active = reactions[msg.advice_id!] === r
                  return (
                    <button
                      key={r}
                      onClick={() => handleReaction(msg.advice_id!, r)}
                      title={r === 'followed' ? 'Helpful' : 'Not helpful'}
                      style={{
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        width: 26, height: 26, borderRadius: 8,
                        background: active ? 'var(--color-accent)' : 'transparent',
                        border: `1px solid ${active ? 'var(--color-accent)' : 'var(--color-border)'}`,
                        color: active ? 'var(--color-accent-contrast)' : 'var(--color-text-muted)',
                        cursor: 'pointer', transition: 'all 0.15s',
                      }}
                    >
                      {r === 'followed'
                        ? <ThumbsUp size={11} />
                        : <ThumbsDown size={11} />}
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        ))}

        {isLoading && !isStreaming && (
          <div className="flex justify-start">
            <div
              className="flex-shrink-0 flex items-center justify-center rounded-full mr-2"
              style={{ width: 28, height: 28, background: 'var(--color-accent)' }}
            >
              <Bot size={14} color="#fff" />
            </div>
            <div
              className="rounded-2xl px-4 py-3 flex items-center gap-2"
              style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
            >
              <Loader2 size={14} className="spinner" style={{ color: 'var(--color-text-muted)' }} />
              <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Thinking…</span>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div className="flex-shrink-0 pt-3 pb-1" style={{ borderTop: '1px solid var(--color-border)' }}>
        <div className="flex gap-2 items-end">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={isOnboarding ? 'Tell me about yourself…' : 'Ask anything about your finances…'}
            rows={1}
            disabled={isLoading || !loaded}
            style={{
              flex: 1,
              resize: 'none',
              fontSize: 14,
              borderRadius: 12,
              padding: '10px 14px',
              background: 'var(--color-surface)',
              border: '1px solid var(--color-border)',
              color: 'var(--color-text-primary)',
              lineHeight: 1.5,
              maxHeight: 120,
              overflowY: 'auto',
            }}
            onInput={(e) => {
              const t = e.target as HTMLTextAreaElement
              t.style.height = 'auto'
              t.style.height = Math.min(t.scrollHeight, 120) + 'px'
            }}
          />
          <button
            onClick={() => sendMessage(input)}
            disabled={isLoading || !input.trim() || !loaded}
            style={{
              width: 42,
              height: 42,
              borderRadius: 12,
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: input.trim() && !isLoading ? 'var(--color-accent)' : 'var(--color-surface)',
              border: '1px solid var(--color-border)',
              color: input.trim() && !isLoading ? 'var(--color-accent-contrast)' : 'var(--color-text-muted)',
              cursor: input.trim() && !isLoading ? 'pointer' : 'default',
              transition: 'background 0.15s, color 0.15s',
            }}
          >
            <Send size={16} />
          </button>
        </div>
        <p style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 6 }}>
          {isOnboarding
            ? 'Enter to send · Shift+Enter for new line'
            : 'Enter to send · Shift+Enter for new line · Financial education only, not investment advice'}
        </p>
      </div>
    </div>
  )
}

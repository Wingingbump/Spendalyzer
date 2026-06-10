import React, { useState, useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Plus, Trash2, X, ChevronRight, ChevronLeft,
  Layers, Target, RefreshCw, Bot, Send,
} from 'lucide-react'
import { workspaceApi, advisorApi, ledgerApi } from '../lib/api'
import type { CustomGroup, Goal, LedgerRow } from '../lib/api'
import { useWorkspace } from '../context/WorkspaceContext'
import { usePanel } from '../context/PanelContext'
import { formatCurrency } from '../lib/utils'

export const PANEL_WIDTH = 300

// ── Tab config ────────────────────────────────────────────────────────────────

type TabId = 'budgets' | 'recurring' | 'groups'

// All three tabs render on every page; only the initial selection changes.
const ALL_TABS: TabId[] = ['budgets', 'recurring', 'groups']

const ROUTE_DEFAULT_TAB: Record<string, TabId> = {
  '/overview':     'budgets',
  '/transactions': 'recurring',
  '/canvas':       'groups',
  '/advisor':      'budgets',
  '/settings':     'budgets',
  '/insights':     'budgets',
}
const FALLBACK_DEFAULT_TAB: TabId = 'budgets'

const TAB_LABEL: Record<TabId, string> = {
  budgets:   'Budgets',
  recurring: 'Recurring',
  groups:    'Groups',
}

const CATEGORIES = [
  'Food & Drink', 'Groceries', 'Shopping', 'Transportation', 'Entertainment',
  'Bills & Utilities', 'Health & Fitness', 'Travel', 'Personal Care',
  'Home', 'Education', 'Business Services', 'Income', 'Transfer', 'Other',
]

const GROUP_COLORS = ['#b5c4ff', '#5abf8a', '#7aaed4', '#e8c17a', '#c47adb', '#e86060']

const FREQ_LABEL: Record<string, string> = {
  weekly: 'weekly', biweekly: 'bi-wk', monthly: 'monthly',
  quarterly: 'qtrly', annual: 'annual',
}

// ── Shared ────────────────────────────────────────────────────────────────────

function ProgressBar({ value, max, color }: { value: number; max: number; color?: string }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0
  const over = value > max
  const barColor = color ?? (over ? 'var(--color-negative)' : pct > 80 ? '#e8c17a' : 'var(--color-accent)')
  return (
    <div style={{ height: 4, borderRadius: 2, background: 'var(--color-border)' }}>
      <div style={{ height: 4, borderRadius: 2, width: `${pct}%`, background: barColor, transition: 'width 0.3s' }} />
    </div>
  )
}

// ── Recurring tab ─────────────────────────────────────────────────────────────

function RecurringTab() {
  const { data: recurring = [], isLoading } = useQuery({
    queryKey: ['recurring'],
    queryFn: workspaceApi.listRecurring,
    staleTime: 120_000,
  })

  const monthlyEstimate = recurring
    .filter((r) => r.frequency === 'monthly')
    .reduce((s, r) => s + r.amount, 0)

  if (isLoading) return <p style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Detecting…</p>

  if (recurring.length === 0) {
    return <p style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>No recurring transactions detected yet.</p>
  }

  return (
    <div>
      <div className="space-y-1.5 mb-4">
        {recurring.map((r, i) => (
          <div key={i} className="flex items-center justify-between gap-2">
            <span
              style={{ fontSize: 12, color: 'var(--color-text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}
              title={r.name}
            >
              {r.name}
            </span>
            <span className="px-1.5 py-0.5 rounded flex-shrink-0" style={{ fontSize: 10, background: 'var(--color-surface-raise)', color: 'var(--color-text-muted)' }}>
              {FREQ_LABEL[r.frequency]}
            </span>
            <span style={{ fontSize: 12, fontFamily: 'monospace', color: 'var(--color-text-primary)', flexShrink: 0 }}>
              {formatCurrency(r.amount)}
            </span>
          </div>
        ))}
      </div>
      {monthlyEstimate > 0 && (
        <div className="flex items-center justify-between rounded-lg px-3 py-2" style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)' }}>
          <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Monthly subscriptions</span>
          <span style={{ fontSize: 12, fontFamily: 'monospace', color: 'var(--color-negative)' }}>{formatCurrency(monthlyEstimate)}</span>
        </div>
      )}
    </div>
  )
}

// ── Goals section (inside Budget tab) ────────────────────────────────────────

function GoalsSection() {
  const qc = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newTarget, setNewTarget] = useState('')
  const [newDeadline, setNewDeadline] = useState('')
  // id → { step: 'pick' | 'amount', sourceTx: LedgerRow | null, amountStr: string }
  const [progressState, setProgressState] = useState<Record<number, {
    step: 'pick' | 'amount'
    sourceTx: LedgerRow | null
    amountStr: string
  }>>({})

  const { data: incomeTxs = [] } = useQuery({
    queryKey: ['income-sources'],
    queryFn: async () => {
      const res = await ledgerApi.list({ range: '60d', types: 'credit', show_transfers: false })
      return res.rows.filter((r) => !r.is_transfer && r.amount < 0).slice(0, 20)
    },
    staleTime: 60_000,
    enabled: Object.keys(progressState).length > 0,
  })

  const { data: goals = [] } = useQuery({
    queryKey: ['goals', 'active'],
    queryFn: () => advisorApi.listGoals('active'),
    staleTime: 30_000,
  })

  const createMutation = useMutation({
    mutationFn: (g: { title: string; target_amount?: number; deadline?: string }) =>
      advisorApi.createGoal(g),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['goals'] })
      setAdding(false)
      setNewTitle('')
      setNewTarget('')
      setNewDeadline('')
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, current_amount }: { id: number; current_amount: number }) =>
      advisorApi.updateGoal(id, { current_amount }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['goals'] })
      setProgressState((p) => { const n = { ...p }; delete n[vars.id]; return n })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => advisorApi.deleteGoal(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['goals'] }),
  })

  const handleAdd = () => {
    if (!newTitle.trim()) return
    const target = newTarget ? parseFloat(newTarget) : undefined
    createMutation.mutate({
      title: newTitle.trim(),
      target_amount: target && !isNaN(target) ? target : undefined,
      deadline: newDeadline || undefined,
    })
  }

  const openProgress = (id: number) =>
    setProgressState((p) => ({ ...p, [id]: { step: 'pick', sourceTx: null, amountStr: '' } }))

  const closeProgress = (id: number) =>
    setProgressState((p) => { const n = { ...p }; delete n[id]; return n })

  const selectSource = (id: number, tx: LedgerRow | null) =>
    setProgressState((p) => ({
      ...p,
      [id]: {
        step: 'amount',
        sourceTx: tx,
        amountStr: tx ? String(Math.abs(tx.amount)) : '',
      },
    }))

  const handleAddProgress = (g: Goal) => {
    const state = progressState[g.id]
    if (!state) return
    const delta = parseFloat(state.amountStr)
    if (isNaN(delta) || delta <= 0) return
    updateMutation.mutate({ id: g.id, current_amount: Math.max(0, g.current_amount + delta) })
  }

  return (
    <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--color-border)' }}>
      <p style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10 }}>
        Goals
      </p>

      {goals.length === 0 && !adding && (
        <p style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8 }}>No active goals.</p>
      )}

      <div className="space-y-4">
        {goals.map((g: Goal) => {
          const pct = g.target_amount
            ? Math.min((g.current_amount / g.target_amount) * 100, 100)
            : null
          const daysLeft = g.deadline
            ? Math.ceil((new Date(g.deadline).getTime() - Date.now()) / 86_400_000)
            : null
          const ps = progressState[g.id]

          return (
            <div key={g.id}>
              <div className="flex items-center justify-between mb-1">
                <span style={{ fontSize: 12, color: 'var(--color-text-secondary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {g.title}
                </span>
                <div className="flex items-center gap-2 flex-shrink-0">
                  {daysLeft !== null && (
                    <span style={{ fontSize: 10, color: daysLeft < 30 ? '#e8c17a' : 'var(--color-text-muted)' }}>
                      {daysLeft > 0 ? `${daysLeft}d` : 'due'}
                    </span>
                  )}
                  {g.target_amount !== null && !ps && (
                    <button
                      onClick={() => openProgress(g.id)}
                      title="Add progress"
                      style={{ color: 'var(--color-text-muted)', lineHeight: 1 }}
                    >
                      <Plus size={11} />
                    </button>
                  )}
                  <button
                    onClick={() => deleteMutation.mutate(g.id)}
                    style={{ color: 'var(--color-text-muted)', lineHeight: 1 }}
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
              </div>

              {pct !== null && g.target_amount !== null && (
                <>
                  <ProgressBar value={g.current_amount} max={g.target_amount} color="var(--color-accent)" />
                  <div className="flex justify-between mt-0.5">
                    <span style={{ fontSize: 10, fontFamily: 'monospace', color: 'var(--color-text-muted)' }}>
                      {formatCurrency(g.current_amount)} / {formatCurrency(g.target_amount)}
                    </span>
                    <span style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>{pct.toFixed(0)}%</span>
                  </div>
                </>
              )}

              {/* Step 1 — pick an income source */}
              {ps?.step === 'pick' && (
                <div
                  className="mt-2 rounded-lg p-2 space-y-1"
                  style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)' }}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                      Log from income
                    </span>
                    <button onClick={() => closeProgress(g.id)} style={{ color: 'var(--color-text-muted)' }}>
                      <X size={11} />
                    </button>
                  </div>

                  {incomeTxs.length === 0 ? (
                    <p style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>No recent income found.</p>
                  ) : (
                    incomeTxs.slice(0, 6).map((tx) => (
                      <button
                        key={tx.id}
                        onClick={() => selectSource(g.id, tx)}
                        className="w-full flex items-center justify-between rounded-md px-2 py-1.5"
                        style={{
                          background: 'var(--color-surface)',
                          border: '1px solid var(--color-border)',
                          cursor: 'pointer',
                          textAlign: 'left',
                        }}
                      >
                        <div>
                          <p style={{ fontSize: 11, color: 'var(--color-text-primary)', fontWeight: 500 }}>
                            {tx.merchant_normalized || tx.name}
                          </p>
                          <p style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>{tx.date}</p>
                        </div>
                        <span style={{ fontSize: 11, fontFamily: 'monospace', color: '#4ade80', fontWeight: 600, flexShrink: 0, marginLeft: 8 }}>
                          +{formatCurrency(Math.abs(tx.amount))}
                        </span>
                      </button>
                    ))
                  )}

                  <button
                    onClick={() => selectSource(g.id, null)}
                    className="w-full text-center"
                    style={{ fontSize: 11, color: 'var(--color-text-muted)', paddingTop: 4, cursor: 'pointer' }}
                  >
                    Enter amount manually →
                  </button>
                </div>
              )}

              {/* Step 2 — confirm amount */}
              {ps?.step === 'amount' && (() => {
                const maxAmount = ps.sourceTx ? Math.abs(ps.sourceTx.amount) : undefined
                const enteredVal = parseFloat(ps.amountStr)
                const overLimit = maxAmount !== undefined && !isNaN(enteredVal) && enteredVal > maxAmount
                const invalid = !ps.amountStr || isNaN(enteredVal) || enteredVal <= 0 || overLimit
                return (
                  <div
                    className="mt-2 rounded-lg p-2.5 space-y-2"
                    style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)' }}
                  >
                    {ps.sourceTx && (
                      <p style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                        From <span style={{ color: 'var(--color-text-primary)' }}>{ps.sourceTx.merchant_normalized || ps.sourceTx.name}</span>
                        {' · '}<span style={{ color: '#4ade80' }}>+{formatCurrency(maxAmount!)}</span>
                      </p>
                    )}

                    <div className="flex items-center gap-1.5">
                      <span style={{ fontSize: 12, color: 'var(--color-text-muted)', flexShrink: 0 }}>+$</span>
                      <input
                        type="number"
                        autoFocus
                        placeholder="0.00"
                        value={ps.amountStr}
                        onChange={(e) => {
                          const raw = e.target.value
                          const val = parseFloat(raw)
                          // Clamp to source max if present
                          const clamped = maxAmount !== undefined && !isNaN(val) && val > maxAmount
                            ? String(maxAmount)
                            : raw
                          setProgressState((prev) => ({
                            ...prev,
                            [g.id]: { ...prev[g.id], amountStr: clamped },
                          }))
                        }}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !invalid) handleAddProgress(g)
                          if (e.key === 'Escape') closeProgress(g.id)
                        }}
                        min="0"
                        max={maxAmount}
                        step="1"
                        style={{
                          fontSize: 13, flex: 1, padding: '5px 8px', borderRadius: 7,
                          border: `1px solid ${overLimit ? '#f87171' : 'var(--color-accent)'}`,
                          background: 'var(--color-surface)',
                          color: 'var(--color-text-primary)', outline: 'none',
                        }}
                      />
                    </div>

                    {overLimit && (
                      <p style={{ fontSize: 10, color: '#f87171' }}>
                        Can't exceed the income amount ({formatCurrency(maxAmount!)})
                      </p>
                    )}

                    <div className="flex gap-1.5">
                      <button
                        onClick={() => handleAddProgress(g)}
                        disabled={updateMutation.isPending || invalid}
                        style={{
                          flex: 1, fontSize: 12, padding: '5px 0', borderRadius: 7,
                          background: invalid ? 'var(--color-surface)' : 'var(--color-accent)',
                          color: invalid ? 'var(--color-text-muted)' : 'var(--color-accent-contrast)',
                          border: `1px solid ${invalid ? 'var(--color-border)' : 'var(--color-accent)'}`,
                          cursor: invalid ? 'default' : 'pointer',
                          fontWeight: 500, transition: 'background 0.15s',
                        }}
                      >
                        {updateMutation.isPending ? 'Saving…' : 'Save'}
                      </button>
                      <button
                        onClick={() => closeProgress(g.id)}
                        style={{
                          fontSize: 12, padding: '5px 10px', borderRadius: 7,
                          background: 'transparent', color: 'var(--color-text-muted)',
                          border: '1px solid var(--color-border)', cursor: 'pointer',
                        }}
                      >
                        Cancel
                      </button>
                    </div>

                    {ps.sourceTx && (
                      <button
                        onClick={() => setProgressState((p) => ({ ...p, [g.id]: { ...p[g.id], step: 'pick' } }))}
                        style={{ fontSize: 10, color: 'var(--color-text-muted)', cursor: 'pointer' }}
                      >
                        ← Pick different source
                      </button>
                    )}
                  </div>
                )
              })()}
            </div>
          )
        })}
      </div>

      {adding ? (
        <div className="space-y-2 mt-3">
          <input
            type="text" placeholder="Goal name" value={newTitle} autoFocus
            onChange={(e) => setNewTitle(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
            style={{ fontSize: 12, width: '100%' }}
          />
          <input
            type="number" placeholder="Target amount (optional)" value={newTarget}
            onChange={(e) => setNewTarget(e.target.value)} step="100" min="0"
            style={{ fontSize: 12, width: '100%' }}
          />
          <input
            type="date" value={newDeadline}
            onChange={(e) => setNewDeadline(e.target.value)}
            style={{ fontSize: 12, width: '100%' }}
          />
          <div className="flex gap-1.5">
            <button
              onClick={handleAdd} disabled={createMutation.isPending || !newTitle.trim()}
              className="flex-1 rounded-md font-medium"
              style={{ background: 'var(--color-accent)', color: 'var(--color-accent-contrast)', padding: '5px 0', fontSize: 12 }}
            >
              {createMutation.isPending ? 'Saving…' : 'Save'}
            </button>
            <button
              onClick={() => { setAdding(false); setNewTitle(''); setNewTarget(''); setNewDeadline('') }}
              className="rounded-md"
              style={{ background: 'var(--color-surface-raise)', color: 'var(--color-text-muted)', padding: '5px 10px', fontSize: 12 }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => setAdding(true)}
          className="flex items-center gap-1.5 mt-2"
          style={{ fontSize: 12, color: 'var(--color-text-muted)' }}
        >
          <Plus size={12} /> New goal
        </button>
      )}
    </div>
  )
}

// ── Budget tab ────────────────────────────────────────────────────────────────

function BudgetTab() {
  const qc = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [newCategory, setNewCategory] = useState(CATEGORIES[0])
  const [newAmount, setNewAmount] = useState('')

  const { data: budgets = [] } = useQuery({
    queryKey: ['budgets'],
    queryFn: workspaceApi.listBudgets,
    staleTime: 30_000,
  })

  const upsertMutation = useMutation({
    mutationFn: ({ category, amount }: { category: string; amount: number }) =>
      workspaceApi.upsertBudget(category, amount),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['budgets'] }); setAdding(false); setNewAmount('') },
  })

  const deleteMutation = useMutation({
    mutationFn: (category: string) => workspaceApi.deleteBudget(category),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['budgets'] }),
  })

  const handleAdd = () => {
    const amt = parseFloat(newAmount)
    if (!newAmount || isNaN(amt) || amt <= 0) return
    upsertMutation.mutate({ category: newCategory, amount: amt })
  }

  // Monthly summary stats
  const today = new Date()
  const daysInMonth = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate()
  const daysLeft = daysInMonth - today.getDate()
  const totalBudgeted = budgets.reduce((s, b) => s + b.amount, 0)
  const totalSpent = budgets.reduce((s, b) => s + b.spent, 0)
  const overallPct = totalBudgeted > 0 ? (totalSpent / totalBudgeted) * 100 : 0

  return (
    <div className="space-y-3">
      {/* Monthly summary */}
      {budgets.length > 0 && (
        <div
          className="rounded-xl p-3 space-y-2"
          style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)' }}
        >
          <div className="flex items-center justify-between">
            <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
              {daysLeft} days left this month
            </span>
            <span style={{
              fontSize: 11,
              fontWeight: 600,
              fontFamily: 'monospace',
              color: overallPct > 90 ? 'var(--color-negative)' : overallPct > 75 ? '#e8c17a' : 'var(--color-positive)',
            }}>
              {overallPct.toFixed(0)}%
            </span>
          </div>
          <ProgressBar value={totalSpent} max={totalBudgeted} />
          <div className="flex justify-between">
            <span style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--color-text-secondary)' }}>
              {formatCurrency(totalSpent)} spent
            </span>
            <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
              of {formatCurrency(totalBudgeted)}
            </span>
          </div>
        </div>
      )}

      {budgets.length === 0 && !adding && (
        <p style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>No budgets set.</p>
      )}

      {/* Individual budget rows */}
      {budgets.map((b) => {
        const over = b.spent > b.amount
        const pct = b.amount > 0 ? Math.min((b.spent / b.amount) * 100, 100) : 0
        const diff = Math.abs(b.amount - b.spent)
        return (
          <div key={b.category}>
            <div className="flex items-center justify-between mb-1">
              <span style={{ fontSize: 12, color: 'var(--color-text-secondary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {b.category}
              </span>
              <div className="flex items-center gap-2 flex-shrink-0">
                <span style={{
                  fontSize: 10,
                  fontWeight: 500,
                  color: over ? 'var(--color-negative)' : 'var(--color-positive)',
                }}>
                  {over ? `$${diff.toFixed(0)} over` : `$${diff.toFixed(0)} left`}
                </span>
                <button onClick={() => deleteMutation.mutate(b.category)} style={{ color: 'var(--color-text-muted)', lineHeight: 1 }}>
                  <Trash2 size={11} />
                </button>
              </div>
            </div>
            <ProgressBar value={b.spent} max={b.amount} />
            <div className="flex justify-between mt-0.5">
              <span style={{ fontSize: 10, fontFamily: 'monospace', color: 'var(--color-text-muted)' }}>
                {formatCurrency(b.spent)} / {formatCurrency(b.amount)}
              </span>
              <span style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>{pct.toFixed(0)}%</span>
            </div>
          </div>
        )
      })}

      {/* Add budget form */}
      {adding ? (
        <div className="space-y-2">
          <select value={newCategory} onChange={(e) => setNewCategory(e.target.value)} style={{ fontSize: 12, width: '100%' }}>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <input
            type="number" placeholder="Monthly limit ($)" value={newAmount}
            onChange={(e) => setNewAmount(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
            step="1" min="0" style={{ fontSize: 12, width: '100%' }} autoFocus
          />
          <div className="flex gap-1.5">
            <button onClick={handleAdd} disabled={upsertMutation.isPending} className="flex-1 rounded-md font-medium"
              style={{ background: 'var(--color-accent)', color: 'var(--color-accent-contrast)', padding: '5px 0', fontSize: 12 }}>
              Save
            </button>
            <button onClick={() => { setAdding(false); setNewAmount('') }} className="rounded-md"
              style={{ background: 'var(--color-surface-raise)', color: 'var(--color-text-muted)', padding: '5px 10px', fontSize: 12 }}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button onClick={() => setAdding(true)} className="flex items-center gap-1.5"
          style={{ fontSize: 12, color: 'var(--color-text-muted)', paddingTop: 2 }}>
          <Plus size={12} /> Add budget
        </button>
      )}

      {/* Goals subsection */}
      <GoalsSection />
    </div>
  )
}

// ── Groups tab ────────────────────────────────────────────────────────────────

function GroupForm({
  initial, onSave, onCancel, saving,
}: {
  initial?: Partial<CustomGroup>
  onSave: (name: string, color: string, goal: number | null) => void
  onCancel: () => void
  saving: boolean
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [color, setColor] = useState(initial?.color ?? GROUP_COLORS[0])
  const [goalStr, setGoalStr] = useState(initial?.goal != null ? String(initial.goal) : '')

  const handleSave = () => {
    if (!name.trim()) return
    const goal = goalStr ? parseFloat(goalStr) : null
    onSave(name.trim(), color, isNaN(goal as number) ? null : goal)
  }

  return (
    <div className="space-y-2.5">
      <input type="text" placeholder="Group name" value={name} onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && handleSave()} style={{ fontSize: 12, width: '100%' }} autoFocus />
      <div className="flex items-center gap-2">
        <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Color</span>
        <div className="flex gap-1.5">
          {GROUP_COLORS.map((c) => (
            <button key={c} onClick={() => setColor(c)} style={{
              width: 18, height: 18, borderRadius: '50%', background: c,
              border: color === c ? '2px solid var(--color-text-primary)' : '2px solid transparent',
              outline: color === c ? '1px solid var(--color-border)' : 'none',
            }} />
          ))}
        </div>
      </div>
      <input type="number" placeholder="Spending goal (optional)" value={goalStr}
        onChange={(e) => setGoalStr(e.target.value)} step="1" min="0" style={{ fontSize: 12, width: '100%' }} />
      <div className="flex gap-1.5">
        <button onClick={handleSave} disabled={saving || !name.trim()} className="flex-1 rounded-md font-medium"
          style={{ background: 'var(--color-accent)', color: 'var(--color-accent-contrast)', padding: '5px 0', fontSize: 12 }}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button onClick={onCancel} className="rounded-md"
          style={{ background: 'var(--color-surface-raise)', color: 'var(--color-text-muted)', padding: '5px 10px', fontSize: 12 }}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function GroupsTab() {
  const qc = useQueryClient()
  const { activeGroup, setActiveGroup } = useWorkspace()
  const [adding, setAdding] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)

  const { data: groups = [] } = useQuery({
    queryKey: ['groups'],
    queryFn: workspaceApi.listGroups,
    staleTime: 15_000,
  })

  const createMutation = useMutation({
    mutationFn: ({ name, color, goal }: { name: string; color: string; goal: number | null }) =>
      workspaceApi.createGroup(name, color, goal),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['groups'] }); setAdding(false) },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, name, color, goal }: { id: number; name: string; color: string; goal: number | null }) =>
      workspaceApi.updateGroup(id, name, color, goal),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['groups'] }); setEditingId(null) },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => workspaceApi.deleteGroup(id),
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ['groups'] })
      if (activeGroup?.id === id) setActiveGroup(null)
    },
  })

  return (
    <div className="space-y-2">
      {groups.length === 0 && !adding && (
        <p style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
          No groups yet. Create one and tag transactions from the Transactions page.
        </p>
      )}

      {groups.map((g) => {
        const isActive = activeGroup?.id === g.id

        if (editingId === g.id) {
          return (
            <div key={g.id} style={{ background: 'var(--color-surface-raise)', borderRadius: 8, padding: 10 }}>
              <GroupForm
                initial={g}
                onSave={(name, color, goal) => updateMutation.mutate({ id: g.id, name, color, goal })}
                onCancel={() => setEditingId(null)}
                saving={updateMutation.isPending}
              />
            </div>
          )
        }

        return (
          <div key={g.id} className="rounded-lg"
            style={{
              border: `1px solid ${isActive ? g.color : 'var(--color-border)'}`,
              background: isActive ? `${g.color}12` : 'transparent',
              padding: '8px 10px', cursor: 'pointer',
              transition: 'border-color 0.15s, background 0.15s',
            }}
            onClick={() => setActiveGroup(isActive ? null : g)}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 min-w-0">
                <div style={{ width: 9, height: 9, borderRadius: '50%', background: g.color, flexShrink: 0 }} />
                <span style={{ fontSize: 13, color: 'var(--color-text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {g.name}
                </span>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
                <button onClick={() => setEditingId(g.id)} style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Edit</button>
                <button onClick={() => deleteMutation.mutate(g.id)} style={{ color: 'var(--color-text-muted)', lineHeight: 1 }}>
                  <Trash2 size={11} />
                </button>
              </div>
            </div>
            <div className="flex items-center gap-3 mt-1.5">
              <span style={{ fontSize: 12, fontFamily: 'monospace', color: 'var(--color-text-secondary)' }}>{formatCurrency(g.total)}</span>
              {g.goal != null
                ? <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>of {formatCurrency(g.goal)} goal</span>
                : <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{g.count} txn{g.count !== 1 ? 's' : ''}</span>
              }
            </div>
            {g.goal != null && <div style={{ marginTop: 6 }}><ProgressBar value={g.total} max={g.goal} color={g.color} /></div>}
            {isActive && <p style={{ fontSize: 11, color: g.color, marginTop: 6 }}>Click rows to tag ↗</p>}
          </div>
        )
      })}

      {adding ? (
        <div style={{ background: 'var(--color-surface-raise)', borderRadius: 8, padding: 10 }}>
          <GroupForm
            onSave={(name, color, goal) => createMutation.mutate({ name, color, goal })}
            onCancel={() => setAdding(false)}
            saving={createMutation.isPending}
          />
        </div>
      ) : (
        <button onClick={() => setAdding(true)} className="flex items-center gap-1.5"
          style={{ fontSize: 12, color: 'var(--color-text-muted)', paddingTop: 2 }}>
          <Plus size={12} /> New group
        </button>
      )}
    </div>
  )
}

// ── Active group banner (exported for pages) ──────────────────────────────────

export function ActiveGroupBanner() {
  const { activeGroup, setActiveGroup } = useWorkspace()
  if (!activeGroup) return null

  return (
    <div className="flex items-center gap-3 px-3 py-2 rounded-lg"
      style={{ background: `${activeGroup.color}18`, border: `1px solid ${activeGroup.color}40`, marginBottom: 12 }}
    >
      <div style={{ width: 9, height: 9, borderRadius: '50%', background: activeGroup.color, flexShrink: 0 }} />
      <span style={{ fontSize: 13, color: 'var(--color-text-primary)', flex: 1 }}>{activeGroup.name}</span>
      <span style={{ fontSize: 12, fontFamily: 'monospace', color: 'var(--color-text-muted)' }}>
        {formatCurrency(activeGroup.total)}
        {activeGroup.goal != null && ` / ${formatCurrency(activeGroup.goal)}`}
        {' · '}{activeGroup.count} txns
      </span>
      <span style={{ fontSize: 12, color: activeGroup.color }}>Click rows to tag</span>
      <button onClick={() => setActiveGroup(null)} style={{ color: 'var(--color-text-muted)', lineHeight: 1 }}>
        <X size={13} />
      </button>
    </div>
  )
}

// ── Advisor widget (pinned at panel bottom) ───────────────────────────────────

function AdvisorWidget() {
  const navigate = useNavigate()
  const location = useLocation()
  const [input, setInput] = useState('')

  // Hide on advisor page — redundant there
  if (location.pathname === '/advisor') return null

  const quickPrompts = [
    { label: 'Review this month', q: "Review my spending this month and tell me how I'm doing." },
    { label: 'Check goals', q: 'How am I tracking toward my financial goals?' },
    { label: 'Spending tips', q: 'Based on my transactions, what spending habits should I improve?' },
  ]

  const go = (message: string) => {
    if (!message.trim()) return
    navigate(`/advisor?q=${encodeURIComponent(message.trim())}`)
  }

  return (
    <div
      className="flex-shrink-0"
      style={{ borderTop: '1px solid var(--color-border)', padding: '12px 16px 14px' }}
    >
      {/* Header */}
      <div className="flex items-center gap-2 mb-2.5">
        <div
          className="flex items-center justify-center rounded-full flex-shrink-0"
          style={{ width: 20, height: 20, background: 'var(--color-accent)' }}
        >
          <Bot size={11} color="var(--color-accent-contrast)" />
        </div>
        <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-secondary)' }}>
          Ask your advisor
        </span>
      </div>

      {/* Quick chips */}
      <div className="flex gap-1.5 mb-2.5 flex-wrap">
        {quickPrompts.map((p) => (
          <button
            key={p.label}
            onClick={() => go(p.q)}
            className="rounded-full"
            style={{
              fontSize: 10,
              fontWeight: 500,
              padding: '3px 10px',
              background: 'var(--color-surface-raise)',
              border: '1px solid var(--color-border)',
              color: 'var(--color-text-secondary)',
              cursor: 'pointer',
              transition: 'border-color 0.15s',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--color-accent)')}
            onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--color-border)')}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* Free-text input */}
      <div className="flex gap-1.5 items-center">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && go(input)}
          placeholder="Ask anything…"
          style={{
            flex: 1,
            fontSize: 12,
            borderRadius: 8,
            padding: '6px 10px',
            background: 'var(--color-surface-raise)',
            border: '1px solid var(--color-border)',
            color: 'var(--color-text-primary)',
          }}
        />
        <button
          onClick={() => go(input)}
          disabled={!input.trim()}
          style={{
            width: 28,
            height: 28,
            borderRadius: 8,
            flexShrink: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: input.trim() ? 'var(--color-accent)' : 'var(--color-surface-raise)',
            border: '1px solid var(--color-border)',
            color: input.trim() ? 'var(--color-accent-contrast)' : 'var(--color-text-muted)',
            cursor: input.trim() ? 'pointer' : 'default',
            transition: 'background 0.15s, color 0.15s',
          }}
        >
          <Send size={12} />
        </button>
      </div>
    </div>
  )
}

// ── Tab icon map ──────────────────────────────────────────────────────────────

const TAB_ICON: Record<TabId, React.ElementType> = {
  budgets:   Target,
  recurring: RefreshCw,
  groups:    Layers,
}

// ── Main panel ────────────────────────────────────────────────────────────────

interface RightPanelProps {
  isOpen: boolean
  onToggle: () => void
}

const PANEL_HIDDEN_ROUTES = ['/settings', '/login']

export default function RightPanel({ isOpen, onToggle }: RightPanelProps) {
  const location = useLocation()

  if (PANEL_HIDDEN_ROUTES.includes(location.pathname)) return null

  const [activeTab, setActiveTab] = useState<TabId>(
    ROUTE_DEFAULT_TAB[location.pathname] ?? FALLBACK_DEFAULT_TAB
  )

  useEffect(() => {
    setActiveTab(ROUTE_DEFAULT_TAB[location.pathname] ?? FALLBACK_DEFAULT_TAB)
  }, [location.pathname])

  // Allow pages to programmatically focus a tab (e.g. marking a transaction
  // recurring jumps the panel to the Recurring tab so the result is visible).
  const { requestedTab } = usePanel()
  useEffect(() => {
    if (requestedTab && ALL_TABS.includes(requestedTab.tab as TabId)) {
      setActiveTab(requestedTab.tab as TabId)
    }
  }, [requestedTab])

  return (
    <div style={{ position: 'fixed', right: 0, top: 0, height: '100%', zIndex: 40 }}>
      {/* Toggle arrow */}
      <button
        onClick={onToggle}
        aria-label={isOpen ? 'Hide panel' : 'Show panel'}
        style={{
          position: 'absolute',
          left: -14,
          top: '50%',
          transform: 'translateY(-50%)',
          width: 14,
          height: 52,
          borderRadius: '6px 0 0 6px',
          background: 'var(--color-surface)',
          border: '1px solid var(--color-border)',
          borderRight: 'none',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          cursor: 'pointer',
          zIndex: 41,
          transition: 'background 0.15s',
        }}
        onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--color-surface-raise)')}
        onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--color-surface)')}
      >
        {isOpen
          ? <ChevronRight size={10} style={{ color: 'var(--color-text-muted)' }} />
          : <ChevronLeft size={10} style={{ color: 'var(--color-text-muted)' }} />
        }
      </button>

      {/* Panel */}
      <aside
        style={{
          width: isOpen ? PANEL_WIDTH : 0,
          height: '100%',
          overflow: 'hidden',
          transition: 'width 0.25s ease',
          background: 'var(--color-surface)',
          borderLeft: '1px solid var(--color-border)',
        }}
      >
        <div style={{ width: PANEL_WIDTH, height: '100%', display: 'flex', flexDirection: 'column' }}>
          {/* Tab bar */}
          <div
            className="flex flex-shrink-0"
            style={{ borderBottom: '1px solid var(--color-border)', paddingTop: 14, paddingLeft: 4, paddingRight: 4 }}
          >
            {ALL_TABS.map((tab) => {
              const Icon = TAB_ICON[tab]
              const isActive = activeTab === tab
              return (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className="flex flex-col items-center gap-0.5 px-1 pb-2 flex-1 justify-center"
                  title={TAB_LABEL[tab]}
                  style={{
                    fontSize: 11,
                    fontWeight: isActive ? 600 : 500,
                    color: isActive ? 'var(--color-accent-text)' : 'var(--color-text-muted)',
                    borderTop: 'none',
                    borderLeft: 'none',
                    borderRight: 'none',
                    borderBottom: isActive ? '2px solid var(--color-accent)' : '2px solid transparent',
                    marginBottom: -1,
                    background: 'none',
                    cursor: 'pointer',
                    transition: 'color 0.15s',
                    whiteSpace: 'nowrap',
                  }}
                >
                  <Icon size={13} />
                  {TAB_LABEL[tab]}
                </button>
              )
            })}
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-y-auto px-5 py-4" style={{ scrollbarWidth: 'thin' }}>
            {activeTab === 'budgets'   && <BudgetTab />}
            {activeTab === 'recurring' && <RecurringTab />}
            {activeTab === 'groups'    && <GroupsTab />}
          </div>

          {/* Advisor widget — pinned at bottom */}
          <AdvisorWidget />
        </div>
      </aside>
    </div>
  )
}

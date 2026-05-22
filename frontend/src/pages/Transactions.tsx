import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useIsMobile } from '../hooks/useIsMobile'
import { Search, Check, ChevronUp, ChevronDown, Plus, Trash2, X, Tag, AlertTriangle, Repeat } from 'lucide-react'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts'
import { transactionsApi, categoriesApi, workspaceApi, merchantsApi } from '../lib/api'
import type { Transaction } from '../lib/api'
import { useFilters } from '../context/FilterContext'
import { useWorkspace } from '../context/WorkspaceContext'
import { useTheme } from '../context/ThemeContext'
import { usePanel } from '../context/PanelContext'
import { PANEL_WIDTH } from '../components/RightPanel'
import { formatCurrency, formatDate, CHART_COLORS_DARK, CHART_COLORS_LIGHT, getCategoryColor } from '../lib/utils'
import SkeletonRow from '../components/SkeletonRow'
import { ActiveGroupBanner } from '../components/RightPanel'


interface EditState {
  [id: number]: {
    category?: string
    amount?: string
    notes?: string
  }
}

interface SavedState {
  [id: number]: boolean
}

const today = new Date().toISOString().split('T')[0]

interface AddForm {
  name: string
  date: string
  amount: string
  category: string
  notes: string
}

export default function Transactions() {
  const { range, institution, account } = useFilters()
  const { activeGroup } = useWorkspace()
  const { theme } = useTheme()
  const { panelOpen } = usePanel()
  const isMobile = useIsMobile()
  const rhsWidth = panelOpen ? PANEL_WIDTH : 0
  const [search, setSearch] = useState('')
  const [editState, setEditState] = useState<EditState>({})
  const [saved, setSaved] = useState<SavedState>({})
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editingMerchant, setEditingMerchant] = useState<number | null>(null)
  const [merchantDraft, setMerchantDraft] = useState('')
  const [showAdd, setShowAdd] = useState(false)
  const [addForm, setAddForm] = useState<AddForm>({ name: '', date: today, amount: '', category: 'Other', notes: '' })
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [merchantSuggestion, setMerchantSuggestion] = useState<{ merchant: string; category: string } | null>(null)
  const [applyDialog, setApplyDialog] = useState<{ merchant: string; category: string } | null>(null)
  const [expandedDup, setExpandedDup] = useState<number | null>(null)
  const qc = useQueryClient()
  const chartColors = theme === 'dark' ? CHART_COLORS_DARK : CHART_COLORS_LIGHT

  const { data: transactions = [], isLoading } = useQuery({
    queryKey: ['transactions', range, institution, account, search],
    queryFn: () => transactionsApi.list({ range, institution, account, search }),
  })

  const { data: userCategories = [] } = useQuery({
    queryKey: ['user-categories'],
    queryFn: () => categoriesApi.userCategories(),
    staleTime: 300_000,
  })

  const { data: categoryOverrides = {} } = useQuery({
    queryKey: ['merchant-category-overrides'],
    queryFn: () => merchantsApi.categoryOverrides(),
  })

  const patchMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: { category?: string; amount?: number; notes?: string } }) =>
      transactionsApi.patch(id, data),
    onSuccess: (_, { id }) => {
      setSaved((s) => ({ ...s, [id]: true }))
      setTimeout(() => setSaved((s) => ({ ...s, [id]: false })), 1500)
      qc.invalidateQueries({ queryKey: ['transactions'] })
      qc.invalidateQueries({ queryKey: ['summary'] })
      qc.invalidateQueries({ queryKey: ['monthly'] })
      qc.invalidateQueries({ queryKey: ['categories'] })
    },
  })


  const createMutation = useMutation({
    mutationFn: (data: { name: string; date: string; amount: number; category?: string; notes?: string }) =>
      transactionsApi.create(data),
    onSuccess: () => {
      setShowAdd(false)
      setAddForm({ name: '', date: today, amount: '', category: 'Other', notes: '' })
      qc.invalidateQueries({ queryKey: ['transactions'] })
      qc.invalidateQueries({ queryKey: ['summary'] })
      qc.invalidateQueries({ queryKey: ['monthly'] })
      qc.invalidateQueries({ queryKey: ['categories'] })
    },
  })

  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null)

  const markRecurringMutation = useMutation({
    mutationFn: (transaction_id: string) => workspaceApi.markRecurringFromTransaction(transaction_id),
    onSuccess: (data) => {
      setToast({ message: `Marked "${data.merchant_key}" as recurring`, type: 'success' })
      setTimeout(() => setToast(null), 2500)
      qc.invalidateQueries({ queryKey: ['recurring'] })
      qc.invalidateQueries({ queryKey: ['recurring-rules'] })
      qc.invalidateQueries({ queryKey: ['tracker'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => transactionsApi.delete(id),
    onSuccess: () => {
      setConfirmDelete(null)
      qc.invalidateQueries({ queryKey: ['transactions'] })
      qc.invalidateQueries({ queryKey: ['summary'] })
      qc.invalidateQueries({ queryKey: ['monthly'] })
      qc.invalidateQueries({ queryKey: ['categories'] })
    },
  })

  const handleAddSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const amount = parseFloat(addForm.amount)
    if (!addForm.name.trim() || isNaN(amount)) return
    createMutation.mutate({
      name: addForm.name.trim(),
      date: addForm.date,
      amount,
      category: addForm.category || undefined,
      notes: addForm.notes.trim() || undefined,
    })
  }

  const renameMerchantMutation = useMutation({
    mutationFn: ({ rawName, displayName }: { rawName: string; displayName: string }) =>
      merchantsApi.saveOverride(rawName, displayName),
    onSuccess: (_data, vars) => {
      setToast({ message: `Renamed merchant to "${vars.displayName}"`, type: 'success' })
      setTimeout(() => setToast(null), 2500)
      qc.invalidateQueries({ queryKey: ['transactions'] })
      qc.invalidateQueries({ queryKey: ['merchants'] })
    },
    onError: () => {
      setToast({ message: 'Failed to rename merchant', type: 'error' })
      setTimeout(() => setToast(null), 2500)
    },
  })

  const saveMerchantCategoryMutation = useMutation({
    mutationFn: ({ merchant, category }: { merchant: string; category: string }) =>
      merchantsApi.saveCategoryOverride(merchant, category),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: ['merchant-category-overrides'] })
      setMerchantSuggestion(null)
      setApplyDialog(vars)
    },
  })

  const applyHistoricalMutation = useMutation({
    mutationFn: ({ merchant, category }: { merchant: string; category: string }) =>
      merchantsApi.applyHistoricalCategory(merchant, category),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['transactions'] })
      setApplyDialog(null)
    },
  })

  const dismissDupMutation = useMutation({
    mutationFn: ({ id, otherId }: { id: string; otherId: string }) =>
      transactionsApi.dismissDuplicate(id, otherId),
    onSuccess: () => {
      setExpandedDup(null)
      qc.invalidateQueries({ queryKey: ['transactions'] })
    },
  })

  const commitMerchant = (rawName: string) => {
    const trimmed = merchantDraft.trim()
    if (trimmed && trimmed !== rawName) renameMerchantMutation.mutate({ rawName, displayName: trimmed })
    setEditingMerchant(null)
  }

  const handleBlur = (tx: Transaction, field: 'category' | 'amount' | 'notes') => {
    const edit = editState[tx.id]
    if (!edit) return
    const value = edit[field]
    if (value === undefined) return

    const patch: { category?: string; amount?: number; notes?: string } = {}
    if (field === 'category' && value !== tx.category) patch.category = value
    if (field === 'amount') {
      const numVal = parseFloat(value as string)
      if (!isNaN(numVal) && numVal !== tx.amount) patch.amount = numVal
    }
    if (field === 'notes' && value !== tx.notes) patch.notes = value as string

    if (Object.keys(patch).length > 0) {
      patchMutation.mutate({ id: tx.id, data: patch })
    }
  }

  const setEdit = (id: number, field: 'category' | 'amount' | 'notes', value: string) => {
    setEditState((s) => ({ ...s, [id]: { ...s[id], [field]: value } }))
  }

  const { data: groupTxData } = useQuery({
    queryKey: ['group-tx-ids', activeGroup?.id],
    queryFn: () => workspaceApi.groupTransactions(activeGroup!.id),
    enabled: !!activeGroup,
  })

  const groupTxIds = new Set((groupTxData?.transaction_ids ?? []).map(String))

  const tagMutation = useMutation({
    mutationFn: ({ txId, inGroup }: { txId: string; inGroup: boolean }) =>
      inGroup
        ? workspaceApi.removeTransaction(activeGroup!.id, txId)
        : workspaceApi.addTransaction(activeGroup!.id, txId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['group-tx-ids', activeGroup?.id] })
      qc.invalidateQueries({ queryKey: ['groups'] })
    },
  })

  const total = transactions.reduce((s, t) => s + t.amount, 0)
  const avg = transactions.length > 0 ? total / transactions.length : 0
  const largest = transactions.length > 0 ? Math.max(...transactions.map((t) => t.amount)) : 0

  // Drawer data — derived from loaded transactions, no extra fetch needed
  const categoryData = Object.entries(
    transactions.reduce((acc, t) => {
      if (t.amount <= 0) return acc
      const cat = t.category || 'Uncategorized'
      acc[cat] = (acc[cat] || 0) + t.amount
      return acc
    }, {} as Record<string, number>)
  )
    .map(([category, value]) => ({ category, value }))
    .sort((a, b) => b.value - a.value)

  const merchantData = Object.entries(
    transactions.reduce((acc, t) => {
      if (t.amount <= 0) return acc
      const m = t.merchant_normalized || t.name
      if (!acc[m]) acc[m] = { total: 0, count: 0 }
      acc[m].total += t.amount
      acc[m].count += 1
      return acc
    }, {} as Record<string, { total: number; count: number }>)
  )
    .map(([name, { total: mTotal, count }]) => ({ name, total: mTotal, count }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 8)

  return (
    <div className="space-y-4 fade-in" style={{ paddingBottom: drawerOpen ? 'calc(45vh + 56px)' : 56, transition: 'padding-bottom 0.25s ease' }}>
      <ActiveGroupBanner />

      {toast && (
        <div
          style={{
            position: 'fixed', bottom: 80, left: '50%', transform: 'translateX(-50%)',
            background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)',
            padding: '10px 16px', borderRadius: 8, fontSize: 13,
            color: 'var(--color-text-primary)', zIndex: 60,
            boxShadow: '0 4px 12px rgba(0,0,0,0.2)',
            display: 'flex', alignItems: 'center', gap: 8,
          }}
        >
          {toast.type === 'success' ? (
            <span
              style={{
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                width: 18, height: 18, borderRadius: '50%',
                background: 'var(--color-positive)', color: '#000', flexShrink: 0,
              }}
            >
              <Check size={12} strokeWidth={3} />
            </span>
          ) : (
            <span
              style={{
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                width: 18, height: 18, borderRadius: '50%',
                background: 'var(--color-negative)', color: '#fff', flexShrink: 0,
              }}
            >
              <X size={12} strokeWidth={3} />
            </span>
          )}
          {toast.message}
        </div>
      )}

      {/* Add Transaction Modal */}
      {showAdd && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setShowAdd(false)}
        >
          <div
            style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 12, padding: 24, width: 'min(400px, 90vw)', position: 'relative' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between" style={{ marginBottom: 16 }}>
              <h2 style={{ fontSize: 16, fontWeight: 600, color: 'var(--color-text-primary)' }}>Add Transaction</h2>
              <button onClick={() => setShowAdd(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-muted)', padding: 4 }}>
                <X size={16} />
              </button>
            </div>
            <form onSubmit={handleAddSubmit} className="flex flex-col gap-3">
              <div>
                <label style={{ fontSize: 12, color: 'var(--color-text-muted)', display: 'block', marginBottom: 4 }}>Name *</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. Coffee shop"
                  value={addForm.name}
                  onChange={(e) => setAddForm((f) => ({ ...f, name: e.target.value }))}
                  style={{ width: '100%' }}
                />
              </div>
              <div className="flex gap-3">
                <div style={{ flex: 1 }}>
                  <label style={{ fontSize: 12, color: 'var(--color-text-muted)', display: 'block', marginBottom: 4 }}>Date *</label>
                  <input
                    type="date"
                    required
                    value={addForm.date}
                    onChange={(e) => setAddForm((f) => ({ ...f, date: e.target.value }))}
                    style={{ width: '100%' }}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={{ fontSize: 12, color: 'var(--color-text-muted)', display: 'block', marginBottom: 4 }}>Amount * <span style={{ fontWeight: 400 }}>(negative = income)</span></label>
                  <input
                    type="number"
                    required
                    step="0.01"
                    placeholder="0.00"
                    value={addForm.amount}
                    onChange={(e) => setAddForm((f) => ({ ...f, amount: e.target.value }))}
                    style={{ width: '100%' }}
                  />
                </div>
              </div>
              <div>
                <label style={{ fontSize: 12, color: 'var(--color-text-muted)', display: 'block', marginBottom: 4 }}>Category</label>
                <select
                  value={addForm.category}
                  onChange={(e) => setAddForm((f) => ({ ...f, category: e.target.value }))}
                  style={{ width: '100%' }}
                >
                  {userCategories.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
              <div>
                <label style={{ fontSize: 12, color: 'var(--color-text-muted)', display: 'block', marginBottom: 4 }}>Notes</label>
                <input
                  type="text"
                  placeholder="Optional note"
                  value={addForm.notes}
                  onChange={(e) => setAddForm((f) => ({ ...f, notes: e.target.value }))}
                  style={{ width: '100%' }}
                />
              </div>
              <div className="flex gap-2 justify-end" style={{ marginTop: 4 }}>
                <button type="button" onClick={() => setShowAdd(false)} style={{ padding: '6px 14px', fontSize: 13, background: 'none', border: '1px solid var(--color-border)', borderRadius: 6, cursor: 'pointer', color: 'var(--color-text-secondary)' }}>
                  Cancel
                </button>
                <button type="submit" disabled={createMutation.isPending} style={{ padding: '6px 14px', fontSize: 13, background: 'var(--color-accent)', border: 'none', borderRadius: 6, cursor: 'pointer', color: '#fff', opacity: createMutation.isPending ? 0.6 : 1 }}>
                  {createMutation.isPending ? 'Adding…' : 'Add'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Confirm Delete Dialog */}
      {confirmDelete && (
        <div
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setConfirmDelete(null)}
        >
          <div
            style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 12, padding: 24, width: 'min(320px, 90vw)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 style={{ fontSize: 15, fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 8 }}>Delete transaction?</h2>
            <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 20 }}>This cannot be undone.</p>
            <div className="flex gap-2 justify-end">
              <button onClick={() => setConfirmDelete(null)} style={{ padding: '6px 14px', fontSize: 13, background: 'none', border: '1px solid var(--color-border)', borderRadius: 6, cursor: 'pointer', color: 'var(--color-text-secondary)' }}>
                Cancel
              </button>
              <button
                onClick={() => deleteMutation.mutate(confirmDelete)}
                disabled={deleteMutation.isPending}
                style={{ padding: '6px 14px', fontSize: 13, background: 'var(--color-negative)', border: 'none', borderRadius: 6, cursor: 'pointer', color: '#fff', opacity: deleteMutation.isPending ? 0.6 : 1 }}
              >
                {deleteMutation.isPending ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 700, color: 'var(--color-text-primary)' }}>Transactions</h1>
          <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 2 }}>
            Click fields to edit
          </p>
        </div>
        <div className="flex items-center gap-3 flex-1 justify-end" style={{ minWidth: 0 }}>
          <button
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-1.5 flex-shrink-0"
            style={{ padding: '6px 12px', fontSize: 13, background: 'var(--color-accent)', border: 'none', borderRadius: 6, cursor: 'pointer', color: '#fff', fontWeight: 500 }}
          >
            <Plus size={14} /> Add
          </button>
          <div className="relative flex-1" style={{ minWidth: 0, maxWidth: 280 }}>
            <Search
              size={14}
              style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--color-text-muted)' }}
            />
            <input
              type="search"
              placeholder="Search transactions…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ paddingLeft: 30, width: '100%' }}
            />
          </div>
        </div>
      </div>

      <div
        className="rounded-xl"
        style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
      >
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Name</th>
                <th>Merchant</th>
                <th>Category</th>
                <th style={{ textAlign: 'right' }}>Amount</th>
                <th>Institution</th>
                <th>Notes</th>
                <th style={{ width: 30 }}></th>
                <th style={{ width: 28 }}></th>
                {activeGroup && <th style={{ width: 28 }}></th>}
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <SkeletonRow cols={activeGroup ? 9 : 8} rows={10} />
              ) : transactions.length === 0 ? (
                <tr>
                  <td colSpan={8} style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: '32px 0' }}>
                    No transactions found
                  </td>
                </tr>
              ) : (
                transactions.map((tx) => {
                  const isEditingAmount = editState[tx.id]?.amount !== undefined
                  const currentAmount = isEditingAmount
                    ? editState[tx.id].amount!
                    : formatCurrency(tx.amount)
                  const currentCategory = editState[tx.id]?.category !== undefined
                    ? editState[tx.id].category!
                    : tx.category
                  const currentNotes = editState[tx.id]?.notes !== undefined
                    ? editState[tx.id].notes!
                    : (tx.notes ?? '')

                  return (
                    <React.Fragment key={tx.id}>
                    <tr>
                      <td style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
                        {formatDate(tx.date)}
                      </td>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4, maxWidth: 170 }}>
                          <span style={{ fontSize: 12, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {tx.name}
                          </span>
                          {tx.is_potential_duplicate && (
                            <button
                              onClick={() => setExpandedDup(expandedDup === tx.id ? null : tx.id)}
                              title="Possible duplicate — click to review"
                              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, display: 'flex', flexShrink: 0 }}
                            >
                              <AlertTriangle size={12} style={{ color: '#e8c17a' }} />
                            </button>
                          )}
                        </div>
                      </td>
                      <td className="editable-cell">
                        {editingMerchant === tx.id ? (
                          <input
                            type="text"
                            value={merchantDraft}
                            autoFocus
                            onChange={(e) => setMerchantDraft(e.target.value)}
                            onBlur={() => commitMerchant(tx.merchant_normalized || '')}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') commitMerchant(tx.merchant_normalized || '')
                              if (e.key === 'Escape') setEditingMerchant(null)
                            }}
                            style={{ fontSize: 12, width: 130 }}
                          />
                        ) : (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 4, maxWidth: 150 }}>
                            <span
                              onDoubleClick={() => { setMerchantDraft(tx.merchant_normalized || ''); setEditingMerchant(tx.id) }}
                              title={categoryOverrides[tx.merchant_normalized] ? `Rule: always ${categoryOverrides[tx.merchant_normalized]} · double-click to rename` : 'Double-click to rename'}
                              style={{ fontSize: 12, color: 'var(--color-text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', cursor: 'text', flex: 1 }}
                            >
                              {tx.merchant_normalized || '—'}
                            </span>
                            {categoryOverrides[tx.merchant_normalized] && (
                              <div
                                title={`Category rule: ${categoryOverrides[tx.merchant_normalized]}`}
                                style={{ width: 6, height: 6, borderRadius: '50%', background: getCategoryColor(categoryOverrides[tx.merchant_normalized]), flexShrink: 0 }}
                              />
                            )}
                          </div>
                        )}
                      </td>
                      <td className="editable-cell">
                        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                          <div style={{ width: 7, height: 7, borderRadius: '50%', background: getCategoryColor(currentCategory), flexShrink: 0 }} />
                          <select
                            value={currentCategory}
                            onChange={(e) => {
                              const newCat = e.target.value
                              setEdit(tx.id, 'category', newCat)
                              patchMutation.mutate({ id: tx.id, data: { category: newCat } })
                              if (tx.merchant_normalized) {
                                setMerchantSuggestion({ merchant: tx.merchant_normalized, category: newCat })
                              }
                            }}
                            style={{ fontSize: 12, minWidth: 130, border: 'none', background: 'var(--color-surface)', color: 'var(--color-text-primary)', padding: '2px 24px 2px 2px' }}
                          >
                            {userCategories.map((c) => (
                              <option key={c} value={c}>{c}</option>
                            ))}
                            {!userCategories.includes(currentCategory) && currentCategory && (
                              <option value={currentCategory}>{currentCategory}</option>
                            )}
                          </select>
                        </div>
                      </td>
                      <td style={{ textAlign: 'right' }} className="editable-cell">
                        <input
                          type="text"
                          inputMode="decimal"
                          value={currentAmount}
                          onFocus={() => {
                            if (!isEditingAmount) setEdit(tx.id, 'amount', tx.amount.toString())
                          }}
                          onChange={(e) => setEdit(tx.id, 'amount', e.target.value)}
                          onBlur={() => {
                            handleBlur(tx, 'amount')
                            // Revert to formatted display after blur
                            setEditState((s) => {
                              const next = { ...s }
                              if (next[tx.id]) {
                                const { amount: _a, ...rest } = next[tx.id]
                                if (Object.keys(rest).length === 0) delete next[tx.id]
                                else next[tx.id] = rest
                              }
                              return next
                            })
                          }}
                          style={{
                            fontFamily: 'monospace',
                            fontSize: 12,
                            textAlign: 'right',
                            width: 90,
                            color: tx.amount < 0 ? 'var(--color-positive)' : 'var(--color-text-primary)',
                          }}
                        />
                      </td>
                      <td style={{ fontSize: 12, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
                        {tx.institution}
                      </td>
                      <td className="editable-cell">
                        <input
                          type="text"
                          value={currentNotes}
                          placeholder="Add note…"
                          onChange={(e) => setEdit(tx.id, 'notes', e.target.value)}
                          onBlur={() => handleBlur(tx, 'notes')}
                          style={{ fontSize: 12, minWidth: 120 }}
                        />
                      </td>
                      <td>
                        {saved[tx.id] && (
                          <Check size={13} style={{ color: 'var(--color-positive)' }} />
                        )}
                        {tx.has_user_override && !saved[tx.id] && (
                          <div
                            title="Has overrides"
                            style={{
                              width: 6,
                              height: 6,
                              borderRadius: '50%',
                              background: 'var(--color-accent)',
                              margin: '0 auto',
                            }}
                          />
                        )}
                      </td>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                          <button
                            onClick={() => markRecurringMutation.mutate(String(tx.id))}
                            disabled={markRecurringMutation.isPending}
                            title="Mark as recurring"
                            className="delete-row-btn"
                            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-muted)', padding: 2, opacity: 0, transition: 'opacity 0.15s', display: 'flex', alignItems: 'center' }}
                          >
                            <Repeat size={12} />
                          </button>
                          <button
                            onClick={() => setConfirmDelete(String(tx.id))}
                            title="Delete transaction"
                            className="delete-row-btn"
                            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-muted)', padding: 2, opacity: 0, transition: 'opacity 0.15s', display: 'flex', alignItems: 'center' }}
                          >
                            <Trash2 size={12} />
                          </button>
                        </div>
                      </td>
                      {activeGroup && (() => {
                        const txId = String(tx.id)
                        const inGroup = groupTxIds.has(txId)
                        return (
                          <td>
                            <button
                              onClick={() => tagMutation.mutate({ txId, inGroup })}
                              title={inGroup ? `Remove from ${activeGroup.name}` : `Add to ${activeGroup.name}`}
                              style={{
                                width: 16,
                                height: 16,
                                borderRadius: '50%',
                                background: inGroup ? activeGroup.color : 'transparent',
                                border: `2px solid ${inGroup ? activeGroup.color : 'var(--color-border)'}`,
                                display: 'block',
                                cursor: 'pointer',
                                transition: 'background 0.15s, border-color 0.15s',
                              }}
                            />
                          </td>
                        )
                      })()}
                    </tr>
                    {expandedDup === tx.id && tx.is_potential_duplicate && (() => {
                      const dupOf = tx.potential_dup_of
                        ? (typeof tx.potential_dup_of === 'string' ? JSON.parse(tx.potential_dup_of) : tx.potential_dup_of)
                        : null
                      return (
                        <tr>
                          <td colSpan={activeGroup ? 10 : 9} style={{ padding: 0 }}>
                            <div style={{ background: 'rgba(232,193,122,0.07)', borderTop: '1px solid rgba(232,193,122,0.25)', borderBottom: '1px solid rgba(232,193,122,0.25)', padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 16 }}>
                              <AlertTriangle size={13} style={{ color: '#e8c17a', flexShrink: 0 }} />
                              <div style={{ flex: 1, fontSize: 12, color: 'var(--color-text-secondary)' }}>
                                <span style={{ fontWeight: 600, color: 'var(--color-text-primary)' }}>Possible duplicate</span>
                                {dupOf && (
                                  <span> — looks like <span style={{ color: 'var(--color-text-primary)' }}>{dupOf.name}</span> on {dupOf.date} for {formatCurrency(dupOf.amount)}</span>
                                )}
                              </div>
                              <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                                <button
                                  onClick={() => deleteMutation.mutate(String(tx.id))}
                                  disabled={deleteMutation.isPending}
                                  style={{ fontSize: 11, padding: '3px 10px', borderRadius: 5, background: 'var(--color-negative)', border: 'none', color: '#fff', cursor: 'pointer', fontWeight: 600 }}
                                >
                                  Delete this one
                                </button>
                                <button
                                  onClick={() => dupOf && dismissDupMutation.mutate({ id: String(tx.id), otherId: dupOf.id })}
                                  disabled={dismissDupMutation.isPending}
                                  style={{ fontSize: 11, padding: '3px 10px', borderRadius: 5, background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)', cursor: 'pointer' }}
                                >
                                  Keep both
                                </button>
                              </div>
                            </div>
                          </td>
                        </tr>
                      )
                    })()}
                    </React.Fragment>
                  )
                })
              )}
            </tbody>
          </table>
        </div>

      </div>

      {/* Slide-up drawer */}
      <div
        style={{
          position: 'fixed',
          bottom: isMobile ? 60 : 56,
          left: isMobile ? 0 : 220,
          right: isMobile ? 0 : rhsWidth,
          height: drawerOpen ? '45vh' : 0,
          overflow: 'hidden',
          transition: 'height 0.25s ease',
          background: 'var(--color-surface)',
          borderTop: drawerOpen ? '1px solid var(--color-border)' : 'none',
          zIndex: 9,
        }}
      >
        <div className="flex h-full gap-0" style={{ overflow: 'hidden' }}>
          {/* Categories pie */}
          <div className="flex flex-col flex-1 p-5" style={{ borderRight: '1px solid var(--color-border)', overflow: 'hidden' }}>
            <p style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8, flexShrink: 0 }}>
              By Category
            </p>
            <div className="flex flex-1 gap-4 min-h-0">
              <ResponsiveContainer width="55%" height="100%">
                <PieChart>
                  <Pie
                    data={categoryData}
                    dataKey="value"
                    nameKey="category"
                    cx="50%" cy="50%"
                    innerRadius="40%" outerRadius="70%"
                    paddingAngle={2}
                  >
                    {categoryData.map((_, i) => (
                      <Cell key={i} fill={chartColors[i % chartColors.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    formatter={(v: number) => formatCurrency(v)}
                    contentStyle={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)', borderRadius: 8, fontSize: 12, color: 'var(--color-text-primary)' }}
                    itemStyle={{ color: 'var(--color-text-primary)' }}
                    labelStyle={{ color: 'var(--color-text-muted)' }}
                    isAnimationActive={false}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex flex-col gap-1.5 justify-center overflow-y-auto" style={{ flex: 1 }}>
                {categoryData.slice(0, 7).map((c, i) => (
                  <div key={c.category} className="flex items-center gap-2 min-w-0">
                    <div style={{ width: 7, height: 7, borderRadius: '50%', background: chartColors[i % chartColors.length], flexShrink: 0 }} />
                    <span style={{ fontSize: 11, color: 'var(--color-text-secondary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.category}</span>
                    <span style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--color-text-muted)', flexShrink: 0 }}>{formatCurrency(c.value)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Top merchants */}
          <div className="flex flex-col flex-1 p-5" style={{ overflow: 'hidden' }}>
            <p style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8, flexShrink: 0 }}>
              Top Merchants
            </p>
            <div className="flex flex-col gap-2 overflow-y-auto flex-1">
              {merchantData.map((m, i) => {
                const pct = total > 0 ? (m.total / total) * 100 : 0
                return (
                  <div key={m.name}>
                    <div className="flex items-center justify-between mb-1">
                      <div className="flex items-center gap-2 min-w-0">
                        <div style={{ width: 7, height: 7, borderRadius: '50%', background: chartColors[i % chartColors.length], flexShrink: 0 }} />
                        <span style={{ fontSize: 12, color: 'var(--color-text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.name}</span>
                      </div>
                      <span style={{ fontSize: 12, fontFamily: 'monospace', color: 'var(--color-text-primary)', flexShrink: 0, marginLeft: 8 }}>{formatCurrency(m.total)}</span>
                    </div>
                    <div style={{ height: 3, borderRadius: 2, background: 'var(--color-border)' }}>
                      <div style={{ height: 3, borderRadius: 2, width: `${pct}%`, background: chartColors[i % chartColors.length], transition: 'width 0.3s' }} />
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      </div>

      {/* Merchant category suggestion toast */}
      {merchantSuggestion && (
        <div
          style={{
            position: 'fixed', bottom: 68, right: rhsWidth + 24, zIndex: 40,
            background: 'var(--color-surface)',
            border: '1px solid var(--color-border)',
            borderRadius: 10, padding: '12px 16px',
            boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
            maxWidth: 320,
          }}
        >
          <div className="flex items-start gap-2 mb-3">
            <Tag size={14} style={{ color: 'var(--color-accent)', flexShrink: 0, marginTop: 1 }} />
            <p style={{ fontSize: 13, color: 'var(--color-text-primary)', lineHeight: 1.4 }}>
              Set <strong>{merchantSuggestion.merchant}</strong> to{' '}
              <strong>{merchantSuggestion.category}</strong> for all future transactions?
            </p>
          </div>
          <div className="flex gap-2 justify-end">
            <button
              onClick={() => setMerchantSuggestion(null)}
              style={{ fontSize: 12, padding: '4px 10px', borderRadius: 5, background: 'none', border: '1px solid var(--color-border)', color: 'var(--color-text-muted)', cursor: 'pointer' }}
            >
              Dismiss
            </button>
            <button
              disabled={saveMerchantCategoryMutation.isPending}
              onClick={() => saveMerchantCategoryMutation.mutate(merchantSuggestion)}
              style={{ fontSize: 12, padding: '4px 10px', borderRadius: 5, background: 'var(--color-accent)', border: 'none', color: '#000', fontWeight: 600, cursor: 'pointer', opacity: saveMerchantCategoryMutation.isPending ? 0.6 : 1 }}
            >
              Set rule
            </button>
          </div>
        </div>
      )}

      {/* Apply historical dialog */}
      {applyDialog && (
        <div
          style={{ position: 'fixed', inset: 0, zIndex: 50, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setApplyDialog(null)}
        >
          <div
            className="rounded-xl p-6"
            style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', maxWidth: 420, width: '90%', boxShadow: '0 20px 60px rgba(0,0,0,0.4)' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 mb-3">
              <Tag size={16} style={{ color: 'var(--color-accent)' }} />
              <p style={{ fontSize: 14, fontWeight: 600, color: 'var(--color-text-primary)' }}>Apply to past transactions?</p>
            </div>
            <p style={{ fontSize: 13, color: 'var(--color-text-secondary)', marginBottom: 4 }}>
              You set <strong>{applyDialog.merchant}</strong> to <strong>{applyDialog.category}</strong>.
            </p>
            <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 20 }}>
              Apply this category to all recorded past transactions from this merchant?
              Individual transaction overrides will not be affected.
            </p>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setApplyDialog(null)}
                style={{ fontSize: 13, padding: '6px 14px', borderRadius: 6, background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)', cursor: 'pointer' }}
              >
                Future only
              </button>
              <button
                disabled={applyHistoricalMutation.isPending}
                onClick={() => applyHistoricalMutation.mutate(applyDialog)}
                style={{ fontSize: 13, padding: '6px 14px', borderRadius: 6, background: 'var(--color-accent)', color: '#000', fontWeight: 600, cursor: 'pointer', border: 'none', opacity: applyHistoricalMutation.isPending ? 0.6 : 1 }}
              >
                {applyHistoricalMutation.isPending ? 'Applying…' : 'Apply to all past'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Bottom bar */}
      <div
        className="flex items-center gap-4 px-4 flex-wrap"
        style={{
          position: 'fixed',
          bottom: isMobile ? 60 : 0,
          left: isMobile ? 0 : 220,
          right: isMobile ? 0 : rhsWidth,
          height: 56,
          borderTop: '1px solid var(--color-border)',
          background: 'var(--color-surface-raise)',
          zIndex: 10,
        }}
      >
        <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
          {transactions.length} transactions
        </span>
        <span style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
          Total <span style={{ fontFamily: 'monospace', fontSize: 15, fontWeight: 600, color: 'var(--color-negative)', marginLeft: 6 }}>{formatCurrency(total)}</span>
        </span>
        <span style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
          Avg <span style={{ fontFamily: 'monospace', fontSize: 15, fontWeight: 600, color: 'var(--color-text-primary)', marginLeft: 6 }}>{formatCurrency(avg)}</span>
        </span>
        <span style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
          Largest <span style={{ fontFamily: 'monospace', fontSize: 15, fontWeight: 600, color: 'var(--color-text-primary)', marginLeft: 6 }}>{formatCurrency(largest)}</span>
        </span>
        <button
          onClick={() => setDrawerOpen((o) => !o)}
          className="ml-auto flex items-center gap-1.5"
          style={{ fontSize: 12, color: 'var(--color-text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: '4px 8px' }}
        >
          {drawerOpen ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
          {drawerOpen ? 'Hide' : 'Insights'}
        </button>
      </div>
    </div>
  )
}

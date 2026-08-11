import React, { useState, useMemo, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Search, Check, Plus, Trash2, X, Tag, Download, AlertTriangle, Repeat, ChevronUp, ChevronDown, ChevronsUpDown, Pencil, SlidersHorizontal, Eye, EyeOff } from 'lucide-react'
import { ledgerApi, transactionsApi, workspaceApi, merchantsApi, categoriesApi } from '../lib/api'
import type { LedgerRow, RecurringRule } from '../lib/api'
import { useFilters } from '../context/FilterContext'
import { useWorkspace } from '../context/WorkspaceContext'
import { usePanel } from '../context/PanelContext'
import { PANEL_WIDTH } from '../components/RightPanel'
import { useIsMobile } from '../hooks/useIsMobile'
import { formatCurrency, formatDate, getCategoryColor } from '../lib/utils'
import SkeletonRow from '../components/SkeletonRow'
import RangeSelect from '../components/RangeSelect'
import { ActiveGroupBanner } from '../components/RightPanel'
import TransactionDrawer from '../components/TransactionDrawer'

// ── Shared escape-key hook ────────────────────────────────────────────────
function useEscapeKey(active: boolean, onClose: () => void) {
  useEffect(() => {
    if (!active) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [active, onClose])
}

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

type SortKey = 'date' | 'name' | 'merchant' | 'category' | 'amount' | 'institution'


const today = new Date().toISOString().split('T')[0]

interface AddForm {
  name: string
  date: string
  amount: string
  category: string
  notes: string
}

export default function Transactions() {
  const { institution, account } = useFilters()
  // Range is contextual to this page; the table defaults to all time so it never
  // silently hides transactions.
  const [range, setRange] = useState('all')
  const { activeGroup } = useWorkspace()
  const { panelOpen, focusTab } = usePanel()
  const isMobile = useIsMobile()
  const rhsWidth = panelOpen ? PANEL_WIDTH : 0
  const [search, setSearch] = useState('')
  // Default to spending + income; transfers + duplicates stay hidden.
  const [types, setTypes] = useState<string[]>(['debit', 'credit'])
  const [categories, setCategories] = useState<string[]>([])  // empty = all
  const [catMenuOpen, setCatMenuOpen] = useState(false)
  const [showMenuOpen, setShowMenuOpen] = useState(false)
  const [rulesOpen, setRulesOpen] = useState(false)
  const [ruleDraft, setRuleDraft] = useState<{ key: string; value: string } | null>(null)
  const [showTransfers, setShowTransfers] = useState(false)
  const [showDuplicates, setShowDuplicates] = useState(false)
  const [sortBy, setSortBy] = useState<SortKey>('date')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [editState, setEditState] = useState<EditState>({})
  const [saved, setSaved] = useState<SavedState>({})
  const [showAdd, setShowAdd] = useState(false)
  const [addForm, setAddForm] = useState<AddForm>({ name: '', date: today, amount: '', category: 'Other', notes: '' })
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [merchantSuggestion, setMerchantSuggestion] = useState<{ merchant: string; category: string } | null>(null)
  const [applyDialog, setApplyDialog] = useState<{ merchant: string; category: string } | null>(null)
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null)
  // Drawer: store selected row id; derive the full row object from current rows
  const [selectedRowId, setSelectedRowId] = useState<number | null>(null)
  const qc = useQueryClient()

  // Modal autofocus refs (Task 4)
  const addNameInputRef = useRef<HTMLInputElement>(null)
  const confirmDeleteCancelRef = useRef<HTMLButtonElement>(null)
  const applyFutureOnlyRef = useRef<HTMLButtonElement>(null)

  // Focus primary element when modals open
  useEffect(() => { if (showAdd) setTimeout(() => addNameInputRef.current?.focus(), 50) }, [showAdd])
  useEffect(() => { if (confirmDelete) setTimeout(() => confirmDeleteCancelRef.current?.focus(), 50) }, [confirmDelete])
  useEffect(() => { if (applyDialog) setTimeout(() => applyFutureOnlyRef.current?.focus(), 50) }, [applyDialog])

  // Escape-key to close modals (Task 4)
  useEscapeKey(showAdd, () => setShowAdd(false))
  useEscapeKey(!!confirmDelete, () => setConfirmDelete(null))
  useEscapeKey(!!applyDialog, () => setApplyDialog(null))

  const { data: ledgerData, isLoading } = useQuery({
    queryKey: ['ledger', range, institution, account, search, categories, types, showTransfers, showDuplicates],
    queryFn: () =>
      ledgerApi.list({
        range,
        institution,
        account,
        search,
        category: categories.length > 0 ? categories.join(',') : undefined,
        types: types.length > 0 ? types.join(',') : undefined,
        show_transfers: showTransfers || undefined,
        show_duplicates: showDuplicates || undefined,
      }),
  })

  // Manual recurring rules — keyed by merchant so we can offer "remove" on the
  // toggle (only manual rules have a deletable id).
  const { data: recurringRules = [] } = useQuery({
    queryKey: ['recurring-rules'],
    queryFn: () => workspaceApi.listRecurringRules(),
    staleTime: 60_000,
  })
  const recurringByKey = useMemo(() => {
    const m = new Map<string, RecurringRule>()
    for (const r of recurringRules) m.set((r.merchant_key || '').toLowerCase().trim(), r)
    return m
  }, [recurringRules])

  // Full recurring detection (auto-detected + manual) — drives the table badge so
  // it matches exactly what the side panel's Recurring tab shows.
  const { data: recurringDetected = [] } = useQuery({
    queryKey: ['recurring'],
    queryFn: () => workspaceApi.listRecurring(),
    staleTime: 60_000,
  })
  const autoRecurringKeys = useMemo(() => {
    const s = new Set<string>()
    for (const r of recurringDetected) {
      if (r.source === 'auto') s.add((r.name || '').toLowerCase().trim())
    }
    return s
  }, [recurringDetected])

  const { data: groupTxData } = useQuery({
    queryKey: ['group-tx-ids', activeGroup?.id],
    queryFn: () => workspaceApi.groupTransactions(activeGroup!.id),
    enabled: !!activeGroup,
  })

  const groupTxIds = new Set((groupTxData?.transaction_ids ?? []).map(String))

  const { data: userCategories = [] } = useQuery({
    queryKey: ['user-categories'],
    queryFn: () => categoriesApi.userCategories(),
    staleTime: 300_000,
  })

  const rows = ledgerData?.rows ?? []
  const summary = ledgerData?.summary

  // Distinct raw descriptors grouped under each displayed merchant name. Drives the
  // rename scope choice: when a name covers >1 descriptor, the drawer offers to
  // rename the whole group or split out just the current descriptor.
  const descriptorsByMerchant = useMemo(() => {
    const m = new Map<string, Set<string>>()
    for (const r of rows) {
      const key = r.merchant_normalized || ''
      if (!key) continue
      if (!m.has(key)) m.set(key, new Set())
      m.get(key)!.add(r.name)
    }
    return m
  }, [rows])

  // Derive selected row from current rows — stays fresh after query invalidations
  const selectedRow = selectedRowId !== null
    ? rows.find((r) => r.id === selectedRowId) ?? null
    : null

  // Close drawer if the selected row disappears (e.g. deleted)
  useEffect(() => {
    if (selectedRowId !== null && !isLoading && rows.length > 0) {
      const found = rows.find((r) => r.id === selectedRowId)
      if (!found) setSelectedRowId(null)
    }
  }, [rows, selectedRowId, isLoading])

  // Client-side sort on top of the backend's date-desc ordering.
  const sortedRows = useMemo(() => {
    const dir = sortDir === 'asc' ? 1 : -1
    const val = (r: LedgerRow): string | number => {
      switch (sortBy) {
        case 'amount': return r.amount
        case 'date': return r.date
        case 'name': return (r.name || '').toLowerCase()
        case 'merchant': return (r.merchant_normalized || '').toLowerCase()
        case 'category': return (r.category || '').toLowerCase()
        case 'institution': return (r.institution || '').toLowerCase()
      }
    }
    return [...rows].sort((a, b) => {
      const av = val(a), bv = val(b)
      if (av < bv) return -dir
      if (av > bv) return dir
      return 0
    })
  }, [rows, sortBy, sortDir])

  const toggleSort = (key: SortKey) => {
    if (sortBy === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortBy(key)
      // Numbers and dates read better newest/largest-first.
      setSortDir(key === 'amount' || key === 'date' ? 'desc' : 'asc')
    }
  }

  const sortHead = (label: string, key: SortKey, align: 'left' | 'right' = 'left') => {
    const active = sortBy === key
    const Icon = !active ? ChevronsUpDown : sortDir === 'asc' ? ChevronUp : ChevronDown
    return (
      <th
        onClick={() => toggleSort(key)}
        title="Click to sort"
        style={{ cursor: 'pointer', userSelect: 'none', textAlign: align, whiteSpace: 'nowrap' }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, flexDirection: align === 'right' ? 'row-reverse' : 'row' }}>
          {label}
          <Icon size={12} style={{ opacity: active ? 1 : 0.35, color: active ? 'var(--color-accent)' : 'currentColor' }} />
        </span>
      </th>
    )
  }

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


  const patchMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: { category?: string; amount?: number; notes?: string } }) =>
      transactionsApi.patch(id, data),
    onSuccess: (_, { id }) => {
      setSaved((s) => ({ ...s, [id]: true }))
      setTimeout(() => setSaved((s) => ({ ...s, [id]: false })), 1500)
      qc.invalidateQueries({ queryKey: ['ledger'] })
    },
  })

  const renameMerchantMutation = useMutation({
    mutationFn: ({ rawName, displayName }: { rawName: string; displayName: string }) =>
      merchantsApi.saveOverride(rawName, displayName),
    onSuccess: (_data, vars) => {
      setToast({ message: `Renamed merchant to "${vars.displayName}"`, type: 'success' })
      setTimeout(() => setToast(null), 2500)
      qc.invalidateQueries({ queryKey: ['ledger'] })
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
      qc.invalidateQueries({ queryKey: ['ledger'] })
      setApplyDialog(null)
    },
  })

  const dismissDupMutation = useMutation({
    mutationFn: ({ id, otherId }: { id: string; otherId: string }) =>
      transactionsApi.dismissDuplicate(id, otherId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ledger'] })
    },
  })

  const markRecurringMutation = useMutation({
    mutationFn: (transaction_id: string) => workspaceApi.markRecurringFromTransaction(transaction_id),
    onSuccess: (data) => {
      setToast({ message: `Marked "${data.merchant_key}" as recurring`, type: 'success' })
      setTimeout(() => setToast(null), 2500)
      qc.invalidateQueries({ queryKey: ['recurring'] })
      qc.invalidateQueries({ queryKey: ['recurring-rules'] })
      qc.invalidateQueries({ queryKey: ['tracker'] })
      // Jump the side panel to Recurring so the result is immediately visible.
      focusTab('recurring')
    },
    onError: () => {
      setToast({ message: 'Could not mark as recurring', type: 'error' })
      setTimeout(() => setToast(null), 2500)
    },
  })

  const unmarkRecurringMutation = useMutation({
    mutationFn: (ruleId: number) => workspaceApi.deleteRecurringRule(ruleId),
    onSuccess: () => {
      setToast({ message: 'Removed from recurring', type: 'success' })
      setTimeout(() => setToast(null), 2500)
      qc.invalidateQueries({ queryKey: ['recurring'] })
      qc.invalidateQueries({ queryKey: ['recurring-rules'] })
      qc.invalidateQueries({ queryKey: ['tracker'] })
    },
    onError: () => {
      setToast({ message: 'Could not remove recurring', type: 'error' })
      setTimeout(() => setToast(null), 2500)
    },
  })

  // Auto-detected items have no rule to delete — suppress the merchant instead so
  // detection stops re-tagging it.
  const suppressRecurringMutation = useMutation({
    mutationFn: (transaction_id: string) => workspaceApi.suppressRecurringFromTransaction(transaction_id),
    onSuccess: () => {
      setToast({ message: 'Removed from recurring', type: 'success' })
      setTimeout(() => setToast(null), 2500)
      qc.invalidateQueries({ queryKey: ['recurring'] })
      qc.invalidateQueries({ queryKey: ['recurring-rules'] })
      qc.invalidateQueries({ queryKey: ['tracker'] })
    },
    onError: () => {
      setToast({ message: 'Could not remove recurring', type: 'error' })
      setTimeout(() => setToast(null), 2500)
    },
  })

  const { data: categoryOverrides = {} } = useQuery({
    queryKey: ['merchant-category-overrides'],
    queryFn: () => merchantsApi.categoryOverrides(),
  })

  // Per-counterparty transfer rulings (merchant -> is_transfer). Drives the
  // drawer's Auto / Transfer / Spending control.
  const { data: transferOverrides = {} } = useQuery({
    queryKey: ['transfer-overrides'],
    queryFn: () => transactionsApi.transferOverrides(),
  })

  const setTransferMutation = useMutation({
    mutationFn: ({ id, isTransfer }: { id: number; isTransfer: boolean | null }) =>
      transactionsApi.setTransfer(id, isTransfer),
    onSuccess: () => {
      // A row moving in/out of "transfer" changes spending totals everywhere.
      qc.invalidateQueries({ queryKey: ['ledger'] })
      qc.invalidateQueries({ queryKey: ['transfer-overrides'] })
      qc.invalidateQueries({ queryKey: ['overview'] })
      qc.invalidateQueries({ queryKey: ['tracker'] })
    },
  })

  // Permanent merchant rename rules (raw merchant name -> display name).
  const { data: renameRules = {} } = useQuery({
    queryKey: ['merchant-overrides'],
    queryFn: () => merchantsApi.overrides(),
  })

  const deleteRenameRuleMutation = useMutation({
    mutationFn: (rawName: string) => merchantsApi.deleteOverride(rawName),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['merchant-overrides'] })
      qc.invalidateQueries({ queryKey: ['ledger'] })
      qc.invalidateQueries({ queryKey: ['merchants'] })
    },
  })

  const editRenameRuleMutation = useMutation({
    mutationFn: ({ rawName, displayName }: { rawName: string; displayName: string }) =>
      merchantsApi.saveOverride(rawName, displayName),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['merchant-overrides'] })
      qc.invalidateQueries({ queryKey: ['ledger'] })
      qc.invalidateQueries({ queryKey: ['merchants'] })
    },
  })

  const deleteCategoryRuleMutation = useMutation({
    mutationFn: (merchant: string) => merchantsApi.deleteCategoryOverride(merchant),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['merchant-category-overrides'] })
      qc.invalidateQueries({ queryKey: ['ledger'] })
    },
  })

  const ruleCount = Object.keys(renameRules).length + Object.keys(categoryOverrides).length

  const createMutation = useMutation({
    mutationFn: (data: { name: string; date: string; amount: number; category?: string; notes?: string }) =>
      transactionsApi.create(data),
    onSuccess: () => {
      setShowAdd(false)
      setAddForm({ name: '', date: today, amount: '', category: 'Other', notes: '' })
      qc.invalidateQueries({ queryKey: ['ledger'] })
      qc.invalidateQueries({ queryKey: ['summary'] })
      qc.invalidateQueries({ queryKey: ['monthly'] })
      qc.invalidateQueries({ queryKey: ['categories'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => transactionsApi.delete(id),
    onSuccess: () => {
      setConfirmDelete(null)
      // Close drawer if the deleted row was selected
      setSelectedRowId(null)
      qc.invalidateQueries({ queryKey: ['ledger'] })
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

  const handleBlur = (row: LedgerRow, field: 'category' | 'amount' | 'notes') => {
    const edit = editState[row.id]
    if (!edit) return
    const value = edit[field]
    if (value === undefined) return

    const patch: { category?: string; amount?: number; notes?: string } = {}
    if (field === 'category' && value !== row.category) patch.category = value
    if (field === 'amount') {
      const numVal = parseFloat(value)
      if (!isNaN(numVal) && numVal !== row.amount) patch.amount = numVal
    }
    if (field === 'notes' && value !== (row.notes ?? '')) patch.notes = value

    if (Object.keys(patch).length > 0) {
      patchMutation.mutate({ id: row.id, data: patch })
    }
  }

  const setEdit = (id: number, field: 'category' | 'amount' | 'notes', value: string) => {
    setEditState((s) => ({ ...s, [id]: { ...s[id], [field]: value } }))
  }

  const toggleType = (t: string) => {
    setTypes((prev) =>
      prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]
    )
  }

  // "Show" filter popover label + highlight logic (Task 1)
  const isDefaultShow = types.includes('debit') && types.includes('credit') && !showTransfers && !showDuplicates
    && types.length === 2
  const showLabel = (() => {
    const parts: string[] = []
    if (types.includes('debit')) parts.push('Spending')
    if (types.includes('credit')) parts.push('Income')
    if (showTransfers) parts.push('Transfers')
    if (showDuplicates) parts.push('Duplicates')
    if (parts.length === 0) return 'Show: Nothing'
    if (isDefaultShow) return 'Show'
    if (parts.length === 4) return 'Show: All'
    if (parts.length <= 2) return `Show: ${parts.join(' + ')}`
    return `Show: ${parts.length} types`
  })()

  // Table column count (for colSpan) — 8 base cols + optional group dot
  const colCount = activeGroup ? 9 : 8

  return (
    <div className="space-y-4 fade-in" style={{ paddingBottom: 56 }}>
      {toast && (
        <div
          style={{
            position: 'fixed', bottom: 80, left: '50%', transform: 'translateX(-50%)',
            background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)',
            padding: '10px 16px', borderRadius: 8, fontSize: 13,
            color: 'var(--color-text-primary)', zIndex: 70,
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
                  ref={addNameInputRef}
                  type="text"
                  required
                  autoFocus
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
                <button type="submit" disabled={createMutation.isPending} style={{ padding: '6px 14px', fontSize: 13, background: 'var(--color-accent)', border: 'none', borderRadius: 6, cursor: 'pointer', color: 'var(--color-accent-contrast)', opacity: createMutation.isPending ? 0.6 : 1 }}>
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
            style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 12, padding: 24, width: 320 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 style={{ fontSize: 15, fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 8 }}>Delete transaction?</h2>
            <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 20 }}>This cannot be undone.</p>
            <div className="flex gap-2 justify-end">
              <button ref={confirmDeleteCancelRef} onClick={() => setConfirmDelete(null)} style={{ padding: '6px 14px', fontSize: 13, background: 'none', border: '1px solid var(--color-border)', borderRadius: 6, cursor: 'pointer', color: 'var(--color-text-secondary)' }}>
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

      <div>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: 'var(--color-text-primary)' }}>Transactions</h1>
        <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 2 }}>
          Spending and income by default — use the Show filter to include transfers and duplicates
        </p>
      </div>

      <ActiveGroupBanner />

      {/* Filters bar */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative">
          <Search
            size={14}
            style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--color-text-muted)' }}
          />
          <input
            type="search"
            placeholder="Search transactions…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ paddingLeft: 30, width: 220 }}
          />
        </div>

        {/* Time range — contextual to this page (defaults to all time) */}
        <RangeSelect value={range} onChange={setRange} />

        {/* Category filter — multi-select */}
        <div style={{ position: 'relative' }}>
          <button
            onClick={() => setCatMenuOpen((o) => !o)}
            className="flex items-center gap-1.5"
            title="Filter by category"
            style={{ fontSize: 12, padding: '6px 10px', borderRadius: 6, background: 'var(--color-surface)', border: `1px solid ${categories.length ? 'var(--color-accent)' : 'var(--color-border)'}`, color: 'var(--color-text-primary)', cursor: 'pointer' }}
          >
            {categories.length === 0
              ? 'All categories'
              : categories.length === 1
                ? categories[0]
                : `${categories.length} categories`}
            <ChevronDown size={13} style={{ color: 'var(--color-text-muted)' }} />
          </button>
          {catMenuOpen && (
            <>
              <div onClick={() => setCatMenuOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 30 }} />
              <div
                style={{ position: 'absolute', top: 'calc(100% + 4px)', left: 0, zIndex: 31, minWidth: 190, maxHeight: 300, overflowY: 'auto', background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8, padding: 6, boxShadow: '0 8px 28px rgba(0,0,0,0.32)' }}
              >
                <button
                  onClick={() => setCategories([])}
                  className="flex items-center justify-between w-full"
                  style={{ fontSize: 12, padding: '5px 8px', borderRadius: 5, background: 'none', border: 'none', color: categories.length ? 'var(--color-text-secondary)' : 'var(--color-accent)', cursor: 'pointer', fontWeight: 600 }}
                >
                  All categories
                  {categories.length === 0 && <Check size={13} />}
                </button>
                <div style={{ height: 1, background: 'var(--color-border)', margin: '4px 0' }} />
                {userCategories.map((c) => {
                  const checked = categories.includes(c)
                  return (
                    <label
                      key={c}
                      className="flex items-center gap-2"
                      style={{ padding: '5px 8px', borderRadius: 5, cursor: 'pointer', fontSize: 12, color: 'var(--color-text-primary)' }}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => setCategories((prev) => (checked ? prev.filter((x) => x !== c) : [...prev, c]))}
                      />
                      <span style={{ width: 7, height: 7, borderRadius: '50%', background: getCategoryColor(c), flexShrink: 0 }} />
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c}</span>
                    </label>
                  )
                })}
              </div>
            </>
          )}
        </div>

        {/* Show filter — consolidates type toggle + transfers + duplicates (Task 1) */}
        <div style={{ position: 'relative' }}>
          <button
            onClick={() => setShowMenuOpen((o) => !o)}
            className="flex items-center gap-1.5"
            title="Filter which transaction types are visible"
            style={{
              fontSize: 12, padding: '6px 10px', borderRadius: 6,
              background: 'var(--color-surface)',
              border: `1px solid ${!isDefaultShow ? 'var(--color-accent)' : 'var(--color-border)'}`,
              color: 'var(--color-text-primary)', cursor: 'pointer',
            }}
          >
            <Eye size={13} style={{ color: 'var(--color-text-muted)' }} />
            {showLabel}
            <ChevronDown size={13} style={{ color: 'var(--color-text-muted)' }} />
          </button>
          {showMenuOpen && (
            <>
              <div onClick={() => setShowMenuOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 30 }} />
              <div
                style={{
                  position: 'absolute', top: 'calc(100% + 4px)', left: 0, zIndex: 31,
                  minWidth: 180, background: 'var(--color-surface)',
                  border: '1px solid var(--color-border)', borderRadius: 8,
                  padding: 8, boxShadow: '0 8px 28px rgba(0,0,0,0.32)',
                }}
              >
                {([
                  ['debit', 'Spending (debit)', types.includes('debit'), () => toggleType('debit')],
                  ['credit', 'Income (credit)', types.includes('credit'), () => toggleType('credit')],
                  ['transfers', 'Transfers', showTransfers, () => setShowTransfers((v) => !v)],
                  ['duplicates', 'Duplicates', showDuplicates, () => setShowDuplicates((v) => !v)],
                ] as [string, string, boolean, () => void][]).map(([key, label, checked, toggle]) => (
                  <label
                    key={key}
                    className="flex items-center gap-2"
                    style={{ padding: '6px 8px', borderRadius: 5, cursor: 'pointer', fontSize: 12, color: 'var(--color-text-primary)' }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={toggle}
                    />
                    {label}
                  </label>
                ))}
              </div>
            </>
          )}
        </div>

        <div className="flex items-center gap-2 ml-auto">
          {/* Rules — view / change / delete permanent merchant + category rules */}
          <div style={{ position: 'relative' }}>
            <button
              onClick={() => setRulesOpen((o) => !o)}
              className="flex items-center gap-1.5"
              title="Your saved merchant & category rules"
              style={{ fontSize: 12, color: 'var(--color-text-muted)', padding: '6px 10px', borderRadius: 6, border: `1px solid ${rulesOpen ? 'var(--color-accent)' : 'var(--color-border)'}`, background: 'var(--color-surface)', cursor: 'pointer' }}
            >
              <SlidersHorizontal size={13} />
              Rules{ruleCount > 0 ? ` (${ruleCount})` : ''}
            </button>
            {rulesOpen && (
              <>
                <div onClick={() => { setRulesOpen(false); setRuleDraft(null) }} style={{ position: 'fixed', inset: 0, zIndex: 30 }} />
                <div
                  style={{ position: 'absolute', top: 'calc(100% + 4px)', right: 0, zIndex: 31, width: 340, maxHeight: 420, overflowY: 'auto', background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 8, boxShadow: '0 8px 28px rgba(0,0,0,0.32)' }}
                >
                  <div style={{ padding: '10px 12px', borderBottom: '1px solid var(--color-border)' }}>
                    <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-primary)' }}>Your rules</p>
                    <p style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 2 }}>
                      Permanent renames and category rules. Delete to revert to the auto-detected value.
                    </p>
                  </div>

                  {/* Renamed merchants */}
                  <div style={{ padding: '8px 12px' }}>
                    <p style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--color-text-muted)', marginBottom: 6 }}>
                      Renamed merchants
                    </p>
                    {Object.keys(renameRules).length === 0 ? (
                      <p style={{ fontSize: 12, color: 'var(--color-text-muted)', padding: '2px 0 6px' }}>No rename rules.</p>
                    ) : (
                      Object.entries(renameRules).map(([rawName, display]) => (
                        <div key={rawName} className="flex items-center gap-2" style={{ padding: '4px 0' }}>
                          <span title={rawName} style={{ fontSize: 11, color: 'var(--color-text-muted)', flexShrink: 0, maxWidth: 90, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {rawName}
                          </span>
                          <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>→</span>
                          {ruleDraft?.key === rawName ? (
                            <input
                              autoFocus
                              value={ruleDraft.value}
                              onChange={(e) => setRuleDraft({ key: rawName, value: e.target.value })}
                              onBlur={() => {
                                const v = ruleDraft.value.trim()
                                if (v && v !== display) editRenameRuleMutation.mutate({ rawName, displayName: v })
                                setRuleDraft(null)
                              }}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                                if (e.key === 'Escape') setRuleDraft(null)
                              }}
                              style={{ fontSize: 12, flex: 1, minWidth: 0 }}
                            />
                          ) : (
                            <span
                              onClick={() => setRuleDraft({ key: rawName, value: display })}
                              title="Click to change"
                              style={{ fontSize: 12, color: 'var(--color-text-primary)', flex: 1, cursor: 'text', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                            >
                              {display}
                            </span>
                          )}
                          <button
                            onClick={() => deleteRenameRuleMutation.mutate(rawName)}
                            title="Delete rule (revert)"
                            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-muted)', padding: 2, display: 'flex', flexShrink: 0 }}
                          >
                            <Trash2 size={12} />
                          </button>
                        </div>
                      ))
                    )}
                  </div>

                  {/* Category rules */}
                  <div style={{ padding: '8px 12px', borderTop: '1px solid var(--color-border)' }}>
                    <p style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--color-text-muted)', marginBottom: 6 }}>
                      Category rules
                    </p>
                    {Object.keys(categoryOverrides).length === 0 ? (
                      <p style={{ fontSize: 12, color: 'var(--color-text-muted)', padding: '2px 0 6px' }}>No category rules.</p>
                    ) : (
                      Object.entries(categoryOverrides).map(([merchant, cat]) => (
                        <div key={merchant} className="flex items-center gap-2" style={{ padding: '4px 0' }}>
                          <span style={{ fontSize: 12, color: 'var(--color-text-primary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {merchant}
                          </span>
                          <span className="flex items-center gap-1" style={{ flexShrink: 0 }}>
                            <span style={{ width: 7, height: 7, borderRadius: '50%', background: getCategoryColor(cat) }} />
                            <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{cat}</span>
                          </span>
                          <button
                            onClick={() => deleteCategoryRuleMutation.mutate(merchant)}
                            title="Delete rule"
                            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-muted)', padding: 2, display: 'flex', flexShrink: 0 }}
                          >
                            <Trash2 size={12} />
                          </button>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
          <button
            onClick={() => ledgerApi.exportCsv({ range, institution, account, search, category: categories.length > 0 ? categories.join(',') : undefined, types: types.length > 0 ? types.join(',') : undefined, show_transfers: showTransfers || undefined, show_duplicates: showDuplicates || undefined })}
            className="flex items-center gap-1.5"
            title="Export current view to CSV"
            style={{ fontSize: 12, color: 'var(--color-text-muted)', padding: '6px 10px', borderRadius: 6, border: '1px solid var(--color-border)', background: 'var(--color-surface)', cursor: 'pointer' }}
          >
            <Download size={13} />
            Export
          </button>
          <button
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-1.5"
            style={{ padding: '6px 12px', fontSize: 13, background: 'var(--color-accent)', border: 'none', borderRadius: 6, cursor: 'pointer', color: 'var(--color-accent-contrast)', fontWeight: 500 }}
          >
            <Plus size={14} /> Add
          </button>
        </div>
      </div>

      <div
        className="rounded-xl"
        style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
      >
        <div className="overflow-x-auto" style={{ borderRadius: '12px 12px 0 0' }}>
          <table>
            <thead>
              <tr>
                {sortHead('Date', 'date')}
                {sortHead('Name', 'name')}
                {sortHead('Merchant', 'merchant')}
                {sortHead('Category', 'category')}
                {sortHead('Amount', 'amount', 'right')}
                {sortHead('Institution', 'institution')}
                <th>Status</th>
                {/* saved/override indicator */}
                <th style={{ width: 30 }}></th>
                {activeGroup && <th style={{ width: 28 }}></th>}
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <SkeletonRow cols={colCount} rows={12} />
              ) : sortedRows.length === 0 ? (
                <tr>
                  <td colSpan={colCount} style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: '32px 0' }}>
                    {types.length === 0 && !showTransfers && !showDuplicates
                      ? 'All transaction types are hidden — adjust the Show filter.'
                      : (search || categories.length > 0)
                        ? 'No transactions match your filters.'
                        : 'No transactions found'}
                  </td>
                </tr>
              ) : (
                sortedRows.map((row) => {
                  const currentCategory = editState[row.id]?.category !== undefined
                    ? editState[row.id].category!
                    : row.category

                  const recurringKey = (row.merchant_normalized || row.name || '').toLowerCase().trim()
                  const recurringRule = recurringByKey.get(recurringKey)
                  const isRecurring = !!recurringRule || autoRecurringKeys.has(recurringKey)

                  const isSelected = row.id === selectedRowId

                  const rowStyle: React.CSSProperties = {
                    cursor: 'pointer',
                  }
                  if (row.is_duplicate) rowStyle.opacity = 0.5
                  if (row.is_transfer) rowStyle.color = 'var(--color-text-muted)'

                  const handleRowClick = (e: React.MouseEvent<HTMLTableRowElement>) => {
                    // Ignore clicks on interactive elements
                    if ((e.target as HTMLElement).closest('button, input, select, a, label')) return
                    setSelectedRowId(row.id)
                  }

                  return (
                    <tr
                      key={row.id}
                      style={rowStyle}
                      onClick={handleRowClick}
                    >
                      <td style={{
                        fontFamily: 'monospace', fontSize: 12, color: 'var(--color-text-muted)', whiteSpace: 'nowrap',
                        background: isSelected ? 'var(--color-accent-soft)' : undefined,
                      }}>
                        {formatDate(row.date)}
                      </td>
                      <td style={{ background: isSelected ? 'var(--color-accent-soft)' : undefined }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4, maxWidth: 170 }}>
                          <span style={{ fontSize: 12, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {row.name}
                          </span>
                          {row.is_potential_duplicate && (
                            <button
                              onClick={(e) => { e.stopPropagation(); setSelectedRowId(row.id) }}
                              title="Possible duplicate — click to review"
                              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, display: 'flex', flexShrink: 0 }}
                            >
                              <AlertTriangle size={12} style={{ color: '#e8c17a' }} />
                            </button>
                          )}
                        </div>
                      </td>
                      <td style={{ background: isSelected ? 'var(--color-accent-soft)' : undefined }} className="editable-cell">
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4, maxWidth: 145 }}>
                          <span
                            title={categoryOverrides[row.merchant_normalized] ? `Rule: always ${categoryOverrides[row.merchant_normalized]}` : undefined}
                            style={{ fontSize: 12, color: 'var(--color-text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}
                          >
                            {row.merchant_normalized || '—'}
                          </span>
                          {categoryOverrides[row.merchant_normalized] && (
                            <span
                              title={`Auto-categorized as "${categoryOverrides[row.merchant_normalized]}" — rule applies to all transactions from this merchant`}
                              style={{ display: 'flex', flexShrink: 0 }}
                            >
                              <Tag size={11} style={{ color: getCategoryColor(categoryOverrides[row.merchant_normalized]) }} />
                            </span>
                          )}
                        </div>
                      </td>
                      <td style={{ background: isSelected ? 'var(--color-accent-soft)' : undefined }} className="editable-cell">
                        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                          <div style={{ width: 7, height: 7, borderRadius: '50%', background: getCategoryColor(currentCategory), flexShrink: 0 }} />
                          <select
                            value={currentCategory}
                            onChange={(e) => {
                              const newCat = e.target.value
                              setEdit(row.id, 'category', newCat)
                              patchMutation.mutate({ id: row.id, data: { category: newCat } })
                              if (row.merchant_normalized) {
                                setMerchantSuggestion({ merchant: row.merchant_normalized, category: newCat })
                              }
                            }}
                            style={{ fontSize: 12, minWidth: 120, border: 'none', background: 'var(--color-surface)', color: 'var(--color-text-primary)', padding: '2px 24px 2px 2px' }}
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
                      <td style={{ textAlign: 'right', background: isSelected ? 'var(--color-accent-soft)' : undefined }} className="editable-cell">
                        {editState[row.id]?.amount !== undefined ? (
                          <input
                            type="text"
                            inputMode="decimal"
                            autoFocus
                            value={editState[row.id]?.amount ?? ''}
                            onChange={(e) => setEdit(row.id, 'amount', e.target.value)}
                            onBlur={() => {
                              handleBlur(row, 'amount')
                              setEditState((s) => {
                                const next = { ...s }
                                if (next[row.id]) {
                                  const { amount: _a, ...rest } = next[row.id]
                                  if (Object.keys(rest).length === 0) delete next[row.id]
                                  else next[row.id] = rest
                                }
                                return next
                              })
                            }}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                              if (e.key === 'Escape') {
                                setEditState((s) => {
                                  const next = { ...s }
                                  if (next[row.id]) {
                                    const { amount: _a, ...rest } = next[row.id]
                                    if (Object.keys(rest).length === 0) delete next[row.id]
                                    else next[row.id] = rest
                                  }
                                  return next
                                })
                              }
                            }}
                            style={{
                              fontFamily: 'monospace',
                              fontSize: 12,
                              textAlign: 'right',
                              width: 90,
                              color: row.amount < 0 ? 'var(--color-positive)' : 'var(--color-text-primary)',
                            }}
                          />
                        ) : (
                          <span
                            onClick={() => setEdit(row.id, 'amount', row.amount.toString())}
                            title="Click to edit amount"
                            style={{
                              fontFamily: 'monospace',
                              fontSize: 12,
                              cursor: 'text',
                              color: row.amount < 0 ? 'var(--color-positive)' : 'var(--color-text-primary)',
                              display: 'inline-block',
                              minWidth: 60,
                              textAlign: 'right',
                            }}
                          >
                            {formatCurrency(row.amount)}
                          </span>
                        )}
                      </td>
                      <td style={{ fontSize: 12, color: 'var(--color-text-muted)', whiteSpace: 'nowrap', background: isSelected ? 'var(--color-accent-soft)' : undefined }}>
                        {row.institution}
                      </td>
                      <td style={{ background: isSelected ? 'var(--color-accent-soft)' : undefined }}>
                        <div className="flex items-center gap-1">
                          {row.pending && (
                            <span
                              className="px-1.5 py-0.5 rounded text-xs"
                              style={{ background: 'rgba(232, 193, 122, 0.15)', color: '#e8c17a', fontSize: 10 }}
                            >
                              pending
                            </span>
                          )}
                          {row.is_transfer && (
                            <span
                              className="px-1.5 py-0.5 rounded text-xs"
                              style={{ background: 'rgba(122, 174, 212, 0.15)', color: '#7aaed4', fontSize: 10 }}
                            >
                              transfer
                            </span>
                          )}
                          {row.is_duplicate && (
                            <span
                              className="px-1.5 py-0.5 rounded text-xs"
                              style={{ background: 'rgba(232, 96, 96, 0.15)', color: 'var(--color-negative)', fontSize: 10 }}
                            >
                              dup
                            </span>
                          )}
                          {row.is_excluded && (
                            <span
                              className="flex items-center gap-1 px-1.5 py-0.5 rounded"
                              title="Excluded from Overview reports"
                              style={{ background: 'var(--color-surface-raise)', color: 'var(--color-text-muted)', fontSize: 10, border: '1px solid var(--color-border)' }}
                            >
                              <EyeOff size={9} />
                              excluded
                            </span>
                          )}
                          {isRecurring && (
                            <span
                              className="flex items-center gap-1 px-1.5 py-0.5 rounded"
                              title="Marked as recurring"
                              style={{ background: 'var(--color-accent-soft)', color: 'var(--color-accent-text)', fontSize: 10 }}
                            >
                              <Repeat size={9} />
                              recurring
                            </span>
                          )}
                          {row.needs_review ? (
                            <span
                              className="flex items-center gap-1 px-1.5 py-0.5 rounded"
                              title="Reimbursement — we couldn't tell what it was for. Set a category so it reduces the right spending."
                              style={{ background: 'rgba(232, 193, 122, 0.18)', color: '#e8c17a', fontSize: 10 }}
                            >
                              <AlertTriangle size={9} />
                              review
                            </span>
                          ) : row.is_reimbursement && (
                            <span
                              className="px-1.5 py-0.5 rounded"
                              title="Money back — reduces this category's spending"
                              style={{ background: 'rgba(90, 191, 138, 0.15)', color: 'var(--color-positive)', fontSize: 10 }}
                            >
                              reimbursed
                            </span>
                          )}
                        </div>
                      </td>
                      {/* Saved / override indicator */}
                      <td style={{ background: isSelected ? 'var(--color-accent-soft)' : undefined }}>
                        {saved[row.id] ? (
                          <Check size={13} style={{ color: 'var(--color-positive)' }} />
                        ) : row.has_user_override ? (
                          <span title="You edited this transaction" style={{ display: 'flex', justifyContent: 'center' }}>
                            <Pencil size={11} style={{ color: 'var(--color-accent)' }} />
                          </span>
                        ) : null}
                      </td>
                      {activeGroup && (() => {
                        const txId = String(row.id)
                        const inGroup = groupTxIds.has(txId)
                        return (
                          <td style={{ background: isSelected ? 'var(--color-accent-soft)' : undefined }}>
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
                  )
                })
              )}
            </tbody>
          </table>
        </div>

      </div>

      {/* Merchant category suggestion toast — bottom-center, stacked above main toast (Task 5b) */}
      {merchantSuggestion && (
        <div
          style={{
            position: 'fixed',
            bottom: toast ? 140 : 80,
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 40,
            background: 'var(--color-surface)',
            border: '1px solid var(--color-border)',
            borderRadius: 10, padding: '12px 16px',
            boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
            maxWidth: 340,
            width: 'max-content',
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
              style={{ fontSize: 12, padding: '4px 10px', borderRadius: 5, background: 'var(--color-accent)', border: 'none', color: 'var(--color-accent-contrast)', fontWeight: 600, cursor: 'pointer', opacity: saveMerchantCategoryMutation.isPending ? 0.6 : 1 }}
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
                ref={applyFutureOnlyRef}
                onClick={() => setApplyDialog(null)}
                style={{ fontSize: 13, padding: '6px 14px', borderRadius: 6, background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)', cursor: 'pointer' }}
              >
                Future only
              </button>
              <button
                disabled={applyHistoricalMutation.isPending}
                onClick={() => applyHistoricalMutation.mutate(applyDialog)}
                style={{ fontSize: 13, padding: '6px 14px', borderRadius: 6, background: 'var(--color-accent)', color: 'var(--color-accent-contrast)', fontWeight: 600, cursor: 'pointer', border: 'none', opacity: applyHistoricalMutation.isPending ? 0.6 : 1 }}
              >
                {applyHistoricalMutation.isPending ? 'Applying…' : 'Apply to all past'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Transaction detail drawer */}
      {selectedRow && (() => {
        const recurringKey = (selectedRow.merchant_normalized || selectedRow.name || '').toLowerCase().trim()
        const recurringRule = recurringByKey.get(recurringKey)
        const isRecurring = !!recurringRule || autoRecurringKeys.has(recurringKey)
        return (
          <TransactionDrawer
            row={selectedRow}
            onClose={() => setSelectedRowId(null)}
            userCategories={userCategories}
            categoryOverrides={categoryOverrides}
            recurringRule={recurringRule}
            isRecurring={isRecurring}
            isSaved={!!saved[selectedRow.id]}
            onPatch={(id, data) => patchMutation.mutate({ id, data })}
            onRenameMerchant={(displayName, scope) => {
              // 'group' keys the override on the merchant name (renames every
              // variant); 'descriptor' keys it on this row's raw descriptor so only
              // matching rows change — that's how a merged merchant gets split.
              const rawName = scope === 'descriptor'
                ? selectedRow.name
                : (selectedRow.merchant_normalized || selectedRow.name)
              renameMerchantMutation.mutate({ rawName, displayName })
            }}
            merchantDescriptorCount={descriptorsByMerchant.get(selectedRow.merchant_normalized || '')?.size ?? 1}
            onMarkRecurring={(txId) => markRecurringMutation.mutate(txId)}
            onUnmarkRecurring={(ruleId) => unmarkRecurringMutation.mutate(ruleId)}
            onSuppressRecurring={(txId) => suppressRecurringMutation.mutate(txId)}
            onDismissDuplicate={(id, otherId) => dismissDupMutation.mutate({ id, otherId })}
            onSetTransfer={(id, isTransfer) => setTransferMutation.mutate({ id, isTransfer })}
            transferOverrides={transferOverrides}
            isSetTransferPending={setTransferMutation.isPending}
            onDelete={(id) => setConfirmDelete(id)}
            isPatchPending={patchMutation.isPending}
            isMarkRecurringPending={markRecurringMutation.isPending}
            isUnmarkRecurringPending={unmarkRecurringMutation.isPending}
            isSuppressRecurringPending={suppressRecurringMutation.isPending}
            isDismissDupPending={dismissDupMutation.isPending}
            isDeletePending={deleteMutation.isPending}
          />
        )
      })()}

      {summary && (
        <div
          className="flex items-center gap-8 px-6 flex-wrap"
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
            {summary.transactions} transactions
          </span>
          <span style={{ fontSize: 13, color: 'var(--color-text-secondary)' }} title="Spending, net of reimbursements">
            Spent <span style={{ fontFamily: 'monospace', fontSize: 15, fontWeight: 600, color: 'var(--color-negative)', marginLeft: 6 }}>{formatCurrency(summary.spent)}</span>
          </span>
          {summary.reimbursements > 0 && (
            <span style={{ fontSize: 13, color: 'var(--color-text-secondary)' }} title="Money paid back to you (Venmo, refunds) — already subtracted from Spent">
              Reimbursed <span style={{ fontFamily: 'monospace', fontSize: 15, fontWeight: 600, color: 'var(--color-accent)', marginLeft: 6 }}>{formatCurrency(summary.reimbursements)}</span>
            </span>
          )}
          <span style={{ fontSize: 13, color: 'var(--color-text-secondary)' }} title="True income only — excludes reimbursements">
            Income <span style={{ fontFamily: 'monospace', fontSize: 15, fontWeight: 600, color: 'var(--color-positive)', marginLeft: 6 }}>{formatCurrency(summary.income)}</span>
          </span>
          <span style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
            Net <span style={{ fontFamily: 'monospace', fontSize: 15, fontWeight: 600, color: summary.net <= 0 ? 'var(--color-positive)' : 'var(--color-negative)', marginLeft: 6 }}>{formatCurrency(summary.net)}</span>
          </span>
          {summary.transfer_count > 0 && (
            <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
              {summary.transfer_count} transfers hidden
            </span>
          )}
        </div>
      )}
    </div>
  )
}

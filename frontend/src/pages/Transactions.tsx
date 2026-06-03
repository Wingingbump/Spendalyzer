import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Search, Check, Plus, Trash2, X, Tag, Download, AlertTriangle } from 'lucide-react'
import { ledgerApi, transactionsApi, workspaceApi, merchantsApi, categoriesApi } from '../lib/api'
import { useFilters } from '../context/FilterContext'
import { useWorkspace } from '../context/WorkspaceContext'
import { usePanel } from '../context/PanelContext'
import { PANEL_WIDTH } from '../components/RightPanel'
import { useIsMobile } from '../hooks/useIsMobile'
import { formatCurrency, formatDate, getCategoryColor } from '../lib/utils'
import SkeletonRow from '../components/SkeletonRow'
import { ActiveGroupBanner } from '../components/RightPanel'

interface EditState {
  [id: number]: {
    category?: string
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

export default function Ledger() {
  const { range, institution, account } = useFilters()
  const { activeGroup } = useWorkspace()
  const { panelOpen } = usePanel()
  const isMobile = useIsMobile()
  const rhsWidth = panelOpen ? PANEL_WIDTH : 0
  const [search, setSearch] = useState('')
  const [types, setTypes] = useState<string[]>([])
  const [showTransfers, setShowTransfers] = useState(false)
  const [showDuplicates, setShowDuplicates] = useState(false)
  const [editState, setEditState] = useState<EditState>({})
  const [saved, setSaved] = useState<SavedState>({})
  const [editingMerchant, setEditingMerchant] = useState<number | null>(null)
  const [merchantDraft, setMerchantDraft] = useState('')
  const [showAdd, setShowAdd] = useState(false)
  const [addForm, setAddForm] = useState<AddForm>({ name: '', date: today, amount: '', category: 'Other', notes: '' })
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [merchantSuggestion, setMerchantSuggestion] = useState<{ merchant: string; category: string } | null>(null)
  const [applyDialog, setApplyDialog] = useState<{ merchant: string; category: string } | null>(null)
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null)
  const [expandedDup, setExpandedDup] = useState<number | null>(null)
  const qc = useQueryClient()

  const { data: ledgerData, isLoading } = useQuery({
    queryKey: ['ledger', range, institution, account, search, types, showTransfers, showDuplicates],
    queryFn: () =>
      ledgerApi.list({
        range,
        institution,
        account,
        search,
        types: types.length > 0 ? types.join(',') : undefined,
        show_transfers: showTransfers || undefined,
        show_duplicates: showDuplicates || undefined,
      }),
  })

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
    mutationFn: ({ id, data }: { id: number; data: { category?: string; notes?: string } }) =>
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
      setExpandedDup(null)
      qc.invalidateQueries({ queryKey: ['ledger'] })
    },
  })

  const { data: categoryOverrides = {} } = useQuery({
    queryKey: ['merchant-category-overrides'],
    queryFn: () => merchantsApi.categoryOverrides(),
  })

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

  const commitMerchant = (rawName: string) => {
    const trimmed = merchantDraft.trim()
    if (trimmed && trimmed !== rawName) renameMerchantMutation.mutate({ rawName, displayName: trimmed })
    setEditingMerchant(null)
  }

  const handleBlur = (rowId: number, field: 'category' | 'notes', originalValue: string) => {
    const edit = editState[rowId]
    if (!edit) return
    const value = edit[field]
    if (value === undefined || value === originalValue) return
    patchMutation.mutate({ id: rowId, data: { [field]: value } })
  }

  const setEdit = (id: number, field: 'category' | 'notes', value: string) => {
    setEditState((s) => ({ ...s, [id]: { ...s[id], [field]: value } }))
  }

  const toggleType = (t: string) => {
    setTypes((prev) =>
      prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]
    )
  }

  return (
    <div className="space-y-4 fade-in" style={{ paddingBottom: 56 }}>
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
            style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)', borderRadius: 12, padding: 24, width: 320 }}
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

      <div className="flex items-center justify-between">
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 700, color: 'var(--color-text-primary)' }}>Ledger</h1>
          <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 2 }}>
            Full transaction history including transfers and duplicates
          </p>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="flex items-center gap-1.5"
          style={{ padding: '6px 12px', fontSize: 13, background: 'var(--color-accent)', border: 'none', borderRadius: 6, cursor: 'pointer', color: '#fff', fontWeight: 500 }}
        >
          <Plus size={14} /> Add
        </button>
      </div>

      <ActiveGroupBanner />

      {/* Filters bar */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="relative">
          <Search
            size={14}
            style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--color-text-muted)' }}
          />
          <input
            type="search"
            placeholder="Search ledger…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ paddingLeft: 30, width: 220 }}
          />
        </div>

        {/* Type filter */}
        <div
          className="flex items-center gap-1 rounded-lg p-1"
          style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
        >
          {['debit', 'credit'].map((t) => (
            <button
              key={t}
              onClick={() => toggleType(t)}
              className="px-3 py-1 rounded-md text-xs font-medium transition-colors"
              style={{
                background: types.includes(t) ? 'var(--color-surface-raise)' : 'transparent',
                color: types.includes(t) ? 'var(--color-text-primary)' : 'var(--color-text-muted)',
                border: types.includes(t) ? '1px solid var(--color-border)' : '1px solid transparent',
              }}
            >
              {t}
            </button>
          ))}
        </div>

        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={showTransfers}
            onChange={(e) => setShowTransfers(e.target.checked)}
          />
          <span style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>Show transfers</span>
        </label>

        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={showDuplicates}
            onChange={(e) => setShowDuplicates(e.target.checked)}
          />
          <span style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>Show duplicates</span>
        </label>

        <button
          onClick={() => ledgerApi.exportCsv({ range, institution, account, search, types: types.length > 0 ? types.join(',') : undefined, show_transfers: showTransfers || undefined, show_duplicates: showDuplicates || undefined })}
          className="flex items-center gap-1.5 ml-auto"
          style={{ fontSize: 12, color: 'var(--color-text-muted)', padding: '4px 10px', borderRadius: 6, border: '1px solid var(--color-border)', background: 'var(--color-surface)' }}
        >
          <Download size={13} />
          Export CSV
        </button>
      </div>

      <div
        className="rounded-xl"
        style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
      >
        <div className="overflow-x-auto" style={{ borderRadius: '12px 12px 0 0' }}>
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Name</th>
                <th>Merchant</th>
                <th>Category</th>
                <th style={{ textAlign: 'right' }}>Amount</th>
                <th>Institution</th>
                <th>Status</th>
                <th>Notes</th>
                <th style={{ width: 30 }}></th>
                <th style={{ width: 28 }}></th>
                {activeGroup && <th style={{ width: 28 }}></th>}
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <SkeletonRow cols={activeGroup ? 10 : 9} rows={12} />
              ) : rows.length === 0 ? (
                <tr>
                  <td colSpan={9} style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: '32px 0' }}>
                    No transactions found
                  </td>
                </tr>
              ) : (
                rows.map((row) => {
                  const currentCategory = editState[row.id]?.category !== undefined
                    ? editState[row.id].category!
                    : row.category
                  const currentNotes = editState[row.id]?.notes !== undefined
                    ? editState[row.id].notes!
                    : (row.notes ?? '')

                  const rowStyle: React.CSSProperties = {}
                  if (row.is_duplicate) rowStyle.opacity = 0.5
                  if (row.is_transfer) rowStyle.color = 'var(--color-text-muted)'

                  return (
                    <React.Fragment key={row.id}>
                    <tr style={rowStyle}>
                      <td style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
                        {formatDate(row.date)}
                      </td>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4, maxWidth: 170 }}>
                          <span style={{ fontSize: 12, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {row.name}
                          </span>
                          {row.is_potential_duplicate && (
                            <button
                              onClick={() => setExpandedDup(expandedDup === row.id ? null : row.id)}
                              title="Possible duplicate — click to review"
                              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, display: 'flex', flexShrink: 0 }}
                            >
                              <AlertTriangle size={12} style={{ color: '#e8c17a' }} />
                            </button>
                          )}
                        </div>
                      </td>
                      <td className="editable-cell">
                        {editingMerchant === row.id ? (
                          <input
                            type="text"
                            value={merchantDraft}
                            autoFocus
                            onChange={(e) => setMerchantDraft(e.target.value)}
                            onBlur={() => commitMerchant(row.merchant_normalized || '')}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') commitMerchant(row.merchant_normalized || '')
                              if (e.key === 'Escape') setEditingMerchant(null)
                            }}
                            style={{ fontSize: 12, width: 120 }}
                          />
                        ) : (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 4, maxWidth: 145 }}>
                            <span
                              onDoubleClick={() => { setMerchantDraft(row.merchant_normalized || ''); setEditingMerchant(row.id) }}
                              title={categoryOverrides[row.merchant_normalized] ? `Rule: always ${categoryOverrides[row.merchant_normalized]} · double-click to rename` : 'Double-click to rename'}
                              style={{ fontSize: 12, color: 'var(--color-text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', cursor: 'text', flex: 1 }}
                            >
                              {row.merchant_normalized || '—'}
                            </span>
                            {categoryOverrides[row.merchant_normalized] && (
                              <div
                                title={`Category rule: ${categoryOverrides[row.merchant_normalized]}`}
                                style={{ width: 6, height: 6, borderRadius: '50%', background: getCategoryColor(categoryOverrides[row.merchant_normalized]), flexShrink: 0 }}
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
                      <td style={{ textAlign: 'right', fontFamily: 'monospace', fontSize: 12 }}>
                        <span style={{ color: row.amount < 0 ? 'var(--color-positive)' : 'var(--color-text-primary)' }}>
                          {formatCurrency(row.amount)}
                        </span>
                      </td>
                      <td style={{ fontSize: 12, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
                        {row.institution}
                      </td>
                      <td>
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
                        </div>
                      </td>
                      <td className="editable-cell">
                        <input
                          type="text"
                          value={currentNotes}
                          placeholder="Add note…"
                          onChange={(e) => setEdit(row.id, 'notes', e.target.value)}
                          onBlur={() => handleBlur(row.id, 'notes', row.notes ?? '')}
                          style={{ fontSize: 12, minWidth: 110 }}
                        />
                      </td>
                      <td>
                        {saved[row.id] && (
                          <Check size={13} style={{ color: 'var(--color-positive)' }} />
                        )}
                      </td>
                      <td>
                        <button
                          onClick={() => setConfirmDelete(String(row.id))}
                          title="Delete transaction"
                          className="delete-row-btn"
                          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-muted)', padding: 2, opacity: 0, transition: 'opacity 0.15s', display: 'flex', alignItems: 'center' }}
                        >
                          <Trash2 size={12} />
                        </button>
                      </td>
                      {activeGroup && (() => {
                        const txId = String(row.id)
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
                    {expandedDup === row.id && row.is_potential_duplicate && (() => {
                      const dupOf = row.potential_dup_of
                        ? (typeof row.potential_dup_of === 'string' ? JSON.parse(row.potential_dup_of) : row.potential_dup_of)
                        : null
                      return (
                        <tr>
                          <td colSpan={activeGroup ? 11 : 10} style={{ padding: 0 }}>
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
                                  onClick={() => deleteMutation.mutate(String(row.id))}
                                  disabled={deleteMutation.isPending}
                                  style={{ fontSize: 11, padding: '3px 10px', borderRadius: 5, background: 'var(--color-negative)', border: 'none', color: '#fff', cursor: 'pointer', fontWeight: 600 }}
                                >
                                  Delete this one
                                </button>
                                <button
                                  onClick={() => dupOf && dismissDupMutation.mutate({ id: String(row.id), otherId: dupOf.id })}
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
          <span style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
            Spent <span style={{ fontFamily: 'monospace', fontSize: 15, fontWeight: 600, color: 'var(--color-negative)', marginLeft: 6 }}>{formatCurrency(summary.spent)}</span>
          </span>
          <span style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
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

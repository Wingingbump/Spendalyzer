import React, { useState, useEffect, useRef } from 'react'
import { X, Pencil, Tag, Repeat, AlertTriangle, Trash2, Check } from 'lucide-react'
import type { LedgerRow, RecurringRule } from '../lib/api'
import { formatDate, formatCurrency, getCategoryColor } from '../lib/utils'

interface TransactionDrawerProps {
  row: LedgerRow
  onClose: () => void
  userCategories: string[]
  categoryOverrides: Record<string, string>
  recurringRule: RecurringRule | undefined
  isRecurring: boolean
  isSaved: boolean
  // Mutation handlers — keep mutations in Transactions.tsx
  onPatch: (id: number, data: { category?: string; amount?: number; notes?: string }) => void
  onRenameMerchant: (rawName: string, displayName: string) => void
  onMarkRecurring: (transactionId: string) => void
  onUnmarkRecurring: (ruleId: number) => void
  onDismissDuplicate: (id: string, otherId: string) => void
  onDelete: (id: string) => void
  isPatchPending: boolean
  isMarkRecurringPending: boolean
  isUnmarkRecurringPending: boolean
  isDismissDupPending: boolean
  isDeletePending: boolean
}

export default function TransactionDrawer({
  row,
  onClose,
  userCategories,
  categoryOverrides,
  recurringRule,
  isRecurring,
  isSaved,
  onPatch,
  onRenameMerchant,
  onMarkRecurring,
  onUnmarkRecurring,
  onDismissDuplicate,
  onDelete,
  isPatchPending,
  isMarkRecurringPending,
  isUnmarkRecurringPending,
  isDismissDupPending,
  isDeletePending,
}: TransactionDrawerProps) {
  // Amount editing
  const [editingAmount, setEditingAmount] = useState(false)
  const [amountDraft, setAmountDraft] = useState('')

  // Notes editing
  const [notesDraft, setNotesDraft] = useState(row.notes ?? '')

  // Merchant rename
  const [editingMerchant, setEditingMerchant] = useState(false)
  const [merchantDraft, setMerchantDraft] = useState(row.merchant_normalized || '')

  // Escape key handler
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (editingAmount) { setEditingAmount(false); return }
        if (editingMerchant) { setEditingMerchant(false); return }
        onClose()
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose, editingAmount, editingMerchant])

  // Sync notes / merchant draft when row updates from parent (after patch invalidates)
  const prevId = useRef(row.id)
  useEffect(() => {
    if (row.id !== prevId.current) {
      prevId.current = row.id
      setEditingAmount(false)
      setEditingMerchant(false)
    }
    setNotesDraft(row.notes ?? '')
    setMerchantDraft(row.merchant_normalized || '')
  }, [row.id, row.notes, row.merchant_normalized])

  const commitAmount = () => {
    const num = parseFloat(amountDraft)
    if (!isNaN(num) && num !== row.amount) {
      onPatch(row.id, { amount: num })
    }
    setEditingAmount(false)
  }

  const commitNotes = () => {
    if (notesDraft !== (row.notes ?? '')) {
      onPatch(row.id, { notes: notesDraft })
    }
  }

  const commitMerchant = () => {
    const trimmed = merchantDraft.trim()
    if (trimmed && trimmed !== row.merchant_normalized) {
      onRenameMerchant(row.merchant_normalized || '', trimmed)
    }
    setEditingMerchant(false)
  }

  const dupOf = row.potential_dup_of
    ? (typeof row.potential_dup_of === 'string'
      ? JSON.parse(row.potential_dup_of)
      : row.potential_dup_of)
    : null

  const categoryOverrideForMerchant = row.merchant_normalized
    ? categoryOverrides[row.merchant_normalized]
    : undefined

  const labelStyle: React.CSSProperties = {
    fontSize: 11,
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    color: 'var(--color-text-muted)',
    marginBottom: 4,
  }

  const fieldRowStyle: React.CSSProperties = {
    marginBottom: 16,
  }

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(0,0,0,0.25)',
          zIndex: 59,
        }}
      />

      {/* Drawer panel */}
      <div
        style={{
          position: 'fixed',
          right: 0,
          top: 0,
          height: '100%',
          width: 'min(380px, 100vw)',
          zIndex: 60,
          background: 'var(--color-surface)',
          borderLeft: '1px solid var(--color-border)',
          boxShadow: '-8px 0 32px rgba(0,0,0,0.35)',
          display: 'flex',
          flexDirection: 'column',
          overflowY: 'auto',
          animation: 'drawerSlideIn 0.18s ease-out',
        }}
      >
        <style>{`
          @keyframes drawerSlideIn {
            from { transform: translateX(100%); }
            to   { transform: translateX(0); }
          }
        `}</style>

        {/* Header */}
        <div
          style={{
            padding: '16px 16px 12px',
            borderBottom: '1px solid var(--color-border)',
            flexShrink: 0,
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: 8,
              marginBottom: 6,
            }}
          >
            <span
              style={{
                fontSize: 15,
                fontWeight: 600,
                color: 'var(--color-text-primary)',
                lineHeight: 1.35,
                flex: 1,
                wordBreak: 'break-word',
              }}
            >
              {row.name}
            </span>
            <button
              onClick={onClose}
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: 'var(--color-text-muted)',
                padding: 4,
                flexShrink: 0,
                display: 'flex',
                alignItems: 'center',
              }}
            >
              <X size={16} />
            </button>
          </div>
          <div
            style={{
              fontSize: 12,
              color: 'var(--color-text-muted)',
              display: 'flex',
              flexWrap: 'wrap',
              gap: '2px 8px',
            }}
          >
            <span>{formatDate(row.date)}</span>
            {row.institution && <span>{row.institution}</span>}
          </div>
        </div>

        {/* Status badges */}
        {(row.pending || row.is_transfer || row.is_duplicate || isRecurring ||
          row.needs_review || row.is_reimbursement || row.is_potential_duplicate) && (
          <div
            style={{
              padding: '10px 16px',
              borderBottom: '1px solid var(--color-border)',
              display: 'flex',
              flexWrap: 'wrap',
              gap: 6,
              flexShrink: 0,
            }}
          >
            {row.pending && (
              <span style={{ background: 'rgba(232,193,122,0.15)', color: '#e8c17a', fontSize: 11, padding: '3px 8px', borderRadius: 12 }}>
                pending
              </span>
            )}
            {row.is_transfer && (
              <span style={{ background: 'rgba(122,174,212,0.15)', color: '#7aaed4', fontSize: 11, padding: '3px 8px', borderRadius: 12 }}>
                transfer
              </span>
            )}
            {row.is_duplicate && (
              <span style={{ background: 'rgba(232,96,96,0.15)', color: 'var(--color-negative)', fontSize: 11, padding: '3px 8px', borderRadius: 12 }}>
                dup
              </span>
            )}
            {isRecurring && (
              <span
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 4,
                  background: 'var(--color-accent-soft)', color: 'var(--color-accent-text)',
                  fontSize: 11, padding: '3px 8px', borderRadius: 12,
                }}
              >
                <Repeat size={10} />
                recurring
              </span>
            )}
            {row.is_potential_duplicate && (
              <span
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 4,
                  background: 'rgba(232,193,122,0.18)', color: '#e8c17a',
                  fontSize: 11, padding: '3px 8px', borderRadius: 12,
                }}
              >
                <AlertTriangle size={10} />
                possible dup
              </span>
            )}
            {row.needs_review ? (
              <span
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 4,
                  background: 'rgba(232,193,122,0.18)', color: '#e8c17a',
                  fontSize: 11, padding: '3px 8px', borderRadius: 12,
                }}
              >
                <AlertTriangle size={10} />
                review
              </span>
            ) : row.is_reimbursement && (
              <span style={{ background: 'rgba(90,191,138,0.15)', color: 'var(--color-positive)', fontSize: 11, padding: '3px 8px', borderRadius: 12 }}>
                reimbursed
              </span>
            )}
          </div>
        )}

        {/* Editable fields */}
        <div style={{ padding: '16px', flex: 1 }}>

          {/* Category */}
          <div style={fieldRowStyle}>
            <div style={labelStyle}>Category</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: getCategoryColor(row.category),
                  flexShrink: 0,
                }}
              />
              <select
                value={row.category}
                onChange={(e) => onPatch(row.id, { category: e.target.value })}
                disabled={isPatchPending}
                style={{
                  fontSize: 13,
                  flex: 1,
                  background: 'var(--color-surface)',
                  border: '1px solid var(--color-border)',
                  borderRadius: 6,
                  color: 'var(--color-text-primary)',
                  padding: '6px 8px',
                  cursor: 'pointer',
                }}
              >
                {userCategories.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
                {!userCategories.includes(row.category) && row.category && (
                  <option value={row.category}>{row.category}</option>
                )}
              </select>
              {isSaved && (
                <Check size={14} style={{ color: 'var(--color-positive)', flexShrink: 0 }} />
              )}
            </div>
          </div>

          {/* Amount */}
          <div style={fieldRowStyle}>
            <div style={labelStyle}>
              Amount{' '}
              <span style={{ fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
                (negative = income)
              </span>
            </div>
            {editingAmount ? (
              <input
                type="text"
                inputMode="decimal"
                autoFocus
                value={amountDraft}
                onChange={(e) => setAmountDraft(e.target.value)}
                onBlur={commitAmount}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') commitAmount()
                  if (e.key === 'Escape') setEditingAmount(false)
                }}
                style={{
                  fontFamily: 'monospace',
                  fontSize: 14,
                  width: '100%',
                  border: '1px solid var(--color-border)',
                  borderRadius: 6,
                  padding: '6px 8px',
                  background: 'var(--color-surface)',
                  color: 'var(--color-text-primary)',
                }}
              />
            ) : (
              <span
                onClick={() => { setAmountDraft(row.amount.toString()); setEditingAmount(true) }}
                title="Click to edit"
                style={{
                  fontFamily: 'monospace',
                  fontSize: 16,
                  fontWeight: 600,
                  cursor: 'text',
                  color: row.amount < 0 ? 'var(--color-positive)' : 'var(--color-text-primary)',
                  display: 'inline-block',
                  padding: '4px 0',
                }}
              >
                {formatCurrency(row.amount)}
              </span>
            )}
          </div>

          {/* Notes */}
          <div style={fieldRowStyle}>
            <div style={labelStyle}>Notes</div>
            <textarea
              value={notesDraft}
              placeholder="Add a note…"
              onChange={(e) => setNotesDraft(e.target.value)}
              onBlur={commitNotes}
              rows={2}
              style={{
                width: '100%',
                fontSize: 13,
                borderRadius: 6,
                border: '1px solid var(--color-border)',
                background: 'var(--color-surface)',
                color: 'var(--color-text-primary)',
                padding: '6px 8px',
                resize: 'vertical',
                fontFamily: 'inherit',
                lineHeight: 1.5,
                boxSizing: 'border-box',
              }}
            />
          </div>

          {/* Merchant */}
          <div style={fieldRowStyle}>
            <div style={labelStyle}>Merchant</div>
            {editingMerchant ? (
              <div style={{ display: 'flex', gap: 6 }}>
                <input
                  type="text"
                  autoFocus
                  value={merchantDraft}
                  onChange={(e) => setMerchantDraft(e.target.value)}
                  onBlur={commitMerchant}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commitMerchant()
                    if (e.key === 'Escape') { setEditingMerchant(false); setMerchantDraft(row.merchant_normalized || '') }
                  }}
                  style={{
                    fontSize: 13,
                    flex: 1,
                    border: '1px solid var(--color-border)',
                    borderRadius: 6,
                    padding: '6px 8px',
                    background: 'var(--color-surface)',
                    color: 'var(--color-text-primary)',
                  }}
                />
                <button
                  onClick={() => { setEditingMerchant(false); setMerchantDraft(row.merchant_normalized || '') }}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-muted)', padding: 4, display: 'flex', alignItems: 'center' }}
                >
                  <X size={14} />
                </button>
              </div>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span
                  style={{
                    fontSize: 13,
                    color: 'var(--color-text-primary)',
                    flex: 1,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {row.merchant_normalized || '—'}
                </span>
                {categoryOverrideForMerchant && (
                  <span
                    title={`Rule: always categorized as "${categoryOverrideForMerchant}"`}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 4,
                      fontSize: 11,
                      background: 'var(--color-surface-raise)',
                      border: '1px solid var(--color-border)',
                      borderRadius: 10,
                      padding: '2px 8px',
                      color: getCategoryColor(categoryOverrideForMerchant),
                      flexShrink: 0,
                    }}
                  >
                    <Tag size={10} />
                    Rule: always {categoryOverrideForMerchant}
                  </span>
                )}
                <button
                  onClick={() => { setMerchantDraft(row.merchant_normalized || ''); setEditingMerchant(true) }}
                  title="Rename merchant"
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-muted)', padding: 4, display: 'flex', alignItems: 'center', flexShrink: 0 }}
                >
                  <Pencil size={13} />
                </button>
              </div>
            )}
            <p style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 4, lineHeight: 1.4 }}>
              Renames apply to all transactions from this merchant.
            </p>
          </div>

          {/* Recurring toggle */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '10px 12px',
              borderRadius: 8,
              border: '1px solid var(--color-border)',
              background: isRecurring ? 'var(--color-accent-soft)' : 'transparent',
              marginBottom: 16,
            }}
          >
            <div>
              <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--color-text-primary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                <Repeat size={14} style={{ color: isRecurring ? 'var(--color-accent)' : 'var(--color-text-muted)' }} />
                Recurring
              </div>
              {isRecurring && !recurringRule && (
                <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 2 }}>
                  Auto-detected
                </div>
              )}
            </div>
            <button
              onClick={() =>
                recurringRule
                  ? onUnmarkRecurring(recurringRule.id)
                  : onMarkRecurring(String(row.id))
              }
              disabled={isMarkRecurringPending || isUnmarkRecurringPending}
              style={{
                fontSize: 12,
                padding: '4px 12px',
                borderRadius: 6,
                border: `1px solid ${isRecurring ? 'var(--color-accent)' : 'var(--color-border)'}`,
                background: isRecurring ? 'var(--color-accent)' : 'var(--color-surface)',
                color: isRecurring ? 'var(--color-accent-contrast)' : 'var(--color-text-secondary)',
                cursor: 'pointer',
                fontWeight: 500,
                opacity: (isMarkRecurringPending || isUnmarkRecurringPending) ? 0.6 : 1,
              }}
            >
              {recurringRule ? 'Remove' : isRecurring ? 'Pin rule' : 'Mark recurring'}
            </button>
          </div>

          {/* Duplicate review callout */}
          {row.is_potential_duplicate && (
            <div
              style={{
                background: 'rgba(232,193,122,0.07)',
                border: '1px solid rgba(232,193,122,0.35)',
                borderRadius: 8,
                padding: '12px',
                marginBottom: 16,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 10 }}>
                <AlertTriangle size={14} style={{ color: '#e8c17a', flexShrink: 0, marginTop: 1 }} />
                <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', lineHeight: 1.5 }}>
                  <span style={{ fontWeight: 600, color: 'var(--color-text-primary)' }}>Possible duplicate</span>
                  {dupOf && (
                    <span>
                      {' — '}looks like{' '}
                      <span style={{ color: 'var(--color-text-primary)' }}>{dupOf.name}</span>
                      {' on '}{dupOf.date}{' for '}{formatCurrency(dupOf.amount)}
                    </span>
                  )}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  onClick={() => onDelete(String(row.id))}
                  disabled={isDeletePending}
                  style={{
                    flex: 1,
                    fontSize: 12,
                    padding: '5px 0',
                    borderRadius: 6,
                    background: 'var(--color-negative)',
                    border: 'none',
                    color: '#fff',
                    cursor: 'pointer',
                    fontWeight: 600,
                    opacity: isDeletePending ? 0.6 : 1,
                  }}
                >
                  Delete this one
                </button>
                <button
                  onClick={() => dupOf && onDismissDuplicate(String(row.id), dupOf.id)}
                  disabled={isDismissDupPending || !dupOf}
                  style={{
                    flex: 1,
                    fontSize: 12,
                    padding: '5px 0',
                    borderRadius: 6,
                    background: 'var(--color-surface-raise)',
                    border: '1px solid var(--color-border)',
                    color: 'var(--color-text-secondary)',
                    cursor: 'pointer',
                    opacity: isDismissDupPending ? 0.6 : 1,
                  }}
                >
                  Keep both
                </button>
              </div>
            </div>
          )}

          {/* Danger zone */}
          <div
            style={{
              marginTop: 'auto',
              paddingTop: 8,
              borderTop: '1px solid var(--color-border)',
            }}
          >
            <button
              onClick={() => onDelete(String(row.id))}
              style={{
                width: '100%',
                fontSize: 13,
                padding: '8px 0',
                borderRadius: 6,
                background: 'transparent',
                border: '1px solid var(--color-negative)',
                color: 'var(--color-negative)',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 6,
                fontWeight: 500,
              }}
            >
              <Trash2 size={14} />
              Delete transaction
            </button>
          </div>
        </div>
      </div>
    </>
  )
}

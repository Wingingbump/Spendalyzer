import { useState, useRef, useEffect } from 'react'
import { useIsMobile } from '../hooks/useIsMobile'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from 'recharts'
import { Check, X, Tag } from 'lucide-react'
import { merchantsApi, categoriesApi } from '../lib/api'
import { useFilters } from '../context/FilterContext'
import { useTheme } from '../context/ThemeContext'
import { formatCurrency, formatDate, CHART_COLORS_DARK, CHART_COLORS_LIGHT, getCategoryColor } from '../lib/utils'
import Card from '../components/Card'
import RangeSelect from '../components/RangeSelect'
import Spinner from '../components/Spinner'
import SkeletonRow from '../components/SkeletonRow'


function MerchantTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: { merchant_normalized: string; total: number; count: number } }> }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div
      className="rounded-lg px-3 py-2"
      style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)', fontSize: 12 }}
    >
      <p style={{ color: 'var(--color-text-secondary)', marginBottom: 2 }}>{d.merchant_normalized}</p>
      <p style={{ color: 'var(--color-text-primary)', fontFamily: 'monospace', fontWeight: 600 }}>
        {formatCurrency(d.total)}
      </p>
      <p style={{ color: 'var(--color-text-muted)' }}>{d.count} visits</p>
    </div>
  )
}

function MerchantNameEditor({
  name,
  onSave,
}: {
  name: string
  onSave: (v: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(name)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => { setDraft(name) }, [name])
  useEffect(() => { if (editing) inputRef.current?.focus() }, [editing])

  const commit = () => {
    const trimmed = draft.trim()
    if (trimmed && trimmed !== name) onSave(trimmed)
    setEditing(false)
  }

  const cancel = () => { setDraft(name); setEditing(false) }

  if (editing) {
    return (
      <div className="flex items-center gap-1.5">
        <input
          ref={inputRef}
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') cancel() }}
          style={{ fontSize: 12, width: 160, padding: '2px 6px' }}
        />
        <button onClick={commit} style={{ color: 'var(--color-positive)', lineHeight: 1 }}><Check size={13} /></button>
        <button onClick={cancel} style={{ color: 'var(--color-text-muted)', lineHeight: 1 }}><X size={13} /></button>
      </div>
    )
  }

  return (
    <span
      onDoubleClick={() => setEditing(true)}
      title="Double-click to rename"
      style={{ fontSize: 12, color: 'var(--color-text-secondary)', cursor: 'text' }}
    >
      {name}
    </span>
  )
}

interface ApplyDialog {
  merchant: string
  category: string
}

export default function Merchants() {
  const qc = useQueryClient()
  const { institution, account } = useFilters()
  const { theme } = useTheme()
  const isMobile = useIsMobile()
  const chartColors = theme === 'dark' ? CHART_COLORS_DARK : CHART_COLORS_LIGHT
  const [selectedMerchant, setSelectedMerchant] = useState<string>('')
  const [applyDialog, setApplyDialog] = useState<ApplyDialog | null>(null)
  const [range, setRange] = useState('90d')
  const params = { range, institution, account }

  const { data: merchants = [], isLoading } = useQuery({
    queryKey: ['merchants', range, institution, account],
    queryFn: () => merchantsApi.list(params),
  })

  const { data: categoryOverrides = {} } = useQuery({
    queryKey: ['merchant-category-overrides'],
    queryFn: () => merchantsApi.categoryOverrides(),
  })

  const { data: userCategories = [] } = useQuery({
    queryKey: ['user-categories'],
    queryFn: () => categoriesApi.userCategories(),
    staleTime: 300_000,
  })

  const saveMutation = useMutation({
    mutationFn: ({ rawName, displayName }: { rawName: string; displayName: string }) =>
      merchantsApi.saveOverride(rawName, displayName),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['merchants'] })
    },
  })

  const saveCategoryMutation = useMutation({
    mutationFn: ({ merchant, category }: { merchant: string; category: string }) =>
      merchantsApi.saveCategoryOverride(merchant, category),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: ['merchant-category-overrides'] })
      qc.invalidateQueries({ queryKey: ['merchants'] })
      setApplyDialog(vars)
    },
  })

  const deleteCategoryMutation = useMutation({
    mutationFn: (merchant: string) => merchantsApi.deleteCategoryOverride(merchant),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['merchant-category-overrides'] })
      qc.invalidateQueries({ queryKey: ['merchants'] })
    },
  })

  const applyHistoricalMutation = useMutation({
    mutationFn: ({ merchant, category }: { merchant: string; category: string }) =>
      merchantsApi.applyHistoricalCategory(merchant, category),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['merchant-detail'] })
      qc.invalidateQueries({ queryKey: ['merchants'] })
      setApplyDialog(null)
    },
  })

  const { data: merchantDetail = [], isLoading: loadingDetail } = useQuery({
    queryKey: ['merchant-detail', selectedMerchant, range, institution, account],
    queryFn: () => merchantsApi.detail(selectedMerchant, params),
    enabled: !!selectedMerchant,
  })

  const top15 = merchants.slice(0, 15)

  return (
    <div className="space-y-5 fade-in">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 style={{ fontSize: 20, fontWeight: 700, color: 'var(--color-text-primary)' }}>Merchants</h1>
          <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 2 }}>
            Spending by merchant
          </p>
        </div>
        <RangeSelect value={range} onChange={setRange} />
      </div>

      {/* Bar chart */}
      <Card>
        <p className="mb-4" style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-secondary)' }}>
          Top 15 Merchants
        </p>
        {isLoading ? (
          <div className="flex items-center justify-center" style={{ height: 400 }}>
            <Spinner />
          </div>
        ) : top15.length === 0 ? (
          <div className="flex items-center justify-center" style={{ height: 200 }}>
            <p style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>No data</p>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={Math.max(300, top15.length * 36)}>
            <BarChart
              data={top15}
              layout="vertical"
              margin={{ left: 0, right: 20, top: 0, bottom: 0 }}
            >
              <XAxis
                type="number"
                tick={{ fill: 'var(--color-text-muted)', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
              />
              <YAxis
                type="category"
                dataKey="merchant_normalized"
                tick={{ fill: 'var(--color-text-secondary)', fontSize: 12 }}
                axisLine={false}
                tickLine={false}
                width={isMobile ? 100 : 160}
              />
              <Tooltip content={<MerchantTooltip />} cursor={{ fill: 'var(--color-surface-raise)' }} />
              <Bar dataKey="total" radius={[0, 3, 3, 0]}>
                {top15.map((_, i) => (
                  <Cell
                    key={i}
                    fill={chartColors[i % chartColors.length]}
                    style={{ cursor: 'pointer' }}
                    onClick={() => setSelectedMerchant(top15[i].merchant_normalized)}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </Card>

      {/* Merchant list with rename + category override + drill-down */}
      <Card style={{ padding: 0, overflow: 'hidden' }}>
        <div className="px-4 py-3" style={{ borderBottom: '1px solid var(--color-border)' }}>
          <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-secondary)' }}>All Merchants</p>
          <p style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 2 }}>
            Double-click name to rename · set category to override Plaid · click row to drill down
          </p>
        </div>
        <div className="overflow-y-auto" style={{ maxHeight: 400 }}>
          {merchants.map((m) => {
            const isSelected = selectedMerchant === m.merchant_normalized
            const currentCat = categoryOverrides[m.merchant_normalized]
            return (
              <div
                key={m.merchant_normalized}
                className="flex items-center gap-3 px-4 py-2.5 cursor-pointer"
                style={{
                  borderBottom: '1px solid var(--color-border)',
                  background: isSelected ? 'var(--color-surface-raise)' : 'transparent',
                  transition: 'background 0.15s',
                }}
                onClick={() => setSelectedMerchant(isSelected ? '' : m.merchant_normalized)}
              >
                <div className="flex-1 min-w-0" onClick={(e) => e.stopPropagation()}>
                  <MerchantNameEditor
                    name={m.merchant_normalized}
                    onSave={(displayName) => saveMutation.mutate({ rawName: m.merchant_normalized, displayName })}
                  />
                </div>

                {/* Category override selector */}
                <div onClick={(e) => e.stopPropagation()} style={{ flexShrink: 0 }}>
                  <div className="flex items-center gap-1">
                    {currentCat && (
                      <span
                        className="px-2 py-0.5 rounded-full"
                        style={{ fontSize: 10, background: getCategoryColor(currentCat), color: '#fff', fontWeight: 600 }}
                      >
                        {currentCat}
                      </span>
                    )}
                    <select
                      value={currentCat ?? ''}
                      onChange={(e) => {
                        const val = e.target.value
                        if (!val) {
                          deleteCategoryMutation.mutate(m.merchant_normalized)
                        } else {
                          saveCategoryMutation.mutate({ merchant: m.merchant_normalized, category: val })
                        }
                      }}
                      style={{
                        fontSize: 10,
                        padding: '2px 4px',
                        background: 'var(--color-surface-raise)',
                        border: '1px solid var(--color-border)',
                        borderRadius: 4,
                        color: 'var(--color-text-muted)',
                        cursor: 'pointer',
                      }}
                      title="Override category for this merchant"
                    >
                      <option value="">
                        {currentCat ? 'Remove override' : 'Set category…'}
                      </option>
                      {userCategories.map((cat) => (
                        <option key={cat} value={cat}>{cat}</option>
                      ))}
                    </select>
                  </div>
                </div>

                <span style={{ fontSize: 11, color: 'var(--color-text-muted)', flexShrink: 0 }}>
                  {m.count} visits
                </span>
                <span style={{ fontSize: 12, fontFamily: 'monospace', color: 'var(--color-text-primary)', flexShrink: 0 }}>
                  {formatCurrency(m.total)}
                </span>
              </div>
            )
          })}
        </div>
      </Card>

      {/* hidden select kept for compat — replaced by list above */}
      <div className="hidden">
        <select
          value={selectedMerchant}
          onChange={(e) => setSelectedMerchant(e.target.value)}
        >
          <option value="">Select merchant…</option>
          {merchants.map((m) => (
            <option key={m.merchant_normalized} value={m.merchant_normalized}>
              {m.merchant_normalized} ({m.count})
            </option>
          ))}
        </select>
      </div>

      {/* Detail table */}
      {selectedMerchant && (
        <div
          className="rounded-xl overflow-hidden fade-in"
          style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
        >
          <div
            className="px-4 py-3"
            style={{ borderBottom: '1px solid var(--color-border)' }}
          >
            <p style={{ fontSize: 14, fontWeight: 600, color: 'var(--color-text-primary)' }}>
              {selectedMerchant}
            </p>
            {!loadingDetail && (
              <p style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2 }}>
                {merchantDetail.length} transactions · {formatCurrency(merchantDetail.reduce((s, t) => s + t.amount, 0))} total
              </p>
            )}
          </div>
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Name</th>
                  <th>Category</th>
                  <th style={{ textAlign: 'right' }}>Amount</th>
                  <th>Institution</th>
                </tr>
              </thead>
              <tbody>
                {loadingDetail ? (
                  <SkeletonRow cols={5} rows={6} />
                ) : merchantDetail.length === 0 ? (
                  <tr>
                    <td colSpan={5} style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: '24px 0' }}>
                      No transactions
                    </td>
                  </tr>
                ) : (
                  merchantDetail.map((tx) => (
                    <tr key={tx.id}>
                      <td style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
                        {formatDate(tx.date)}
                      </td>
                      <td style={{ fontSize: 12, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {tx.name}
                      </td>
                      <td>
                        <span
                          className="px-2 py-0.5 rounded-full text-xs"
                          style={{ background: 'var(--color-surface-raise)', color: 'var(--color-text-secondary)', fontSize: 11 }}
                        >
                          {tx.category}
                        </span>
                      </td>
                      <td style={{ textAlign: 'right', fontFamily: 'monospace', fontSize: 12 }}>
                        <span style={{ color: tx.amount < 0 ? 'var(--color-positive)' : 'var(--color-text-primary)' }}>
                          {formatCurrency(tx.amount)}
                        </span>
                      </td>
                      <td style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                        {tx.institution}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Apply historical dialog */}
      {applyDialog && (
        <div
          style={{
            position: 'fixed', inset: 0, zIndex: 50,
            background: 'rgba(0,0,0,0.55)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
          onClick={() => setApplyDialog(null)}
        >
          <div
            className="rounded-xl p-6"
            style={{
              background: 'var(--color-surface)',
              border: '1px solid var(--color-border)',
              maxWidth: 420, width: '90%',
              boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 mb-3">
              <Tag size={16} style={{ color: 'var(--color-accent)' }} />
              <p style={{ fontSize: 14, fontWeight: 600, color: 'var(--color-text-primary)' }}>
                Apply to past transactions?
              </p>
            </div>
            <p style={{ fontSize: 13, color: 'var(--color-text-secondary)', marginBottom: 4 }}>
              You set <strong>{applyDialog.merchant}</strong> to{' '}
              <strong>{applyDialog.category}</strong>.
            </p>
            <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 20 }}>
              Apply this category to all recorded past transactions from this merchant?
              Individual transaction overrides will not be affected.
            </p>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setApplyDialog(null)}
                style={{
                  fontSize: 13, padding: '6px 14px', borderRadius: 6,
                  background: 'var(--color-surface-raise)',
                  border: '1px solid var(--color-border)',
                  color: 'var(--color-text-secondary)', cursor: 'pointer',
                }}
              >
                Future only
              </button>
              <button
                disabled={applyHistoricalMutation.isPending}
                onClick={() => applyHistoricalMutation.mutate(applyDialog)}
                style={{
                  fontSize: 13, padding: '6px 14px', borderRadius: 6,
                  background: 'var(--color-accent)', color: '#000',
                  fontWeight: 600, cursor: 'pointer', border: 'none',
                  opacity: applyHistoricalMutation.isPending ? 0.6 : 1,
                }}
              >
                {applyHistoricalMutation.isPending ? 'Applying…' : 'Apply to all past'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

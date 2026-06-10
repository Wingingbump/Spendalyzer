import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
} from 'recharts'
import { categoriesApi } from '../lib/api'
import { useFilters } from '../context/FilterContext'
import { useTheme } from '../context/ThemeContext'
import { formatCurrency, formatDate, CHART_COLORS_DARK, CHART_COLORS_LIGHT } from '../lib/utils'
import Card from '../components/Card'
import RangeSelect from '../components/RangeSelect'
import Spinner from '../components/Spinner'
import SkeletonRow from '../components/SkeletonRow'

function CategoryTooltip({ active, payload }: { active?: boolean; payload?: Array<{ name: string; value: number; payload: { pct: number } }> }) {
  if (!active || !payload?.length) return null
  return (
    <div
      className="rounded-lg px-3 py-2"
      style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)', fontSize: 12 }}
    >
      <p style={{ color: 'var(--color-text-secondary)', marginBottom: 2 }}>{payload[0].name}</p>
      <p style={{ color: 'var(--color-text-primary)', fontFamily: 'monospace', fontWeight: 600 }}>
        {formatCurrency(payload[0].value)}
      </p>
      <p style={{ color: 'var(--color-text-muted)' }}>{payload[0].payload.pct.toFixed(1)}%</p>
    </div>
  )
}

export default function Categories({ embedded = false }: { embedded?: boolean }) {
  const { institution, account } = useFilters()
  const { theme } = useTheme()
  const chartColors = theme === 'dark' ? CHART_COLORS_DARK : CHART_COLORS_LIGHT
  const [selectedCategory, setSelectedCategory] = useState<string>('')
  const [range, setRange] = useState('90d')
  const params = { range, institution, account }

  const { data: categories = [], isLoading } = useQuery({
    queryKey: ['categories', range, institution, account],
    queryFn: () => categoriesApi.list(params),
  })

  const { data: categoryDetail = [], isLoading: loadingDetail } = useQuery({
    queryKey: ['category-detail', selectedCategory, range, institution, account],
    queryFn: () => categoriesApi.detail(selectedCategory, params),
    enabled: !!selectedCategory,
  })

  const total = categories.reduce((s, c) => s + c.total, 0)

  return (
    <div className="space-y-5 fade-in">
      {!embedded && (
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <h1 style={{ fontSize: 20, fontWeight: 700, color: 'var(--color-text-primary)' }}>Categories</h1>
            <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 2 }}>
              Spending breakdown by category
            </p>
          </div>
          <RangeSelect value={range} onChange={setRange} />
        </div>
      )}
      {embedded && (
        <div className="flex justify-end">
          <RangeSelect value={range} onChange={setRange} />
        </div>
      )}

      {/* Main two-column layout */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* Donut chart */}
        <Card>
          <p className="mb-4" style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-secondary)' }}>
            Spending Distribution
          </p>
          {isLoading ? (
            <div className="flex items-center justify-center" style={{ height: 300 }}>
              <Spinner />
            </div>
          ) : categories.length === 0 ? (
            <div className="flex items-center justify-center" style={{ height: 300 }}>
              <p style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>No data</p>
            </div>
          ) : (
            <>
              <ResponsiveContainer width="100%" height={280}>
                <PieChart>
                  <Pie
                    data={categories}
                    dataKey="total"
                    nameKey="category"
                    cx="50%"
                    cy="50%"
                    innerRadius={70}
                    outerRadius={110}
                    paddingAngle={2}
                    onClick={(entry) => setSelectedCategory(entry.category as string)}
                    style={{ cursor: 'pointer' }}
                  >
                    {categories.map((_, i) => (
                      <Cell
                        key={i}
                        fill={chartColors[i % chartColors.length]}
                        opacity={selectedCategory && selectedCategory !== categories[i].category ? 0.4 : 1}
                      />
                    ))}
                  </Pie>
                  <Tooltip content={<CategoryTooltip />} />
                </PieChart>
              </ResponsiveContainer>

              {/* Center text overlay */}
              <div className="text-center -mt-4">
                <p style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Total</p>
                <p style={{ fontSize: 18, fontWeight: 700, fontFamily: 'monospace', color: 'var(--color-text-primary)' }}>
                  {formatCurrency(total)}
                </p>
              </div>
            </>
          )}
        </Card>

        {/* Category list */}
        <Card style={{ padding: 0, overflow: 'hidden' }}>
          <div className="p-4" style={{ borderBottom: '1px solid var(--color-border)' }}>
            <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-secondary)' }}>
              All Categories
            </p>
          </div>
          <div className="overflow-y-auto" style={{ maxHeight: 380 }}>
            {isLoading ? (
              <div className="flex items-center justify-center p-8">
                <Spinner />
              </div>
            ) : (
              categories.map((cat, i) => (
                <button
                  key={cat.category}
                  onClick={() => setSelectedCategory(cat.category === selectedCategory ? '' : cat.category)}
                  className="w-full px-4 py-3 flex items-center gap-3 transition-colors text-left"
                  style={{
                    borderBottom: '1px solid var(--color-border)',
                    background: selectedCategory === cat.category ? 'var(--color-surface-raise)' : 'transparent',
                  }}
                >
                  <div
                    className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                    style={{ background: chartColors[i % chartColors.length] }}
                  />
                  <div className="flex-1 min-w-0">
                    <p style={{ fontSize: 13, color: 'var(--color-text-primary)', fontWeight: 500 }} className="truncate">
                      {cat.category}
                    </p>
                    <p style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                      {cat.count} transactions
                    </p>
                  </div>
                  <div className="text-right flex-shrink-0">
                    <p style={{ fontSize: 13, fontFamily: 'monospace', color: 'var(--color-text-primary)', fontWeight: 600 }}>
                      {formatCurrency(cat.total)}
                    </p>
                    <p style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                      {cat.pct.toFixed(1)}%
                    </p>
                  </div>
                </button>
              ))
            )}
          </div>
        </Card>
      </div>

      {/* Category drill-down */}
      <div className="flex items-center gap-3">
        <label style={{ fontSize: 13, color: 'var(--color-text-secondary)', fontWeight: 500 }}>
          Drill-down:
        </label>
        <select
          value={selectedCategory}
          onChange={(e) => setSelectedCategory(e.target.value)}
          style={{ minWidth: 200 }}
        >
          <option value="">Select category…</option>
          {categories.map((c) => (
            <option key={c.category} value={c.category}>
              {c.category} ({c.count})
            </option>
          ))}
        </select>
        {selectedCategory && (
          <button
            onClick={() => setSelectedCategory('')}
            style={{ fontSize: 12, color: 'var(--color-text-muted)' }}
          >
            Clear
          </button>
        )}
      </div>

      {/* Detail table */}
      {selectedCategory && (
        <div
          className="rounded-xl overflow-hidden fade-in"
          style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
        >
          <div
            className="px-4 py-3"
            style={{ borderBottom: '1px solid var(--color-border)' }}
          >
            <p style={{ fontSize: 14, fontWeight: 600, color: 'var(--color-text-primary)' }}>
              {selectedCategory}
            </p>
            {!loadingDetail && (
              <p style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2 }}>
                {categoryDetail.length} transactions · {formatCurrency(categoryDetail.reduce((s, t) => s + t.amount, 0))} total
              </p>
            )}
          </div>
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Name</th>
                  <th>Merchant</th>
                  <th style={{ textAlign: 'right' }}>Amount</th>
                  <th>Institution</th>
                </tr>
              </thead>
              <tbody>
                {loadingDetail ? (
                  <SkeletonRow cols={5} rows={6} />
                ) : categoryDetail.length === 0 ? (
                  <tr>
                    <td colSpan={5} style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: '24px 0' }}>
                      No transactions
                    </td>
                  </tr>
                ) : (
                  categoryDetail.map((tx) => (
                    <tr key={tx.id}>
                      <td style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
                        {formatDate(tx.date)}
                      </td>
                      <td style={{ fontSize: 12, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {tx.name}
                      </td>
                      <td style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
                        {tx.merchant_normalized || '—'}
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
    </div>
  )
}

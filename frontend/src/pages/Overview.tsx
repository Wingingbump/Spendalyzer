
import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, Cell,
} from 'recharts'
import { ShoppingBag, Store, Calendar, CreditCard, X, BarChart2, ArrowLeftRight, Bot, ChevronRight, ChevronLeft, AlertTriangle, RefreshCw, CheckCircle } from 'lucide-react'
import { accountsApi, insightsApi, syncApi } from '../lib/api'
import type { HealthWarning } from '../lib/api'
import { useFilters } from '../context/FilterContext'
import { useTheme } from '../context/ThemeContext'
import { useAuth } from '../context/AuthContext'
import { formatCurrency, formatMonth, CHART_COLORS_DARK, CHART_COLORS_LIGHT } from '../lib/utils'
import Card from '../components/Card'
import MetricCard from '../components/MetricCard'
import Spinner from '../components/Spinner'

// Custom tooltip for bar chart
function MonthlyTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number }>; label?: string }) {
  if (!active || !payload?.length) return null
  return (
    <div
      className="rounded-lg px-3 py-2"
      style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)', fontSize: 12 }}
    >
      <p style={{ color: 'var(--color-text-muted)', marginBottom: 2 }}>{label}</p>
      <p style={{ color: 'var(--color-text-primary)', fontFamily: 'monospace', fontWeight: 600 }}>
        {formatCurrency(payload[0].value)}
      </p>
    </div>
  )
}

function DowTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number }>; label?: string }) {
  if (!active || !payload?.length) return null
  return (
    <div
      className="rounded-lg px-3 py-2"
      style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)', fontSize: 12 }}
    >
      <p style={{ color: 'var(--color-text-muted)', marginBottom: 2 }}>{label}</p>
      <p style={{ color: 'var(--color-text-primary)', fontFamily: 'monospace', fontWeight: 600 }}>
        {formatCurrency(payload[0].value)}
      </p>
    </div>
  )
}


const TOUR_SLIDES = [
  {
    icon: CreditCard,
    iconBg: 'rgba(200,255,0,0.12)',
    iconColor: 'var(--color-accent)',
    title: 'Welcome to spend.',
    body: 'spend. pulls your real transactions from your bank and turns them into insights — and eventually, personalized AI advice. Let\'s show you around.',
    cta: null,
  },
  {
    icon: BarChart2,
    iconBg: 'rgba(200,255,0,0.12)',
    iconColor: 'var(--color-accent)',
    title: 'Overview',
    body: 'Your financial snapshot. See total spending, monthly trends, top categories, and your biggest purchases — all filterable by account and time range.',
    cta: null,
  },
  {
    icon: ArrowLeftRight,
    iconBg: 'rgba(90,191,138,0.12)',
    iconColor: 'var(--color-positive)',
    title: 'Transactions',
    body: 'Every transaction in one place. Search, filter by category, and edit details. The Ledger tab gives you an income vs. expenses view.',
    cta: null,
  },
  {
    icon: Bot,
    iconBg: 'rgba(139,92,246,0.12)',
    iconColor: '#8b5cf6',
    title: 'Advisor',
    body: 'Ask your AI financial advisor anything. It reads your actual spending data to give you specific, actionable answers — not generic advice.',
    cta: null,
  },
  {
    icon: CreditCard,
    iconBg: 'rgba(200,255,0,0.12)',
    iconColor: 'var(--color-accent)',
    title: 'First step: connect an account',
    body: 'Head to Settings to connect your bank via Plaid. Once connected, your transactions will sync automatically and everything comes to life.',
    cta: 'Go to Settings',
  },
]

function OnboardingTour({ onDismiss }: { onDismiss: () => void }) {
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const slide = TOUR_SLIDES[step]
  const Icon = slide.icon
  const isLast = step === TOUR_SLIDES.length - 1

  return (
    <div className="fixed inset-0 flex items-center justify-center" style={{ background: 'rgba(0,0,0,0.55)', zIndex: 50 }}>
      <div className="rounded-2xl p-8 w-full relative" style={{ maxWidth: 440, background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}>
        <button
          onClick={onDismiss}
          style={{ position: 'absolute', top: 16, right: 16, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-muted)', padding: 4 }}
        >
          <X size={16} />
        </button>

        {/* Step dots */}
        <div className="flex gap-1.5 mb-6">
          {TOUR_SLIDES.map((_, i) => (
            <div
              key={i}
              className="rounded-full transition-all"
              style={{
                width: i === step ? 20 : 6,
                height: 6,
                background: i === step ? 'var(--color-accent)' : 'var(--color-border)',
              }}
            />
          ))}
        </div>

        <div className="w-12 h-12 rounded-xl flex items-center justify-center mb-5" style={{ background: slide.iconBg }}>
          <Icon size={22} style={{ color: slide.iconColor }} />
        </div>

        <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--color-text-primary)', marginBottom: 8 }}>
          {slide.title}
        </h2>
        <p style={{ fontSize: 13, color: 'var(--color-text-muted)', lineHeight: 1.65, marginBottom: 28 }}>
          {slide.body}
        </p>

        <div className="flex items-center gap-3">
          {step > 0 && (
            <button
              onClick={() => setStep(s => s - 1)}
              className="px-4 py-2.5 rounded-lg font-medium flex items-center gap-1.5"
              style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)', fontSize: 13 }}
            >
              <ChevronLeft size={14} /> Back
            </button>
          )}
          <button
            onClick={() => {
              if (isLast && slide.cta) {
                onDismiss()
                navigate('/settings')
              } else if (isLast) {
                onDismiss()
              } else {
                setStep(s => s + 1)
              }
            }}
            className="flex-1 py-2.5 rounded-lg font-semibold flex items-center justify-center gap-1.5"
            style={{ background: 'var(--color-accent)', color: '#000', fontSize: 14 }}
          >
            {isLast ? (slide.cta ?? 'Done') : 'Next'}
            {!isLast && <ChevronRight size={14} />}
          </button>
        </div>
      </div>
    </div>
  )
}

function DataHealthCard() {
  const qc = useQueryClient()

  const { data: health, isLoading } = useQuery({
    queryKey: ['data-health'],
    queryFn: () => insightsApi.health(),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  })

  const syncMutation = useMutation({
    mutationFn: () => syncApi.sync(true),
    onSuccess: () => {
      qc.invalidateQueries()
    },
  })

  if (isLoading) return null
  if (!health) return null

  const isOk = health.status === 'ok'
  const isError = health.status === 'error'
  const warnings = health.warnings

  if (isOk) {
    return (
      <div
        className="flex items-center gap-2 px-4 py-2.5 rounded-xl"
        style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
      >
        <CheckCircle size={14} style={{ color: 'var(--color-positive)', flexShrink: 0 }} />
        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>All data looks healthy</span>
      </div>
    )
  }

  return (
    <div
      className="rounded-xl"
      style={{
        background: isError ? 'rgba(232, 96, 96, 0.06)' : 'rgba(232, 193, 122, 0.06)',
        border: `1px solid ${isError ? 'rgba(232, 96, 96, 0.3)' : 'rgba(232, 193, 122, 0.3)'}`,
        padding: '14px 16px',
      }}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-2.5 flex-1 min-w-0">
          <AlertTriangle
            size={15}
            style={{ color: isError ? 'var(--color-negative)' : '#e8c17a', flexShrink: 0, marginTop: 1 }}
          />
          <div className="flex-1 min-w-0">
            <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 6 }}>
              {warnings.length === 1 ? 'Data issue detected' : `${warnings.length} data issues detected`}
            </p>
            <div className="flex flex-col gap-1.5">
              {warnings.map((w: HealthWarning, i: number) => {
                const icon =
                  w.type === 'item_error' || w.type === 'consent_expired' || w.type === 'sync_failure' ? '⚠ ' :
                  w.type === 'consent_expiring' ? '🔑 ' :
                  w.type === 'stuck_pending' ? '⏳ ' :
                  w.type === 'volume_drop' ? '📉 ' :
                  w.type === 'missing_recurring' ? '↻ ' : '⬤ '
                return (
                  <p key={i} style={{ fontSize: 12, color: 'var(--color-text-secondary)', lineHeight: 1.45 }}>
                    {icon}{w.message}
                  </p>
                )
              })}
            </div>
          </div>
        </div>
        <div className="flex flex-col items-end gap-1.5 flex-shrink-0">
          <button
            onClick={() => syncMutation.mutate()}
            disabled={syncMutation.isPending}
            className="flex items-center gap-1.5"
            style={{
              padding: '5px 12px',
              fontSize: 12,
              fontWeight: 600,
              background: isError ? 'var(--color-negative)' : '#e8c17a',
              color: '#000',
              border: 'none',
              borderRadius: 6,
              cursor: 'pointer',
              opacity: syncMutation.isPending ? 0.7 : 1,
              whiteSpace: 'nowrap',
            }}
          >
            <RefreshCw size={11} className={syncMutation.isPending ? 'spinner' : ''} />
            {syncMutation.isPending ? 'Syncing…' : 'Full history sync'}
          </button>
          {syncMutation.isSuccess && (
            <span style={{ fontSize: 11, color: 'var(--color-positive)' }}>
              +{(syncMutation.data as { synced_count?: number })?.synced_count ?? 0} transactions pulled
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

export default function Overview() {
  const { range, institution, account } = useFilters()
  const { theme } = useTheme()
  const { user } = useAuth()
  const chartColors = theme === 'dark' ? CHART_COLORS_DARK : CHART_COLORS_LIGHT
  const params = { range, institution, account }
  const INSIGHTS_STALE = 3 * 60_000
  const [monthView, setMonthView] = useState<'this' | 'last'>('this')

  const { data: accounts = [], isLoading: loadingAccounts } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
    staleTime: INSIGHTS_STALE,
  })

  const bannerKey = `onboarding_dismissed_${user?.id}`
  const [showBanner, setShowBanner] = useState(false)

  useEffect(() => {
    if (!loadingAccounts && accounts.length === 0 && !localStorage.getItem(bannerKey)) {
      setShowBanner(true)
    }
  }, [loadingAccounts, accounts.length, bannerKey])

  const dismissBanner = () => {
    localStorage.setItem(bannerKey, '1')
    setShowBanner(false)
  }

  const { data: summary, isLoading: loadingSummary } = useQuery({
    queryKey: ['summary', range, institution, account],
    queryFn: () => insightsApi.summary(params),
    staleTime: INSIGHTS_STALE,
  })

  const { data: monthly = [], isLoading: loadingMonthly } = useQuery({
    queryKey: ['monthly', range, institution, account],
    queryFn: () => insightsApi.monthly(params),
    staleTime: INSIGHTS_STALE,
  })

  const { data: categories = [], isLoading: loadingCategories } = useQuery({
    queryKey: ['categories', range, institution, account],
    queryFn: () => insightsApi.categories(params),
    staleTime: INSIGHTS_STALE,
  })

  const { data: dow = [], isLoading: loadingDow } = useQuery({
    queryKey: ['dow', range, institution, account],
    queryFn: () => insightsApi.dow(params),
    staleTime: INSIGHTS_STALE,
  })

  const delta = summary?.delta ?? 0
  const deltaPct = summary?.delta_pct ?? 0
  const isPositiveDelta = delta <= 0

  // ── Derived pace metrics ───────────────────────────────────────────────────
  const today = new Date()
  const daysInMonth = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate()
  const daysElapsed = today.getDate()
  const daysRemaining = daysInMonth - daysElapsed
  const dailyRate = summary && daysElapsed > 0 ? summary.this_month / daysElapsed : 0
  const projected = Math.round(dailyRate * daysInMonth)
  const isPaceOver = summary?.last_month ? projected > summary.last_month : false
  const paceVsLast = summary?.last_month && summary.last_month > 0
    ? ((projected - summary.last_month) / summary.last_month * 100).toFixed(0)
    : null

  // ── Monthly chart helpers ──────────────────────────────────────────────────
  const monthlyAvg = monthly.length > 1
    ? monthly.reduce((s, m) => s + m.total, 0) / monthly.length
    : 0
  const currentMonthKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`

  // ── Savings proxy (this month credits vs debits) ───────────────────────────
  const savingsPct = summary?.total_credits && summary.total_credits > 0
    ? Math.round((1 - summary.this_month / summary.total_credits) * 100)
    : null

  return (
    <div className="space-y-5 fade-in">
      {showBanner && <OnboardingTour onDismiss={dismissBanner} />}
      <div>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: 'var(--color-text-primary)' }}>Overview</h1>
        <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 2 }}>
          Your spending summary
        </p>
      </div>

      <DataHealthCard />

      {/* Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <MetricCard
          label={monthView === 'this' ? 'This Month' : 'Last Month'}
          value={formatCurrency((monthView === 'this' ? summary?.this_month : summary?.last_month) ?? 0)}
          sub={
            summary
              ? monthView === 'this'
                ? `${isPositiveDelta ? '↓' : '↑'} ${formatCurrency(Math.abs(delta))} vs last month (${Math.abs(deltaPct).toFixed(1)}%)`
                : 'Final total for last month'
              : undefined
          }
          subPositive={monthView === 'this' ? isPositiveDelta : undefined}
          isLoading={loadingSummary}
          hero
          tabs={[
            { key: 'this', label: 'This Month' },
            { key: 'last', label: 'Last Month' },
          ]}
          activeTab={monthView}
          onTabChange={(k) => setMonthView(k as 'this' | 'last')}
        />
        <MetricCard
          label={`On Pace For · ${daysRemaining}d left`}
          value={loadingSummary ? '—' : formatCurrency(projected)}
          sub={
            !loadingSummary && paceVsLast !== null
              ? `${isPaceOver ? '↑' : '↓'} ${Math.abs(Number(paceVsLast))}% vs last month`
              : undefined
          }
          subPositive={!isPaceOver}
          isLoading={loadingSummary}
        />
        <MetricCard
          label="Est. Savings Rate"
          value={loadingSummary ? '—' : savingsPct !== null ? `${savingsPct}%` : '—'}
          sub={summary ? `Net ${formatCurrency(summary.net_spend)}` : undefined}
          subPositive={savingsPct !== null && savingsPct > 0}
          isLoading={loadingSummary}
        />
      </div>

      {/* Monthly Chart */}
      <Card>
        <p className="mb-4" style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-secondary)' }}>
          Monthly Spending
        </p>
        {loadingMonthly ? (
          <div className="flex items-center justify-center" style={{ height: 200 }}>
            <Spinner />
          </div>
        ) : monthly.length === 0 ? (
          <div className="flex items-center justify-center" style={{ height: 200 }}>
            <p style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>No data</p>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={monthly.map((m) => ({ ...m, label: formatMonth(m.month) }))}>
              <XAxis
                dataKey="label"
                tick={{ fill: 'var(--color-text-muted)', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: 'var(--color-text-muted)', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
                width={45}
              />
              <Tooltip content={<MonthlyTooltip />} cursor={{ fill: 'var(--color-surface-raise)' }} />
              {monthlyAvg > 0 && (
                <ReferenceLine
                  y={monthlyAvg}
                  stroke="var(--color-text-muted)"
                  strokeDasharray="4 3"
                  strokeWidth={1}
                  label={{ value: 'avg', position: 'insideTopRight', fontSize: 10, fill: 'var(--color-text-muted)', dy: -4 }}
                />
              )}
              <Bar dataKey="total" radius={[3, 3, 0, 0]}>
                {monthly.map((m) => (
                  <Cell
                    key={m.month}
                    fill={m.month === currentMonthKey ? 'var(--color-accent)' : chartColors[0]}
                    opacity={m.month === currentMonthKey ? 1 : 0.7}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </Card>

      {/* Category + DOW charts */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Categories */}
        <Card>
          <p className="mb-4" style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-secondary)' }}>
            By Category
          </p>
          {loadingCategories ? (
            <div className="flex items-center justify-center" style={{ height: 200 }}>
              <Spinner />
            </div>
          ) : categories.length === 0 ? (
            <div className="flex items-center justify-center" style={{ height: 200 }}>
              <p style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>No data</p>
            </div>
          ) : (
            <div className="space-y-2">
              {categories.slice(0, 8).map((cat, i) => (
                <div key={cat.category}>
                  <div className="flex items-center justify-between mb-0.5">
                    <span style={{ fontSize: 12, color: 'var(--color-text-secondary)' }} className="truncate max-w-32">
                      {cat.category}
                    </span>
                    <span style={{ fontSize: 12, fontFamily: 'monospace', color: 'var(--color-text-primary)' }}>
                      {formatCurrency(cat.total)}
                    </span>
                  </div>
                  <div className="rounded-full overflow-hidden" style={{ height: 6, background: 'var(--color-surface-raise)' }}>
                    <div
                      className="h-full rounded-full"
                      style={{ width: `${cat.pct}%`, background: chartColors[i % chartColors.length] }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        {/* Day of Week */}
        <Card>
          <p className="mb-4" style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-secondary)' }}>
            Typical Day of Week
          </p>
          {loadingDow ? (
            <div className="flex items-center justify-center" style={{ height: 200 }}>
              <Spinner />
            </div>
          ) : dow.length === 0 ? (
            <div className="flex items-center justify-center" style={{ height: 200 }}>
              <p style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>No data</p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={dow}>
                <XAxis
                  dataKey="day"
                  tick={{ fill: 'var(--color-text-muted)', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fill: 'var(--color-text-muted)', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
                  width={40}
                />
                <Tooltip content={<DowTooltip />} cursor={{ fill: 'var(--color-surface-raise)' }} />
                <Bar dataKey="total" fill={chartColors[1]} radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>

      {/* Highlight strip — 3 stats in one compact card */}
      <Card>
        <div className="grid grid-cols-1 sm:grid-cols-3">
          {/* Biggest Purchase */}
          <div className="flex items-start gap-2.5 pr-4">
            <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5" style={{ background: 'rgba(200,255,0,0.10)' }}>
              <ShoppingBag size={13} style={{ color: 'var(--color-accent)' }} />
            </div>
            <div className="flex-1 min-w-0">
              <p style={{ fontSize: 10, color: 'var(--color-text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Biggest Purchase</p>
              {loadingSummary ? <div className="skeleton mt-1.5" style={{ height: 16, width: 80 }} /> : summary?.biggest_purchase ? (
                <>
                  <p style={{ fontSize: 15, fontWeight: 700, fontFamily: 'monospace', color: 'var(--color-text-primary)', marginTop: 3 }}>{formatCurrency(summary.biggest_purchase.amount)}</p>
                  <p className="truncate" style={{ fontSize: 11, color: 'var(--color-text-secondary)', marginTop: 1 }}>{summary.biggest_purchase.name}</p>
                  <p style={{ fontSize: 10, color: 'var(--color-text-muted)', marginTop: 1 }}>{summary.biggest_purchase.date}</p>
                </>
              ) : <p style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 4 }}>—</p>}
            </div>
          </div>

          {/* Most Visited */}
          <div
            className="flex items-start gap-2.5 px-4 pt-4 sm:pt-0 sm:pl-4 border-t sm:border-t-0 sm:border-l"
            style={{ borderColor: 'var(--color-border)' }}
          >
            <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5" style={{ background: 'rgba(90,191,138,0.10)' }}>
              <Store size={13} style={{ color: 'var(--color-positive)' }} />
            </div>
            <div className="flex-1 min-w-0">
              <p style={{ fontSize: 10, color: 'var(--color-text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Most Visited</p>
              {loadingSummary ? <div className="skeleton mt-1.5" style={{ height: 16, width: 80 }} /> : summary?.most_visited_merchant ? (
                <>
                  <p className="truncate" style={{ fontSize: 15, fontWeight: 700, color: 'var(--color-text-primary)', marginTop: 3 }}>{summary.most_visited_merchant.merchant}</p>
                  <p style={{ fontSize: 11, color: 'var(--color-text-secondary)', marginTop: 1 }}>{summary.most_visited_merchant.count} visits · {formatCurrency(summary.most_visited_merchant.total)}</p>
                </>
              ) : <p style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 4 }}>—</p>}
            </div>
          </div>

          {/* Biggest Day */}
          <div
            className="flex items-start gap-2.5 px-4 pt-4 sm:pt-0 sm:pl-4 border-t sm:border-t-0 sm:border-l"
            style={{ borderColor: 'var(--color-border)' }}
          >
            <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5" style={{ background: 'rgba(232,96,96,0.10)' }}>
              <Calendar size={13} style={{ color: 'var(--color-negative)' }} />
            </div>
            <div className="flex-1 min-w-0">
              <p style={{ fontSize: 10, color: 'var(--color-text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Biggest Day</p>
              {loadingSummary ? <div className="skeleton mt-1.5" style={{ height: 16, width: 80 }} /> : summary?.biggest_spending_day ? (
                <>
                  <p style={{ fontSize: 15, fontWeight: 700, fontFamily: 'monospace', color: 'var(--color-text-primary)', marginTop: 3 }}>{formatCurrency(summary.biggest_spending_day.total)}</p>
                  <p style={{ fontSize: 10, color: 'var(--color-text-muted)', marginTop: 1 }}>{summary.biggest_spending_day.date}</p>
                </>
              ) : <p style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 4 }}>—</p>}
            </div>
          </div>
        </div>
      </Card>
    </div>
  )
}

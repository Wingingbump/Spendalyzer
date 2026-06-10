import { useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import {
  BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Cell,
  PieChart, Pie,
} from 'recharts'
import {
  Wallet, TrendingUp, Tag, Bot, ArrowRight, ShoppingBag, Coffee,
  Car, Home,
} from 'lucide-react'
import { useTheme } from '../context/ThemeContext'
import { formatCurrency, CHART_COLORS_DARK, CHART_COLORS_LIGHT } from '../lib/utils'

const MOCK_MONTHLY = [
  { month: 'Dec', total: 2840 },
  { month: 'Jan', total: 3120 },
  { month: 'Feb', total: 2680 },
  { month: 'Mar', total: 3450 },
  { month: 'Apr', total: 2980 },
  { month: 'May', total: 3210 },
]

const MOCK_TRANSACTIONS = [
  { id: 1, merchant: 'Whole Foods', category: 'Groceries', amount: 84.32, icon: ShoppingBag },
  { id: 2, merchant: 'Blue Bottle Coffee', category: 'Food & Drink', amount: 6.75, icon: Coffee },
  { id: 3, merchant: 'Uber', category: 'Transport', amount: 18.40, icon: Car },
  { id: 4, merchant: 'Pacific Gas & Electric', category: 'Utilities', amount: 142.18, icon: Home },
  { id: 5, merchant: 'Trader Joe\'s', category: 'Groceries', amount: 52.91, icon: ShoppingBag },
]

const MOCK_CATEGORIES = [
  { name: 'Groceries', value: 680, color: '#22c55e' },
  { name: 'Food & Drink', value: 420, color: '#f97316' },
  { name: 'Transport', value: 310, color: '#3b82f6' },
  { name: 'Shopping', value: 540, color: '#a855f7' },
  { name: 'Utilities', value: 280, color: '#06b6d4' },
]

const FEATURES = [
  {
    icon: Wallet,
    title: 'Connect every account',
    body: 'Link banks and cards via Plaid. Transactions sync automatically — no spreadsheets, no CSV exports.',
  },
  {
    icon: TrendingUp,
    title: 'See where it goes',
    body: 'Monthly trends, day-of-week patterns, and merchant breakdowns surface the spending you actually do.',
  },
  {
    icon: Tag,
    title: 'Smart categorization',
    body: 'Auto-categorized by merchant with manual overrides. Recurring charges detected and grouped.',
  },
  {
    icon: Bot,
    title: 'Ask your data',
    body: 'A built-in advisor answers questions about your spending in plain language.',
  },
]

export default function Welcome() {
  const { theme } = useTheme()
  const { user } = useAuth()
  const navigate = useNavigate()
  const palette = theme === 'dark' ? CHART_COLORS_DARK : CHART_COLORS_LIGHT
  const accent = palette[0]

  // Warm the Render dyno while the user reads. Public endpoint, no auth.
  useEffect(() => {
    const base = (import.meta as unknown as { env: { VITE_API_URL?: string } }).env.VITE_API_URL || '/api'
    fetch(`${base}/health`, { credentials: 'omit' }).catch(() => {})
  }, [])

  // If the user is already signed in (e.g. landed on / by typing the bare URL),
  // forward them to the dashboard once auth resolves.
  useEffect(() => {
    if (user) navigate('/overview', { replace: true })
  }, [user, navigate])

  const ctaLabel = user ? 'Open dashboard' : 'Sign in'
  const ctaTo = user ? '/overview' : '/login'

  const peakMonth = MOCK_MONTHLY.reduce((m, x) => (x.total > m.total ? x : m), MOCK_MONTHLY[0])

  return (
    <div className="min-h-screen" style={{ background: 'var(--color-bg)', color: 'var(--color-text-primary)' }}>
      {/* Top bar */}
      <header className="flex items-center justify-between px-6 py-5 max-w-6xl mx-auto">
        <Link to="/welcome" aria-label="Home" style={{ textDecoration: 'none' }}>
          <span style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.03em', color: 'var(--color-text-primary)' }}>
            spend<span style={{ color: 'var(--color-accent)' }}>.</span>
          </span>
        </Link>
        <Link
          to={ctaTo}
          className="rounded-lg px-4 py-2"
          style={{ background: 'var(--color-accent)', color: 'var(--color-accent-contrast)', fontSize: 13, fontWeight: 600 }}
        >
          {ctaLabel}
        </Link>
      </header>

      {/* Hero */}
      <section className="px-6 pt-8 pb-12 max-w-6xl mx-auto text-center">
        <h1 style={{ fontSize: 'clamp(32px, 5vw, 52px)', fontWeight: 700, lineHeight: 1.1, letterSpacing: '-0.02em' }}>
          Personal finance,<br />
          <span style={{ color: accent }}>without the spreadsheet.</span>
        </h1>
        <p className="mx-auto mt-5" style={{ maxWidth: 560, fontSize: 16, color: 'var(--color-text-muted)', lineHeight: 1.5 }}>
          Connect your accounts, see every transaction, and understand your spending in seconds.
        </p>
        <div className="mt-7 flex items-center justify-center gap-3">
          <Link
            to={ctaTo}
            className="rounded-lg px-5 py-2.5 inline-flex items-center gap-2"
            style={{ background: 'var(--color-accent)', color: 'var(--color-accent-contrast)', fontSize: 14, fontWeight: 600 }}
          >
            {user ? 'Open dashboard' : 'Get started'} <ArrowRight size={16} />
          </Link>
          <a
            href="#preview"
            className="rounded-lg px-5 py-2.5"
            style={{ border: '1px solid var(--color-border)', color: 'var(--color-text-primary)', fontSize: 14, fontWeight: 500 }}
          >
            See preview
          </a>
        </div>
      </section>

      {/* Preview panels */}
      <section id="preview" className="px-6 pb-16 max-w-6xl mx-auto">
        <div className="mb-3 flex items-center gap-2">
          <span
            className="rounded-full px-2 py-0.5"
            style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)', fontSize: 10, color: 'var(--color-text-muted)', letterSpacing: '0.04em', textTransform: 'uppercase' }}
          >
            Demo data
          </span>
          <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
            A peek at what your dashboard could look like.
          </span>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Monthly trend */}
          <div
            className="rounded-xl p-5 lg:col-span-2"
            style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
          >
            <div className="flex items-baseline justify-between mb-1">
              <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Monthly spend</div>
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>last 6 months</div>
            </div>
            <div style={{ fontSize: 28, fontWeight: 700, fontFamily: 'monospace' }}>
              {formatCurrency(MOCK_MONTHLY[MOCK_MONTHLY.length - 1].total)}
            </div>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 12 }}>
              Peak: {peakMonth.month} · {formatCurrency(peakMonth.total)}
            </div>
            <div style={{ height: 180 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={MOCK_MONTHLY} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                  <XAxis dataKey="month" tick={{ fill: 'var(--color-text-muted)', fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis hide />
                  <Bar dataKey="total" radius={[6, 6, 0, 0]}>
                    {MOCK_MONTHLY.map((entry, i) => (
                      <Cell
                        key={entry.month}
                        fill={i === MOCK_MONTHLY.length - 1 ? accent : 'var(--color-border)'}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Category donut */}
          <div
            className="rounded-xl p-5"
            style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
          >
            <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>By category</div>
            <div style={{ height: 140 }}>
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={MOCK_CATEGORIES}
                    dataKey="value"
                    innerRadius={42}
                    outerRadius={62}
                    paddingAngle={2}
                    stroke="none"
                  >
                    {MOCK_CATEGORIES.map((c) => (
                      <Cell key={c.name} fill={c.color} />
                    ))}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-3 space-y-1.5">
              {MOCK_CATEGORIES.map((c) => (
                <div key={c.name} className="flex items-center justify-between" style={{ fontSize: 12 }}>
                  <span className="flex items-center gap-2" style={{ color: 'var(--color-text-secondary)' }}>
                    <span style={{ width: 8, height: 8, borderRadius: 2, background: c.color, display: 'inline-block' }} />
                    {c.name}
                  </span>
                  <span style={{ fontFamily: 'monospace', color: 'var(--color-text-muted)' }}>
                    {formatCurrency(c.value)}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Recent transactions */}
          <div
            className="rounded-xl p-5 lg:col-span-3"
            style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
          >
            <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>Recent transactions</div>
            <div className="divide-y" style={{ borderColor: 'var(--color-border)' }}>
              {MOCK_TRANSACTIONS.map((tx) => {
                const Icon = tx.icon
                return (
                  <div key={tx.id} className="flex items-center justify-between py-2.5" style={{ borderTop: tx.id === 1 ? 'none' : '1px solid var(--color-border)' }}>
                    <div className="flex items-center gap-3">
                      <div
                        className="flex items-center justify-center rounded-lg"
                        style={{ width: 32, height: 32, background: 'var(--color-surface-raise)', color: 'var(--color-text-secondary)' }}
                      >
                        <Icon size={15} />
                      </div>
                      <div>
                        <div style={{ fontSize: 13, fontWeight: 500 }}>{tx.merchant}</div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{tx.category}</div>
                      </div>
                    </div>
                    <div style={{ fontSize: 13, fontFamily: 'monospace', fontWeight: 500 }}>
                      −{formatCurrency(tx.amount)}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="px-6 pb-20 max-w-6xl mx-auto">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {FEATURES.map((f) => {
            const Icon = f.icon
            return (
              <div
                key={f.title}
                className="rounded-xl p-5"
                style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
              >
                <div
                  className="flex items-center justify-center rounded-lg mb-3"
                  style={{ width: 36, height: 36, background: 'var(--color-surface-raise)', color: accent }}
                >
                  <Icon size={18} />
                </div>
                <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>{f.title}</div>
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)', lineHeight: 1.55 }}>{f.body}</div>
              </div>
            )
          })}
        </div>
      </section>

      {/* Bottom CTA */}
      <section className="px-6 pb-20 max-w-6xl mx-auto">
        <div
          className="rounded-2xl p-8 text-center"
          style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
        >
          <h2 style={{ fontSize: 24, fontWeight: 700, letterSpacing: '-0.01em' }}>Ready to see your own?</h2>
          <p className="mx-auto mt-2" style={{ maxWidth: 460, fontSize: 14, color: 'var(--color-text-muted)' }}>
            Sign in to connect your accounts and replace this preview with your real spending.
          </p>
          <Link
            to={ctaTo}
            className="mt-5 inline-flex items-center gap-2 rounded-lg px-5 py-2.5"
            style={{ background: 'var(--color-accent)', color: 'var(--color-accent-contrast)', fontSize: 14, fontWeight: 600 }}
          >
            {user ? 'Open dashboard' : 'Continue to sign in'} <ArrowRight size={16} />
          </Link>
        </div>
      </section>

      <footer className="px-6 pb-8 max-w-6xl mx-auto" style={{ fontSize: 11, color: 'var(--color-text-muted)', textAlign: 'center' }}>
        Spend · personal finance dashboard
      </footer>
    </div>
  )
}

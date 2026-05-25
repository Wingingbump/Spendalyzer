import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { useIsMobile } from '../hooks/useIsMobile'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  LayoutDashboard,
  List,
  BookOpen,
  Store,
  Tag,
  Settings,
  RefreshCw,
  ChevronDown,
  LayoutGrid,
  Bot,
  Target,
} from 'lucide-react'
import { insightsApi, syncApi } from '../lib/api'
import { useAuth } from '../context/AuthContext'
import { useFilters, RANGE_OPTIONS } from '../context/FilterContext'
import { formatDate } from '../lib/utils'

const NAV_ITEMS = [
  { to: '/overview', label: 'Overview', icon: LayoutDashboard },
  { to: '/transactions', label: 'Transactions', icon: List },
  { to: '/ledger', label: 'Ledger', icon: BookOpen },
  { to: '/merchants', label: 'Merchants', icon: Store },
  { to: '/categories', label: 'Categories', icon: Tag },
  { to: '/canvas', label: 'Canvas', icon: LayoutGrid },
  { to: '/advisor', label: 'Advisor', icon: Bot },
  { to: '/tracker', label: 'Tracker', icon: Target },
  { to: '/settings', label: 'Settings', icon: Settings },
]

export default function Sidebar() {
  const isMobile = useIsMobile()
  const { user, logout } = useAuth()
  const { range, institution, account, setRange, setInstitution, setAccount } = useFilters()
  const qc = useQueryClient()
  const [userMenuOpen, setUserMenuOpen] = useState(false)

  const { data: institutions = [] } = useQuery({
    queryKey: ['institutions'],
    queryFn: () => insightsApi.institutions(),
    staleTime: 60_000,
  })

  const { data: accounts = [] } = useQuery({
    queryKey: ['sidebar-accounts'],
    queryFn: () => insightsApi.accounts(),
    staleTime: 60_000,
  })

  const { data: lastSyncedData } = useQuery({
    queryKey: ['last-synced'],
    queryFn: async () => {
      const { settingsApi } = await import('../lib/api')
      return settingsApi.lastSynced()
    },
    staleTime: 30_000,
  })

  const { data: summary } = useQuery({
    queryKey: ['sidebar-summary', range, institution, account],
    queryFn: () =>
      insightsApi.summary({
        range,
        institution,
        account,
      }),
    staleTime: 30_000,
  })

  const syncMutation = useMutation({
    mutationFn: ({ fullSync }: { fullSync: boolean }) => syncApi.sync(fullSync),
    onSuccess: async () => {
      // Refetch every query (not just active ones) and keep isPending true
      // until the data is back so the spinner aligns with the visible refresh.
      await qc.invalidateQueries({ refetchType: 'all' })
    },
  })

  const filteredAccounts = institution === 'all'
    ? accounts
    : accounts.filter((a) => a.institution === institution)

  const rangeOptions = [...RANGE_OPTIONS]

  const isCustom = range.startsWith('custom:') || range === 'custom'
  const today = new Date().toISOString().slice(0, 10)
  const thirtyDaysAgo = new Date(Date.now() - 30 * 86400_000).toISOString().slice(0, 10)
  const customParts = range.startsWith('custom:') ? range.split(':') : []
  const customStart = customParts[1] ?? thirtyDaysAgo
  const customEnd = customParts[2] ?? today

  const handleRangeChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const val = e.target.value
    if (val === 'custom') {
      setRange(`custom:${thirtyDaysAgo}:${today}`)
    } else {
      setRange(val)
    }
  }

  if (isMobile) return null

  return (
    <aside
      className="fixed top-0 left-0 h-full flex flex-col overflow-y-auto z-10"
      style={{
        width: 220,
        background: 'var(--color-surface)',
        borderRight: '1px solid var(--color-border)',
      }}
    >
      {/* Logo */}
      <div className="px-5 py-5 flex-shrink-0">
        <span style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.03em', color: 'var(--color-text-primary)' }}>
          spend<span style={{ color: 'var(--color-accent)' }}>.</span>
        </span>
      </div>

      {/* Nav */}
      <nav className="px-2 flex-shrink-0">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 rounded-lg mb-0.5 text-sm transition-colors ${
                isActive
                  ? 'font-medium'
                  : 'hover:opacity-100'
              }`
            }
            style={({ isActive }) => ({
              background: isActive ? 'rgba(26, 86, 219, 0.1)' : 'transparent',
              color: isActive ? 'var(--color-accent-text)' : 'var(--color-text-secondary)',
              borderRight: isActive ? '2px solid var(--color-accent)' : '2px solid transparent',
            })}
          >
            <Icon size={15} strokeWidth={1.8} />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Divider */}
      <div className="mx-4 my-3 flex-shrink-0" style={{ height: 1, background: 'var(--color-border)' }} />

      {/* Filters */}
      <div className="px-4 flex-shrink-0 space-y-3">
        <div>
          <label className="block mb-1" style={{ fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Range
          </label>
          <select
            value={isCustom ? 'custom' : range}
            onChange={handleRangeChange}
            className="w-full"
            style={{ fontSize: 12 }}
          >
            {rangeOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>

          {isCustom && (
            <div className="mt-2 space-y-1.5">
              <div>
                <label style={{ fontSize: 10, color: 'var(--color-text-muted)', display: 'block', marginBottom: 3 }}>From</label>
                <input
                  type="date"
                  value={customStart}
                  max={customEnd}
                  onChange={(e) => setRange(`custom:${e.target.value}:${customEnd}`)}
                  className="w-full"
                  style={{ fontSize: 11, padding: '4px 8px' }}
                />
              </div>
              <div>
                <label style={{ fontSize: 10, color: 'var(--color-text-muted)', display: 'block', marginBottom: 3 }}>To</label>
                <input
                  type="date"
                  value={customEnd}
                  min={customStart}
                  max={today}
                  onChange={(e) => setRange(`custom:${customStart}:${e.target.value}`)}
                  className="w-full"
                  style={{ fontSize: 11, padding: '4px 8px' }}
                />
              </div>
            </div>
          )}
        </div>

        <div>
          <label className="block mb-1" style={{ fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Institution
          </label>
          <select
            value={institution}
            onChange={(e) => setInstitution(e.target.value)}
            className="w-full"
            style={{ fontSize: 12 }}
          >
            <option value="all">All institutions</option>
            {institutions.map((inst) => (
              <option key={inst} value={inst}>
                {inst}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block mb-1" style={{ fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            Account
          </label>
          <select
            value={account}
            onChange={(e) => setAccount(e.target.value)}
            className="w-full"
            style={{ fontSize: 12 }}
          >
            <option value="all">All accounts</option>
            {filteredAccounts.map((acc) => (
              <option key={acc.plaid_account_id} value={acc.plaid_account_id}>
                {acc.name} {acc.mask ? `••${acc.mask}` : ''}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Divider */}
      <div className="mx-4 my-3 flex-shrink-0" style={{ height: 1, background: 'var(--color-border)' }} />

      {/* Sync section */}
      <div className="px-4 flex-shrink-0">
        {lastSyncedData?.last_synced_at && (
          <p style={{ fontSize: 11, color: 'var(--color-text-muted)' }} className="mb-2">
            Synced {formatDate(lastSyncedData.last_synced_at)}
          </p>
        )}

        <button
          onClick={() => syncMutation.mutate({ fullSync: false })}
          disabled={syncMutation.isPending}
          className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-sm font-medium transition-opacity disabled:opacity-50"
          style={{
            background: 'var(--color-accent)',
            color: '#fff',
            fontSize: 12,
          }}
        >
          <RefreshCw
            size={13}
            className={syncMutation.isPending && !syncMutation.variables?.fullSync ? 'spinner' : ''}
          />
          {syncMutation.isPending && !syncMutation.variables?.fullSync ? 'Syncing…' : 'Sync Now'}
        </button>
        <button
          onClick={() => syncMutation.mutate({ fullSync: true })}
          disabled={syncMutation.isPending}
          title="Re-pull full transaction history from Plaid (up to 24 months). Use this if transactions are missing."
          className="flex items-center gap-1.5 w-full px-3 py-1.5 rounded-lg transition-opacity disabled:opacity-50"
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-muted)', fontSize: 11, marginTop: 4 }}
        >
          <RefreshCw
            size={11}
            className={syncMutation.isPending && syncMutation.variables?.fullSync ? 'spinner' : ''}
          />
          {syncMutation.isPending && syncMutation.variables?.fullSync ? 'Full sync…' : 'Full history sync'}
        </button>
        {syncMutation.isSuccess && (
          <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-positive)' }}>
            +{syncMutation.data?.synced_count ?? 0} transactions
          </p>
        )}

        {syncMutation.isError && (
          <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>
            Sync failed
          </p>
        )}
      </div>

      {/* Dedup summary */}
      {summary && (
        <div className="px-4 mt-2 flex-shrink-0">
          <p style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
            {summary.transaction_count} txns
          </p>
        </div>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* User section */}
      <div
        className="px-4 py-4 flex-shrink-0"
        style={{ borderTop: '1px solid var(--color-border)' }}
      >
        <button
          className="flex items-center gap-2 w-full"
          onClick={() => setUserMenuOpen((o) => !o)}
        >
          <div
            className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0"
            style={{ background: 'var(--color-accent)', color: '#fff' }}
          >
            {user?.username?.[0]?.toUpperCase() ?? 'U'}
          </div>
          <span className="text-sm truncate flex-1 text-left" style={{ color: 'var(--color-text-secondary)' }}>
            {user?.username}
          </span>
          <ChevronDown size={13} style={{ color: 'var(--color-text-muted)' }} />
        </button>

        {userMenuOpen && (
          <div
            className="mt-2 rounded-lg overflow-hidden"
            style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)' }}
          >
            <button
              onClick={logout}
              className="w-full px-3 py-2 text-left text-sm transition-colors hover:opacity-80"
              style={{ color: 'var(--color-negative)', fontSize: 12 }}
            >
              Sign out
            </button>
          </div>
        )}
      </div>
    </aside>
  )
}

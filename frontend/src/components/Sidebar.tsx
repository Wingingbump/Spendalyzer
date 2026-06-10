import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { useIsMobile } from '../hooks/useIsMobile'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  LayoutDashboard,
  List,
  PieChart,
  Settings,
  RefreshCw,
  ChevronDown,
  LayoutGrid,
  Bot,
  Target,
} from 'lucide-react'
import { insightsApi, syncApi } from '../lib/api'
import { useAuth } from '../context/AuthContext'
import { useFilters } from '../context/FilterContext'
import { formatDate } from '../lib/utils'

const NAV_ITEMS = [
  { to: '/overview', label: 'Overview', icon: LayoutDashboard },
  { to: '/transactions', label: 'Transactions', icon: List },
  { to: '/tracker', label: 'Tracker', icon: Target },
  { to: '/advisor', label: 'Advisor', icon: Bot },
  { to: '/insights', label: 'Insights', icon: PieChart },
  { to: '/canvas', label: 'Canvas', icon: LayoutGrid },
  { to: '/settings', label: 'Settings', icon: Settings },
]

export default function Sidebar() {
  const isMobile = useIsMobile()
  const { user, logout } = useAuth()
  const { institution, account, setInstitution, setAccount } = useFilters()
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
    queryKey: ['sidebar-summary', institution, account],
    queryFn: () =>
      insightsApi.summary({
        range: 'all',
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
              background: isActive ? 'var(--color-accent-soft)' : 'transparent',
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
        <div className="flex items-center gap-2 mb-2">
          {lastSyncedData?.last_synced_at && (
            <p style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
              Synced {formatDate(lastSyncedData.last_synced_at)}
            </p>
          )}
          <button
            onClick={() => syncMutation.mutate({ fullSync: false })}
            disabled={syncMutation.isPending}
            title="Sync now"
            className="flex items-center justify-center p-0.5 rounded transition-opacity disabled:opacity-50"
            style={{
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--color-text-muted)',
              flex: 'none',
            }}
          >
            <RefreshCw
              size={13}
              className={syncMutation.isPending ? 'spinner' : ''}
            />
          </button>
        </div>
        {syncMutation.isSuccess && (
          <p style={{ fontSize: 11, color: 'var(--color-positive)' }}>
            +{syncMutation.data?.synced_count ?? 0} transactions
          </p>
        )}

        {syncMutation.isError && (
          <p style={{ fontSize: 11, color: 'var(--color-negative)' }}>
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
            style={{ background: 'var(--color-accent)', color: 'var(--color-accent-contrast)' }}
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

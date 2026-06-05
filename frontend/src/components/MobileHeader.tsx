import { useState } from 'react'
import { SlidersHorizontal, X } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useFilters } from '../context/FilterContext'
import { insightsApi } from '../lib/api'

export default function MobileHeader() {
  const [filtersOpen, setFiltersOpen] = useState(false)
  const { institution, account, setInstitution, setAccount } = useFilters()

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

  const filteredAccounts = institution === 'all'
    ? accounts
    : accounts.filter((a) => a.institution === institution)

  const hasActiveFilters = institution !== 'all' || account !== 'all'

  return (
    <>
      <header
        className="flex items-center justify-between px-4 flex-shrink-0"
        style={{
          height: 52,
          background: 'var(--color-surface)',
          borderBottom: '1px solid var(--color-border)',
          position: 'sticky',
          top: 0,
          zIndex: 30,
        }}
      >
        <span style={{ fontSize: 20, fontWeight: 700, letterSpacing: '-0.03em', color: 'var(--color-text-primary)' }}>
          spend<span style={{ color: 'var(--color-accent)' }}>.</span>
        </span>

        <button
          onClick={() => setFiltersOpen(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg"
          style={{
            background: hasActiveFilters ? 'rgba(26, 86, 219, 0.1)' : 'var(--color-surface-raise)',
            border: `1px solid ${hasActiveFilters ? 'var(--color-accent)' : 'var(--color-border)'}`,
            color: hasActiveFilters ? 'var(--color-accent-text)' : 'var(--color-text-secondary)',
            fontSize: 12,
            fontWeight: 500,
          }}
        >
          <SlidersHorizontal size={13} />
          {hasActiveFilters ? 'Filters · on' : 'Filters'}
        </button>
      </header>

      {filtersOpen && (
        <div
          className="fixed inset-0"
          style={{ zIndex: 100 }}
          onClick={() => setFiltersOpen(false)}
        >
          <div
            className="absolute bottom-0 left-0 right-0 rounded-t-2xl"
            style={{
              background: 'var(--color-surface)',
              borderTop: '1px solid var(--color-border)',
              padding: '20px 20px 32px',
              maxHeight: '80vh',
              overflowY: 'auto',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-5">
              <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--color-text-primary)' }}>Filters</span>
              <button onClick={() => setFiltersOpen(false)}>
                <X size={18} style={{ color: 'var(--color-text-muted)' }} />
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label style={{ fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', display: 'block', marginBottom: 6 }}>
                  Institution
                </label>
                <select value={institution} onChange={(e) => setInstitution(e.target.value)} className="w-full" style={{ fontSize: 14 }}>
                  <option value="all">All institutions</option>
                  {institutions.map((inst) => (
                    <option key={inst} value={inst}>{inst}</option>
                  ))}
                </select>
              </div>

              <div>
                <label style={{ fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', display: 'block', marginBottom: 6 }}>
                  Account
                </label>
                <select value={account} onChange={(e) => setAccount(e.target.value)} className="w-full" style={{ fontSize: 14 }}>
                  <option value="all">All accounts</option>
                  {filteredAccounts.map((acc) => (
                    <option key={acc.plaid_account_id} value={acc.plaid_account_id}>
                      {acc.name}{acc.mask ? ` ••${acc.mask}` : ''}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <button
              onClick={() => setFiltersOpen(false)}
              className="w-full mt-6 py-3 rounded-xl font-semibold"
              style={{ background: 'var(--color-accent)', color: '#fff', fontSize: 14 }}
            >
              Done
            </button>
          </div>
        </div>
      )}
    </>
  )
}

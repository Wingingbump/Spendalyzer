import React, { createContext, useContext, useState } from 'react'

export const RANGE_OPTIONS = [
  { value: '30d', label: 'Last 30 days' },
  { value: '60d', label: 'Last 60 days' },
  { value: '90d', label: 'Last 90 days' },
  { value: '6m', label: 'Last 6 months' },
  { value: 'ytd', label: 'Year to date' },
  { value: 'all', label: 'All time' },
  { value: 'custom', label: 'Custom range…' },
]

interface FilterContextValue {
  range: string
  institution: string
  account: string
  setRange: (v: string) => void
  setInstitution: (v: string) => void
  setAccount: (v: string) => void
}

const FilterContext = createContext<FilterContextValue>({
  // Range is no longer a global UI control — each view owns its own range.
  // This stays as an "all time" default for the few non-page consumers
  // (Canvas widget fallback, the side panel's recent list).
  range: 'all',
  institution: 'all',
  account: 'all',
  setRange: () => {},
  setInstitution: () => {},
  setAccount: () => {},
})

export function FilterProvider({ children }: { children: React.ReactNode }) {
  const [range, setRange] = useState('all')
  const [institution, setInstitution] = useState('all')
  const [account, setAccount] = useState('all')

  const handleSetInstitution = (v: string) => {
    setInstitution(v)
    setAccount('all') // reset account when institution changes
  }

  return (
    <FilterContext.Provider
      value={{
        range,
        institution,
        account,
        setRange,
        setInstitution: handleSetInstitution,
        setAccount,
      }}
    >
      {children}
    </FilterContext.Provider>
  )
}

export function useFilters(): FilterContextValue {
  return useContext(FilterContext)
}

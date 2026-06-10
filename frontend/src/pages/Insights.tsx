import React from 'react'
import { useSearchParams } from 'react-router-dom'
import Categories from './Categories'
import Merchants from './Merchants'

export default function Insights() {
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = searchParams.get('tab') === 'merchants' ? 'merchants' : 'categories'

  const handleTabChange = (newTab: 'categories' | 'merchants') => {
    setSearchParams({ tab: newTab }, { replace: true })
  }

  const tabStyle = (active: boolean): React.CSSProperties => ({
    fontSize: 13,
    padding: '6px 14px',
    borderRadius: 6,
    border: active ? '1px solid var(--color-border)' : '1px solid transparent',
    background: active ? 'var(--color-surface-raise)' : 'transparent',
    color: active ? 'var(--color-text-primary)' : 'var(--color-text-muted)',
    cursor: 'pointer',
    fontWeight: active ? 500 : 400,
    transition: 'all 0.15s',
  })

  return (
    <div className="space-y-5 fade-in">
      {/* Page header */}
      <div>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: 'var(--color-text-primary)' }}>Insights</h1>
        <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 2 }}>
          Spending by category and merchant
        </p>
      </div>

      {/* Segmented tab control */}
      <div
        style={{
          display: 'inline-flex',
          background: 'var(--color-surface)',
          border: '1px solid var(--color-border)',
          borderRadius: 8,
          padding: 3,
          gap: 2,
        }}
      >
        <button
          onClick={() => handleTabChange('categories')}
          style={tabStyle(tab === 'categories')}
        >
          Categories
        </button>
        <button
          onClick={() => handleTabChange('merchants')}
          style={tabStyle(tab === 'merchants')}
        >
          Merchants
        </button>
      </div>

      {/* Tab content */}
      {tab === 'categories' ? (
        <Categories embedded />
      ) : (
        <Merchants embedded />
      )}
    </div>
  )
}

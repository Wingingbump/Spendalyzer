import Card from './Card'
import Spinner from './Spinner'

interface Tab {
  key: string
  label: string
}

interface MetricCardProps {
  label: string
  value: string
  sub?: string
  subPositive?: boolean
  isLoading?: boolean
  hero?: boolean
  tabs?: Tab[]
  activeTab?: string
  onTabChange?: (key: string) => void
}

function HeroTabs({ tabs, activeTab, onTabChange }: { tabs: Tab[]; activeTab?: string; onTabChange?: (key: string) => void }) {
  return (
    <div className="inline-flex rounded-md p-0.5 mb-2" style={{ background: 'rgba(255,255,255,0.12)' }}>
      {tabs.map((tab) => {
        const isActive = tab.key === activeTab
        return (
          <button
            key={tab.key}
            onClick={() => onTabChange?.(tab.key)}
            className="px-2.5 py-1 rounded transition-colors"
            style={{
              background: isActive ? 'rgba(255,255,255,0.95)' : 'transparent',
              color: isActive ? 'var(--color-accent)' : 'rgba(255,255,255,0.7)',
              fontSize: 10,
              fontWeight: 700,
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
              cursor: 'pointer',
              border: 'none',
            }}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}

function PlainTabs({ tabs, activeTab, onTabChange }: { tabs: Tab[]; activeTab?: string; onTabChange?: (key: string) => void }) {
  return (
    <div className="inline-flex rounded-md p-0.5 mb-2" style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)' }}>
      {tabs.map((tab) => {
        const isActive = tab.key === activeTab
        return (
          <button
            key={tab.key}
            onClick={() => onTabChange?.(tab.key)}
            className="px-2.5 py-1 rounded transition-colors"
            style={{
              background: isActive ? 'var(--color-accent)' : 'transparent',
              color: isActive ? 'var(--color-accent-contrast)' : 'var(--color-text-muted)',
              fontSize: 10,
              fontWeight: 700,
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
              cursor: 'pointer',
              border: 'none',
            }}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}

export default function MetricCard({ label, value, sub, subPositive, isLoading, hero, tabs, activeTab, onTabChange }: MetricCardProps) {
  if (hero) {
    return (
      <div
        className="rounded-xl p-4 relative overflow-hidden"
        style={{ background: 'var(--color-accent)' }}
      >
        {/* subtle glare */}
        <div style={{ position: 'absolute', right: -16, top: -16, width: 120, height: 120, borderRadius: '50%', background: 'rgba(255,255,255,0.06)', filter: 'blur(20px)', pointerEvents: 'none' }} />
        {tabs && tabs.length > 0 ? (
          <HeroTabs tabs={tabs} activeTab={activeTab} onTabChange={onTabChange} />
        ) : (
          <p style={{ fontSize: 11, color: 'rgba(255,255,255,0.65)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>
            {label}
          </p>
        )}
        {isLoading ? (
          <Spinner size={20} />
        ) : (
          <>
            <p style={{ fontSize: 26, fontWeight: 700, fontFamily: 'ui-monospace, monospace', color: 'var(--color-accent-contrast)', lineHeight: 1.1 }}>
              {value}
            </p>
            {sub && (
              <div className="mt-2 inline-flex items-center gap-1 px-2 py-0.5 rounded" style={{ background: 'rgba(255,255,255,0.15)', fontSize: 11, color: 'var(--color-accent-contrast)', fontWeight: 600 }}>
                {sub}
              </div>
            )}
          </>
        )}
      </div>
    )
  }

  return (
    <Card>
      {tabs && tabs.length > 0 ? (
        <PlainTabs tabs={tabs} activeTab={activeTab} onTabChange={onTabChange} />
      ) : (
        <p style={{ fontSize: 11, color: 'var(--color-text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>
          {label}
        </p>
      )}
      {isLoading ? (
        <Spinner size={20} />
      ) : (
        <>
          <p style={{ fontSize: 26, fontWeight: 700, fontFamily: 'ui-monospace, monospace', color: 'var(--color-text-primary)', lineHeight: 1.1 }}>
            {value}
          </p>
          {sub && (
            <p
              className="mt-1"
              style={{
                fontSize: 12,
                color: subPositive === undefined
                  ? 'var(--color-text-muted)'
                  : subPositive
                    ? 'var(--color-positive)'
                    : 'var(--color-negative)',
              }}
            >
              {sub}
            </p>
          )}
        </>
      )}
    </Card>
  )
}

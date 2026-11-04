import { useNavigate } from 'react-router-dom'
import { FlaskConical } from 'lucide-react'
import { useAuth } from '../context/AuthContext'

/**
 * Standing notice for throwaway demo accounts.
 *
 * Two jobs: stop anyone mistaking the sample year for their own money, and
 * give them the way out. Deliberately persistent rather than dismissible —
 * someone who forgets they're in the demo and starts setting real budgets has
 * wasted their time, and the account disappears in 24 hours regardless.
 */
export default function DemoBanner() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  if (!user?.is_demo) return null

  return (
    <div
      className="flex items-center gap-2 flex-wrap rounded-lg px-3 py-2"
      style={{
        background: 'var(--color-accent-soft)',
        border: '1px solid color-mix(in srgb, var(--color-accent) 30%, transparent)',
        // Layout drops this straight above the page heading with no gap of its
        // own, so the spacing lives here rather than in every page.
        marginBottom: 16,
      }}
    >
      <FlaskConical size={14} style={{ color: 'var(--color-accent)', flexShrink: 0 }} />
      <p style={{ fontSize: 12, color: 'var(--color-text-secondary)', flex: '1 1 260px' }}>
        <strong style={{ color: 'var(--color-text-primary)' }}>Demo account.</strong>{' '}
        A year of sample transactions — edit anything you like. It isn't linked to a
        real bank and is deleted after 24 hours.
      </p>
      <button
        onClick={async () => { await logout(); navigate('/login?signup=1') }}
        style={{
          flexShrink: 0, fontSize: 12, fontWeight: 600, padding: '5px 11px',
          borderRadius: 7, cursor: 'pointer', border: 'none',
          background: 'var(--color-accent)', color: 'var(--color-accent-contrast)',
        }}
      >
        Create a real account
      </button>
    </div>
  )
}

import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard, List, Target, Bot, MoreHorizontal,
  Store, Tag, LayoutGrid, Settings, X,
} from 'lucide-react'

const PRIMARY_NAV = [
  { to: '/overview',      label: 'Overview',      icon: LayoutDashboard },
  { to: '/transactions',  label: 'Transactions',  icon: List },
  { to: '/tracker',       label: 'Tracker',       icon: Target },
  { to: '/advisor',       label: 'Advisor',       icon: Bot },
]

const MORE_NAV = [
  { to: '/merchants',   label: 'Merchants',   icon: Store },
  { to: '/categories',  label: 'Categories',  icon: Tag },
  { to: '/canvas',      label: 'Canvas',      icon: LayoutGrid },
  { to: '/settings',    label: 'Settings',    icon: Settings },
]

export default function BottomNav() {
  const [moreOpen, setMoreOpen] = useState(false)

  return (
    <>
      {moreOpen && (
        <div
          className="fixed inset-0"
          style={{ zIndex: 90 }}
          onClick={() => setMoreOpen(false)}
        >
          <div
            className="absolute bottom-[60px] left-0 right-0 rounded-t-2xl"
            style={{
              background: 'var(--color-surface)',
              borderTop: '1px solid var(--color-border)',
              padding: '16px 8px',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 mb-3">
              <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-muted)' }}>More</span>
              <button onClick={() => setMoreOpen(false)}>
                <X size={16} style={{ color: 'var(--color-text-muted)' }} />
              </button>
            </div>
            <div className="grid grid-cols-4 gap-2 px-2">
              {MORE_NAV.map(({ to, label, icon: Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  onClick={() => setMoreOpen(false)}
                  className="flex flex-col items-center gap-1.5 py-3 rounded-xl"
                  style={({ isActive }) => ({
                    background: isActive ? 'rgba(26, 86, 219, 0.1)' : 'var(--color-surface-raise)',
                    color: isActive ? 'var(--color-accent-text)' : 'var(--color-text-secondary)',
                  })}
                >
                  <Icon size={20} strokeWidth={1.6} />
                  <span style={{ fontSize: 10, fontWeight: 500 }}>{label}</span>
                </NavLink>
              ))}
            </div>
          </div>
        </div>
      )}

      <nav
        className="fixed bottom-0 left-0 right-0 flex items-center"
        style={{
          height: 60,
          background: 'var(--color-surface)',
          borderTop: '1px solid var(--color-border)',
          zIndex: 80,
          paddingBottom: 'env(safe-area-inset-bottom)',
        }}
      >
        {PRIMARY_NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className="flex flex-col items-center justify-center gap-1 flex-1 h-full"
            style={({ isActive }) => ({
              color: isActive ? 'var(--color-accent-text)' : 'var(--color-text-muted)',
            })}
          >
            <Icon size={20} strokeWidth={1.6} />
            <span style={{ fontSize: 10, fontWeight: 500 }}>{label}</span>
          </NavLink>
        ))}

        <button
          onClick={() => setMoreOpen((o) => !o)}
          className="flex flex-col items-center justify-center gap-1 flex-1 h-full"
          style={{ color: moreOpen ? 'var(--color-accent-text)' : 'var(--color-text-muted)' }}
        >
          <MoreHorizontal size={20} strokeWidth={1.6} />
          <span style={{ fontSize: 10, fontWeight: 500 }}>More</span>
        </button>
      </nav>
    </>
  )
}

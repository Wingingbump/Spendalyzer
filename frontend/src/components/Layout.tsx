import React, { useState } from 'react'
import { useLocation } from 'react-router-dom'
import Sidebar from './Sidebar'
import RightPanel from './RightPanel'
import BottomNav from './BottomNav'
import MobileHeader from './MobileHeader'
import { PANEL_WIDTH } from './RightPanel'
import { PanelContext } from '../context/PanelContext'
import type { TabFocusRequest } from '../context/PanelContext'
import { useAuth } from '../context/AuthContext'
import { useIdleTimeout } from '../hooks/useIdleTimeout'
import { useIsMobile } from '../hooks/useIsMobile'

const PANEL_HIDDEN_ROUTES = ['/settings', '/login']

interface LayoutProps {
  children: React.ReactNode
}

export default function Layout({ children }: LayoutProps) {
  const { logout } = useAuth()
  const { showWarning, secondsLeft, stayActive } = useIdleTimeout(logout)
  const location = useLocation()
  const isMobile = useIsMobile()
  const [panelOpen, setPanelOpen] = useState(() => {
    try { return localStorage.getItem('rhs-panel-open') !== 'false' } catch { return true }
  })
  const [requestedTab, setRequestedTab] = useState<TabFocusRequest | null>(null)
  const effectivePanelOpen = !isMobile && panelOpen && !PANEL_HIDDEN_ROUTES.includes(location.pathname)

  const handleToggle = () => {
    setPanelOpen((o) => {
      const next = !o
      try { localStorage.setItem('rhs-panel-open', String(next)) } catch {}
      return next
    })
  }

  const focusTab = (tab: string) => {
    // Nonce ensures repeated requests for the same tab still fire the effect.
    setRequestedTab({ tab, nonce: Date.now() })
    if (!panelOpen) {
      setPanelOpen(true)
      try { localStorage.setItem('rhs-panel-open', 'true') } catch {}
    }
  }

  return (
    <PanelContext.Provider value={{ panelOpen: effectivePanelOpen, focusTab, requestedTab }}>
    <div className="flex min-h-screen" style={{ background: 'var(--color-bg)' }}>
      {/* Blurred content layer — blur covers sidebar + main + right panel */}
      <div
        className="flex flex-1 min-h-screen"
        style={{
          filter: showWarning ? 'blur(6px)' : 'none',
          transition: 'filter 0.3s ease',
          pointerEvents: showWarning ? 'none' : 'auto',
        }}
      >
        <Sidebar />
        <main
          className="flex-1 overflow-auto"
          style={{
            marginLeft: isMobile ? 0 : 220,
            marginRight: effectivePanelOpen ? PANEL_WIDTH : 0,
            minHeight: '100vh',
            transition: 'margin-right 0.25s ease',
            paddingBottom: isMobile ? 60 : 0,
          }}
        >
          {isMobile && <MobileHeader />}
          <div className={isMobile ? 'p-4' : 'p-6'}>
            {children}
          </div>
        </main>
        {!isMobile && <RightPanel isOpen={effectivePanelOpen} onToggle={handleToggle} />}
      </div>
      {isMobile && <BottomNav />}

      {showWarning && (
        <div
          className="fixed inset-0 flex items-center justify-center"
          style={{ zIndex: 200, background: 'rgba(0,0,0,0.4)', pointerEvents: 'auto' }}
        >
          <div
            className="rounded-2xl p-8 flex flex-col items-center"
            style={{
              background: 'var(--color-surface)',
              border: '1px solid var(--color-border)',
              width: 340,
              textAlign: 'center',
            }}
          >
            {/* Countdown ring */}
            <div
              className="flex items-center justify-center rounded-full mb-5"
              style={{
                width: 72,
                height: 72,
                background: 'var(--color-surface-raise)',
                border: `3px solid ${secondsLeft <= 15 ? 'var(--color-negative)' : 'var(--color-border)'}`,
                transition: 'border-color 0.3s',
              }}
            >
              <span
                style={{
                  fontSize: 28,
                  fontWeight: 700,
                  fontFamily: 'monospace',
                  color: secondsLeft <= 15 ? 'var(--color-negative)' : 'var(--color-text-primary)',
                  transition: 'color 0.3s',
                }}
              >
                {secondsLeft}
              </span>
            </div>

            <h2 style={{ fontSize: 16, fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 8 }}>
              Still there?
            </h2>
            <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 24, lineHeight: 1.5 }}>
              You've been inactive for a while. We'll sign you out in{' '}
              <span style={{ color: 'var(--color-text-secondary)', fontWeight: 500 }}>
                {secondsLeft} second{secondsLeft !== 1 ? 's' : ''}
              </span>{' '}
              to keep your data safe.
            </p>

            <div className="flex gap-3 w-full">
              <button
                onClick={logout}
                className="flex-1 py-2 rounded-lg font-medium"
                style={{
                  background: 'var(--color-surface-raise)',
                  border: '1px solid var(--color-border)',
                  color: 'var(--color-text-muted)',
                  fontSize: 13,
                  cursor: 'pointer',
                }}
              >
                Sign out
              </button>
              <button
                onClick={stayActive}
                className="flex-1 py-2 rounded-lg font-semibold"
                style={{
                  background: 'var(--color-accent)',
                  color: '#fff',
                  fontSize: 13,
                  cursor: 'pointer',
                  border: 'none',
                }}
              >
                I'm still here
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
    </PanelContext.Provider>
  )

}

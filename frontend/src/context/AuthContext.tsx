import React, { createContext, useContext, useEffect, useState } from 'react'
import { api, authApi, type User } from '../lib/api'

interface AuthContextValue {
  user: User | null
  isLoading: boolean
  logout: () => Promise<void>
  setUser: (user: User | null) => void
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  isLoading: true,
  logout: async () => {},
  setUser: () => {},
})

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    const initAuth = async () => {
      try {
        const u = await authApi.me()
        if (!cancelled) setUser(u)
      } catch {
        // Access token may be missing or expired — try refreshing once.
        if (cancelled) return
        try {
          await api.post('/auth/refresh')
          if (cancelled) return
          const u = await authApi.me()
          if (!cancelled) setUser(u)
        } catch {
          if (!cancelled) setUser(null)
        }
      } finally {
        if (!cancelled) setIsLoading(false)
      }
    }

    initAuth()
    return () => { cancelled = true }
  }, [])

  // Listen for the soft logout signal dispatched by the API interceptor when a
  // token refresh fails mid-session. Setting user to null lets React Router
  // redirect to /login without a hard page reload (which would restart the loop).
  useEffect(() => {
    const handleAuthLogout = () => setUser(null)
    window.addEventListener('auth:logout', handleAuthLogout)
    return () => window.removeEventListener('auth:logout', handleAuthLogout)
  }, [])

  const logout = async () => {
    try {
      await authApi.logout()
    } catch {
      // ignore
    } finally {
      setUser(null)
      window.location.href = '/login'
    }
  }

  return (
    <AuthContext.Provider value={{ user, isLoading, logout, setUser }}>
      {children}
    </AuthContext.Provider>
  )
}

/**
 * Whether the LLM-backed advisor is switched on server-side.
 *
 * Defaults to false while the user is still loading or when the server omits
 * the flag, so the advisor UI never flashes in before we know it's available.
 */
export function useAdvisorEnabled(): boolean {
  const { user } = useAuth()
  return user?.features?.advisor === true
}

export function useAuth(): AuthContextValue {
  return useContext(AuthContext)
}

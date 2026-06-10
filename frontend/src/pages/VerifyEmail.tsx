import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { authApi } from '../lib/api'
import { useAuth } from '../context/AuthContext'

export default function VerifyEmail() {
  const [searchParams] = useSearchParams()
  const [status, setStatus] = useState<'verifying' | 'success' | 'error'>('verifying')
  const [error, setError] = useState('')
  const navigate = useNavigate()
  const { setUser } = useAuth()

  useEffect(() => {
    const token = searchParams.get('token')
    if (!token) {
      setStatus('error')
      setError('No verification token found in the link.')
      return
    }
    authApi.verifyEmail(token)
      .then((user) => {
        setUser(user)
        setStatus('success')
        setTimeout(() => navigate('/overview', { replace: true }), 2000)
      })
      .catch((err) => {
        setStatus('error')
        const e = err as { response?: { data?: { detail?: string } } }
        setError(e.response?.data?.detail || 'This link is invalid or has expired.')
      })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--color-bg)' }}>
      <div className="rounded-2xl p-8 text-center" style={{ maxWidth: 380, background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}>
        <div className="mb-6">
          <span style={{ fontSize: 28, fontWeight: 700, letterSpacing: '-0.03em', color: 'var(--color-text-primary)' }}>
            spend<span style={{ color: 'var(--color-accent)' }}>.</span>
          </span>
        </div>

        {status === 'verifying' && (
          <>
            <div className="w-8 h-8 rounded-full border-2 spinner mx-auto mb-4"
              style={{ borderColor: 'var(--color-border)', borderTopColor: 'var(--color-accent)' }} />
            <p style={{ fontSize: 14, color: 'var(--color-text-muted)' }}>Verifying your email…</p>
          </>
        )}

        {status === 'success' && (
          <>
            <div className="text-4xl mb-4">✅</div>
            <h2 style={{ fontSize: 16, fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 8 }}>Email confirmed!</h2>
            <p style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Taking you to your dashboard…</p>
          </>
        )}

        {status === 'error' && (
          <>
            <div className="text-4xl mb-4">❌</div>
            <h2 style={{ fontSize: 16, fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 8 }}>Verification failed</h2>
            <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 20 }}>{error}</p>
            <button
              onClick={() => navigate('/login')}
              className="w-full py-2.5 rounded-lg font-semibold"
              style={{ background: 'var(--color-accent)', color: 'var(--color-accent-contrast)', fontSize: 14 }}
            >
              Back to sign in
            </button>
          </>
        )}
      </div>
    </div>
  )
}

import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { authApi } from '../lib/api'
import { useAuth } from '../context/AuthContext'
import PasswordInput from '../components/PasswordInput'

const loginSchema = z.object({
  username: z.string().min(1, 'Username is required'),
  password: z.string().min(1, 'Password is required'),
})

const registerSchema = z.object({
  first_name: z.string().min(1, 'First name is required'),
  last_name: z.string().min(1, 'Last name is required'),
  email: z.string().email('Enter a valid email'),
  phone: z.string().min(7, 'Enter a valid phone number'),
  username: z.string().min(3, 'Username must be at least 3 characters'),
  password: z.string().min(8, 'Password must be at least 8 characters'),
})

const forgotSchema = z.object({
  email: z.string().email('Enter a valid email'),
})

type LoginData = z.infer<typeof loginSchema>
type RegisterData = z.infer<typeof registerSchema>
type ForgotData = z.infer<typeof forgotSchema>

export default function Login() {
  const [tab, setTab] = useState<'login' | 'register'>('login')
  const [showForgot, setShowForgot] = useState(false)
  const [serverError, setServerError] = useState('')
  const [successMsg, setSuccessMsg] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [registered, setRegistered] = useState(false)
  const [showResend, setShowResend] = useState(false)
  const [resendEmail, setResendEmail] = useState('')
  const [resendMsg, setResendMsg] = useState('')
  const [resendLoading, setResendLoading] = useState(false)
  const navigate = useNavigate()
  const { setUser } = useAuth()

  const loginForm = useForm<LoginData>({ resolver: zodResolver(loginSchema) })
  const registerForm = useForm<RegisterData>({ resolver: zodResolver(registerSchema) })
  const forgotForm = useForm<ForgotData>({ resolver: zodResolver(forgotSchema) })

  const onLogin = async (data: LoginData) => {
    setServerError('')
    setIsSubmitting(true)
    try {
      const user = await authApi.login(data.username, data.password)
      setUser(user)
      navigate('/overview', { replace: true })
    } catch (err: unknown) {
      const e = err as { response?: { status?: number; data?: { detail?: string } } }
      const detail = e.response?.data?.detail || 'Invalid username or password'
      setServerError(detail)
      if (e.response?.status === 403 && detail.toLowerCase().includes('verify')) {
        setShowResend(true)
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  const onResend = async () => {
    if (!resendEmail) return
    setResendLoading(true)
    try {
      await authApi.resendVerification(resendEmail)
      setResendMsg('Confirmation link sent — check your email.')
    } catch {
      setResendMsg('If that email exists and is unverified, a new link has been sent.')
    } finally {
      setResendLoading(false)
    }
  }

  const onRegister = async (data: RegisterData) => {
    setServerError('')
    setIsSubmitting(true)
    try {
      await authApi.register(data.username, data.password, data.first_name, data.last_name, data.email, data.phone)
      setRegistered(true)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } }
      setServerError(e.response?.data?.detail || 'Registration failed')
    } finally {
      setIsSubmitting(false)
    }
  }

  const onForgot = async (data: ForgotData) => {
    setServerError('')
    setIsSubmitting(true)
    try {
      await authApi.forgotPassword(data.email)
      setSuccessMsg('If that email is registered, a reset link has been sent.')
      forgotForm.reset()
    } catch {
      setSuccessMsg('If that email is registered, a reset link has been sent.')
    } finally {
      setIsSubmitting(false)
    }
  }

  const closeForgot = () => {
    setShowForgot(false)
    setServerError('')
    setSuccessMsg('')
    forgotForm.reset()
  }

  const switchTab = (t: 'login' | 'register') => {
    setTab(t)
    setServerError('')
    setRegistered(false)
    setShowResend(false)
    setResendEmail('')
    setResendMsg('')
    loginForm.reset()
    registerForm.reset()
  }

  return (
    <div className="min-h-screen flex items-start justify-center" style={{ background: 'var(--color-bg)', paddingTop: '10vh' }}>
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-8">
          <Link to="/welcome" aria-label="Back to home" style={{ textDecoration: 'none', display: 'inline-block' }}>
            <span style={{ fontSize: 36, fontWeight: 700, letterSpacing: '-0.03em', color: 'var(--color-text-primary)' }}>
              spend<span style={{ color: 'var(--color-accent)' }}>.</span>
            </span>
          </Link>
          <p className="mt-2" style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
            personal finance, your way
          </p>
        </div>

        {/* Card */}
        <div className="rounded-2xl p-6" style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}>
          {/* Tabs */}
          <div className="flex rounded-lg p-1 mb-6" style={{ background: 'var(--color-surface-raise)' }}>
            {(['login', 'register'] as const).map((t) => (
              <button
                key={t}
                onClick={() => switchTab(t)}
                className="flex-1 py-2 rounded-md text-sm font-medium transition-all"
                style={{
                  background: tab === t ? 'var(--color-surface)' : 'transparent',
                  color: tab === t ? 'var(--color-text-primary)' : 'var(--color-text-muted)',
                  border: tab === t ? '1px solid var(--color-border)' : '1px solid transparent',
                  fontSize: 13,
                }}
              >
                {t === 'login' ? 'Sign in' : 'Register'}
              </button>
            ))}
          </div>

          {/* ── Login form ── */}
          {tab === 'login' && (
            <form onSubmit={loginForm.handleSubmit(onLogin)} className="space-y-4">
              <div>
                <label className="block mb-1.5" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>Username or email</label>
                <input {...loginForm.register('username')} type="text" placeholder="Username or email" autoComplete="username" className="w-full" style={{ fontSize: 14 }} />
                {loginForm.formState.errors.username && <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>{loginForm.formState.errors.username.message}</p>}
              </div>
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>Password</label>
                  <button type="button" onClick={() => { setShowForgot(true); setServerError('') }}
                    style={{ fontSize: 12, color: 'var(--color-text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                    onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--color-text-secondary)')}
                    onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--color-text-muted)')}>
                    Forgot password?
                  </button>
                </div>
                <PasswordInput registerProps={loginForm.register('password')} autoComplete="current-password" />
                {loginForm.formState.errors.password && <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>{loginForm.formState.errors.password.message}</p>}
              </div>
              {serverError && <ErrorBox msg={serverError} />}
              {showResend && !resendMsg && (
                <div className="rounded-lg p-3 space-y-2" style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)' }}>
                  <p style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Enter your email to resend the confirmation link.</p>
                  <div className="flex gap-2">
                    <input
                      type="email"
                      placeholder="you@example.com"
                      value={resendEmail}
                      onChange={(e) => setResendEmail(e.target.value)}
                      className="flex-1"
                      style={{ fontSize: 13 }}
                    />
                    <button
                      type="button"
                      onClick={onResend}
                      disabled={resendLoading}
                      className="px-3 py-1.5 rounded-lg font-medium disabled:opacity-60"
                      style={{ background: 'var(--color-accent)', color: 'var(--color-accent-contrast)', fontSize: 12, whiteSpace: 'nowrap' }}
                    >
                      {resendLoading ? 'Sending…' : 'Resend'}
                    </button>
                  </div>
                </div>
              )}
              {resendMsg && (
                <div className="rounded-lg px-3 py-2" style={{ background: 'rgba(90,191,138,0.1)', border: '1px solid rgba(90,191,138,0.3)', color: 'var(--color-positive)', fontSize: 12 }}>
                  {resendMsg}
                </div>
              )}
              <div className="pt-2">
                <SubmitButton loading={isSubmitting} label="Sign in" loadingLabel="Signing in…" />
              </div>
            </form>
          )}

          {/* ── Register form ── */}
          {tab === 'register' && !registered && (
            <form onSubmit={registerForm.handleSubmit(onRegister)} className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block mb-1.5" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>First name</label>
                  <input {...registerForm.register('first_name')} type="text" placeholder="First" autoComplete="given-name" className="w-full" style={{ fontSize: 14 }} />
                  {registerForm.formState.errors.first_name && <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>{registerForm.formState.errors.first_name.message}</p>}
                </div>
                <div>
                  <label className="block mb-1.5" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>Last name</label>
                  <input {...registerForm.register('last_name')} type="text" placeholder="Last" autoComplete="family-name" className="w-full" style={{ fontSize: 14 }} />
                  {registerForm.formState.errors.last_name && <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>{registerForm.formState.errors.last_name.message}</p>}
                </div>
              </div>
              <div>
                <label className="block mb-1.5" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>Email</label>
                <input {...registerForm.register('email')} type="email" placeholder="you@example.com" autoComplete="email" className="w-full" style={{ fontSize: 14 }} />
                {registerForm.formState.errors.email && <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>{registerForm.formState.errors.email.message}</p>}
              </div>
              <div>
                <label className="block mb-1.5" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>Phone</label>
                <input {...registerForm.register('phone')} type="tel" placeholder="+1 555 000 0000" autoComplete="tel" className="w-full" style={{ fontSize: 14 }} />
                {registerForm.formState.errors.phone && <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>{registerForm.formState.errors.phone.message}</p>}
              </div>
              <div>
                <label className="block mb-1.5" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>Username</label>
                <input {...registerForm.register('username')} type="text" placeholder="username" autoComplete="username" className="w-full" style={{ fontSize: 14 }} />
                {registerForm.formState.errors.username && <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>{registerForm.formState.errors.username.message}</p>}
              </div>
              <div>
                <label className="block mb-1.5" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>Password</label>
                <PasswordInput registerProps={registerForm.register('password')} autoComplete="new-password" />
                {registerForm.formState.errors.password && <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>{registerForm.formState.errors.password.message}</p>}
              </div>
              {serverError && <ErrorBox msg={serverError} />}
              <div className="pt-2">
                <SubmitButton loading={isSubmitting} label="Create account" loadingLabel="Creating account…" />
              </div>
            </form>
          )}

          {/* ── Post-registration email check ── */}
          {tab === 'register' && registered && (
            <div className="text-center py-4 space-y-3">
              <div style={{ fontSize: 36 }}>📬</div>
              <h3 style={{ fontSize: 15, fontWeight: 600, color: 'var(--color-text-primary)' }}>Check your email</h3>
              <p style={{ fontSize: 13, color: 'var(--color-text-muted)', lineHeight: 1.6 }}>
                We sent a confirmation link to your email address. Click it to activate your account and sign in.
              </p>
              {!resendMsg ? (
                <button
                  onClick={() => { setShowResend(true); setTab('login') }}
                  style={{ fontSize: 12, color: 'var(--color-text-muted)', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline' }}
                >
                  Didn't get it? Resend
                </button>
              ) : (
                <p style={{ fontSize: 12, color: 'var(--color-positive)' }}>{resendMsg}</p>
              )}
              <button onClick={() => switchTab('login')} style={{ fontSize: 12, color: 'var(--color-text-muted)', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline' }}>
                Back to sign in
              </button>
            </div>
          )}
        </div>
      </div>

      {/* ── Forgot password modal ── */}
      {showForgot && (
        <div className="fixed inset-0 flex items-center justify-center" style={{ background: 'rgba(0,0,0,0.5)', zIndex: 50 }}
          onClick={(e) => { if (e.target === e.currentTarget) closeForgot() }}>
          <div className="rounded-2xl p-6 w-full" style={{ maxWidth: 360, background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}>
            <h2 style={{ fontSize: 16, fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 4 }}>Forgot password</h2>
            <p style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 20 }}>
              Enter your email and we'll send you a reset link.
            </p>

            {!successMsg ? (
              <form onSubmit={forgotForm.handleSubmit(onForgot)} className="space-y-4">
                <div>
                  <label className="block mb-1.5" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>Email</label>
                  <input {...forgotForm.register('email')} type="email" placeholder="jane@example.com" autoComplete="email" className="w-full" style={{ fontSize: 14 }} />
                  {forgotForm.formState.errors.email && <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>{forgotForm.formState.errors.email.message}</p>}
                </div>
                {serverError && <ErrorBox msg={serverError} />}
                <div className="flex gap-2 pt-1">
                  <button type="button" onClick={closeForgot} className="flex-1 py-2 rounded-lg font-medium"
                    style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)', fontSize: 13 }}>
                    Cancel
                  </button>
                  <SubmitButton loading={isSubmitting} label="Send reset link" loadingLabel="Sending…" flex />
                </div>
              </form>
            ) : (
              <div className="space-y-4">
                <div className="rounded-lg px-3 py-2" style={{ background: 'rgba(90,191,138,0.1)', border: '1px solid rgba(90,191,138,0.3)', color: 'var(--color-positive)', fontSize: 12 }}>
                  {successMsg}
                </div>
                <button onClick={closeForgot} className="w-full py-2 rounded-lg font-medium"
                  style={{ background: 'var(--color-accent)', color: 'var(--color-accent-contrast)', fontSize: 13 }}>
                  Done
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function ErrorBox({ msg }: { msg: string }) {
  return (
    <div className="rounded-lg px-3 py-2" style={{ background: 'rgba(232,96,96,0.1)', border: '1px solid rgba(232,96,96,0.3)', color: 'var(--color-negative)', fontSize: 12 }}>
      {msg}
    </div>
  )
}

function SubmitButton({ loading, label, loadingLabel, flex }: { loading: boolean; label: string; loadingLabel: string; flex?: boolean }) {
  return (
    <button type="submit" disabled={loading}
      className={`${flex ? 'flex-1' : 'w-full'} py-2.5 rounded-lg font-semibold transition-opacity disabled:opacity-60`}
      style={{ background: 'var(--color-accent)', color: 'var(--color-accent-contrast)', fontSize: 14, marginTop: flex ? 0 : 8 }}>
      {loading ? loadingLabel : label}
    </button>
  )
}

import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { authApi } from '../lib/api'
import PasswordInput from '../components/PasswordInput'

const schema = z.object({
  new_password: z.string().min(8, 'Password must be at least 8 characters'),
  confirm_password: z.string().min(1, 'Please confirm your password'),
}).refine((d) => d.new_password === d.confirm_password, {
  message: "Passwords don't match",
  path: ['confirm_password'],
})

type FormData = z.infer<typeof schema>

export default function ResetPassword() {
  const [searchParams] = useSearchParams()
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [success, setSuccess] = useState(false)
  const [serverError, setServerError] = useState('')
  const navigate = useNavigate()
  const token = searchParams.get('token') || ''

  const { register, handleSubmit, formState: { errors } } = useForm<FormData>({
    resolver: zodResolver(schema),
  })

  const onSubmit = async (data: FormData) => {
    if (!token) {
      setServerError('Missing reset token. Please request a new reset link.')
      return
    }
    setServerError('')
    setIsSubmitting(true)
    try {
      await authApi.resetPassword(token, data.new_password)
      setSuccess(true)
      setTimeout(() => navigate('/login', { replace: true }), 2500)
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } }
      setServerError(e.response?.data?.detail || 'This link is invalid or has expired.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex items-start justify-center" style={{ background: 'var(--color-bg)', paddingTop: '10vh' }}>
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <span style={{ fontSize: 36, fontWeight: 700, letterSpacing: '-0.03em', color: 'var(--color-text-primary)' }}>
            spend<span style={{ color: 'var(--color-accent)' }}>.</span>
          </span>
        </div>

        <div className="rounded-2xl p-6" style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}>
          {!success ? (
            <>
              <h2 style={{ fontSize: 16, fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 4 }}>Set new password</h2>
              <p style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 20 }}>Choose a strong password for your account.</p>

              <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
                <div>
                  <label className="block mb-1.5" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>New password</label>
                  <PasswordInput registerProps={register('new_password')} autoComplete="new-password" />
                  {errors.new_password && <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>{errors.new_password.message}</p>}
                </div>
                <div>
                  <label className="block mb-1.5" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>Confirm password</label>
                  <PasswordInput registerProps={register('confirm_password')} autoComplete="new-password" />
                  {errors.confirm_password && <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>{errors.confirm_password.message}</p>}
                </div>

                {serverError && (
                  <div className="rounded-lg px-3 py-2" style={{ background: 'rgba(232,96,96,0.1)', border: '1px solid rgba(232,96,96,0.3)', color: 'var(--color-negative)', fontSize: 12 }}>
                    {serverError}
                  </div>
                )}

                <button type="submit" disabled={isSubmitting}
                  className="w-full py-2.5 rounded-lg font-semibold transition-opacity disabled:opacity-60"
                  style={{ background: 'var(--color-accent)', color: 'var(--color-accent-contrast)', fontSize: 14, marginTop: 8 }}>
                  {isSubmitting ? 'Saving…' : 'Set new password'}
                </button>
              </form>
            </>
          ) : (
            <div className="text-center py-4 space-y-3">
              <div style={{ fontSize: 36 }}>✅</div>
              <h3 style={{ fontSize: 15, fontWeight: 600, color: 'var(--color-text-primary)' }}>Password updated</h3>
              <p style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Taking you back to sign in…</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

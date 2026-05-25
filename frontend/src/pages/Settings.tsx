import { useState, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Trash2, Plus, Sun, Moon, CreditCard, Shield, Palette, Tag, AlertTriangle, UserCircle, FileText, ChevronDown, ChevronUp, Check, Lock, Eye, XCircle } from 'lucide-react'
import { accountsApi, plaidApi, settingsApi, categoriesApi } from '../lib/api'
import { useTheme, type DarkPalette, type LightPalette } from '../context/ThemeContext'
import { useAuth } from '../context/AuthContext'
import Card from '../components/Card'
import Spinner from '../components/Spinner'

// Profile schema
const profileSchema = z.object({
  first_name: z.string().min(1, 'First name is required'),
  last_name: z.string().min(1, 'Last name is required'),
  phone: z.string().min(7, 'Enter a valid phone number'),
})

type ProfileFormData = z.infer<typeof profileSchema>

// Password change schema
const passwordSchema = z
  .object({
    current_password: z.string().min(1, 'Current password is required'),
    new_password: z.string().min(8, 'New password must be at least 8 characters'),
    confirm_password: z.string().min(1, 'Please confirm your password'),
  })
  .refine((d) => d.new_password === d.confirm_password, {
    message: 'Passwords do not match',
    path: ['confirm_password'],
  })

type PasswordFormData = z.infer<typeof passwordSchema>

const POLICIES = [
  {
    title: 'Privacy Policy',
    content: `Last updated: April 2026

spend. ("we", "us") is a personal finance application that connects to your bank accounts via Plaid to provide transaction insights and AI-powered financial guidance.

Data we collect
• Account credentials (username, hashed password, email, phone)
• Financial data accessed via Plaid: account balances, transaction history, institution names
• Usage data: session tokens, last sync timestamps

How we use your data
Your data is used solely to provide the spend. service — displaying your transactions, generating insights, and powering the AI advisor. We do not sell, share, or monetize your data with third parties.

Third-party services
• Plaid — used to securely access your financial institution data. Plaid's privacy policy applies to data shared with them.
• Supabase — database and file storage provider.
• Resend — transactional email delivery.
• Anthropic — AI model provider for the advisor feature. Only relevant context is sent; raw transaction data is not transmitted.

Data retention
Your data is retained for as long as your account is active. Upon account deletion, all data is permanently removed after a 30-day grace period.

Contact
For privacy questions, contact us at privacy@wingingbump.com.`,
  },
  {
    title: 'Information Security Policy',
    content: `Last updated: April 2026

spend. is committed to protecting the security of user data. This policy summarizes our security practices.

Encryption
• All data in transit is encrypted via TLS/HTTPS.
• Sensitive credentials (Plaid access tokens) are encrypted at rest using AES-256 (Fernet).
• Passwords are hashed using bcrypt with a cost factor of 12.

Authentication & access control
• Users authenticate via username/password with JWT-based session tokens.
• Access tokens expire after a short window; refresh tokens are rotated on each use and stored as single-use.
• All API endpoints require authentication. Resources are scoped to the authenticated user — no user can access another user's data.

Vulnerability management
• Dependencies are monitored continuously via GitHub Dependabot.
• Identified vulnerabilities are reviewed and patched within 30 days (critical: 7 days).

Incident response
Security incidents are investigated promptly. Affected users will be notified within 72 hours of a confirmed breach involving their data.

Infrastructure
• Backend hosted on Render (SOC 2 Type II certified).
• Database hosted on Supabase (SOC 2 Type II certified).
• Frontend hosted on Vercel (SOC 2 Type II certified).`,
  },
  {
    title: 'Data Deletion & Retention Policy',
    content: `Last updated: April 2026

Account deletion
You may request deletion of your account at any time from Settings → Danger Zone. Deletion is scheduled with a 30-day grace period, during which you may cancel the request and restore your account.

What gets deleted
Upon permanent deletion, the following data is removed:
• Your profile (name, email, phone, username)
• All connected bank accounts and Plaid access tokens
• All transaction history
• All financial insights and category mappings
• Session tokens and refresh tokens

Data retention during active use
• Transaction data is retained as long as your account is active to power insights and the AI advisor.
• Plaid access tokens are retained to enable background syncing and are encrypted at rest.

Requests
To request immediate data deletion or a copy of your data, contact privacy@wingingbump.com.`,
  },
  {
    title: 'Access Control Policy',
    content: `Last updated: April 2026

spend. enforces access control at every layer of the application.

Authentication
All users must authenticate with a verified email address and password before accessing any data. Session tokens are short-lived and rotated regularly.

Authorization
Every API request is authenticated and authorized server-side. Data access is strictly scoped to the authenticated user — queries include user_id filters at the database level. No user can read, modify, or delete another user's data.

Role-based access
Currently spend. has one user role (standard user). Administrative access to infrastructure (Render, Supabase, Vercel) is restricted to the service owner and protected by multi-factor authentication.

Access reviews
Administrative access to all systems is reviewed periodically. Unused credentials are revoked promptly.`,
  },
]

function PolicyItem({ title, content }: { title: string; content: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ borderBottom: '1px solid var(--color-border)' }}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between py-3"
        style={{ background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left' }}
      >
        <span style={{ fontSize: 13, color: 'var(--color-text-secondary)', fontWeight: 500 }}>{title}</span>
        {open
          ? <ChevronUp size={14} style={{ color: 'var(--color-text-muted)', flexShrink: 0 }} />
          : <ChevronDown size={14} style={{ color: 'var(--color-text-muted)', flexShrink: 0 }} />
        }
      </button>
      {open && (
        <div className="pb-4" style={{ fontSize: 12, color: 'var(--color-text-muted)', lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>
          {content}
        </div>
      )}
    </div>
  )
}

function LegalSection() {
  return (
    <Card>
      <SectionHeader icon={FileText} title="Legal" />
      <p className="mb-4" style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
        Policies governing use of spend. and how we handle your data.
      </p>
      <div>
        {POLICIES.map((p) => (
          <PolicyItem key={p.title} title={p.title} content={p.content} />
        ))}
      </div>
      <p className="mt-4" style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
        spend. · wingingbump.com · privacy@wingingbump.com
      </p>
    </Card>
  )
}

function SectionHeader({ icon: Icon, title }: { icon: React.ElementType; title: string }) {
  return (
    <div className="flex items-center gap-2 mb-4">
      <Icon size={16} style={{ color: 'var(--color-text-muted)' }} />
      <h2 style={{ fontSize: 15, fontWeight: 600, color: 'var(--color-text-primary)' }}>{title}</h2>
    </div>
  )
}

const DARK_PALETTES = [
  { id: 'default',  label: 'Default',  bg: '#111318', surface: '#1a1c20', accent: '#1a56db' },
  { id: 'midnight', label: 'Midnight', bg: '#090c14', surface: '#101828', accent: '#4d8be8' },
  { id: 'forest',   label: 'Forest',   bg: '#0b1410', surface: '#111f18', accent: '#3eb87f' },
  { id: 'ember',    label: 'Ember',    bg: '#150f0a', surface: '#1f1510', accent: '#e07340' },
]

const LIGHT_PALETTES = [
  { id: 'default',  label: 'Default',  bg: '#f0f2f8', surface: '#e8eaf4', accent: '#1a56db' },
  { id: 'warm',     label: 'Warm',     bg: '#f6f0e8', surface: '#ede5d8', accent: '#c25d1e' },
  { id: 'sage',     label: 'Sage',     bg: '#edf3ed', surface: '#e3ece3', accent: '#2d7a4f' },
  { id: 'lavender', label: 'Lavender', bg: '#f0eef8', surface: '#e8e4f4', accent: '#6b4fd8' },
]

function PaletteSwatch({
  label, bg, surface, accent, selected, onClick,
}: {
  label: string
  bg: string
  surface: string
  accent: string
  selected: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      style={{
        background: 'none',
        border: 'none',
        cursor: 'pointer',
        padding: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 6,
      }}
    >
      <div
        style={{
          width: 64,
          height: 48,
          borderRadius: 10,
          background: bg,
          border: selected ? `2px solid ${accent}` : '2px solid transparent',
          outline: selected ? `2px solid ${accent}` : '2px solid transparent',
          outlineOffset: 1,
          position: 'relative',
          overflow: 'hidden',
          boxShadow: selected ? `0 0 0 1px ${accent}40` : '0 0 0 1px rgba(128,128,128,0.2)',
          transition: 'box-shadow 0.15s, outline 0.15s',
        }}
      >
        {/* Surface strip */}
        <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: 20, background: surface }} />
        {/* Accent dot */}
        <div style={{
          position: 'absolute',
          bottom: 5,
          right: 7,
          width: 10,
          height: 10,
          borderRadius: '50%',
          background: accent,
        }} />
        {/* Check mark */}
        {selected && (
          <div style={{
            position: 'absolute',
            top: 5,
            right: 5,
            width: 16,
            height: 16,
            borderRadius: '50%',
            background: accent,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}>
            <Check size={10} color="#fff" strokeWidth={3} />
          </div>
        )}
      </div>
      <span style={{ fontSize: 11, color: selected ? 'var(--color-text-primary)' : 'var(--color-text-muted)', fontWeight: selected ? 600 : 400 }}>
        {label}
      </span>
    </button>
  )
}

export default function Settings() {
  const { theme, setTheme, darkPalette, lightPalette, setDarkPalette, setLightPalette } = useTheme()
  const { user, logout } = useAuth()
  const qc = useQueryClient()
  const plaidScriptLoaded = useRef(false)

  // ── Profile ─────────────────────────────────────────────────────────────────
  const { data: profile, isLoading: loadingProfile } = useQuery({
    queryKey: ['profile'],
    queryFn: () => settingsApi.getProfile(),
  })

  const [profileSuccess, setProfileSuccess] = useState(false)
  const [profileError, setProfileError] = useState('')
  const [avatarUploading, setAvatarUploading] = useState(false)
  const avatarInputRef = useRef<HTMLInputElement>(null)

  const {
    register: registerProfile,
    handleSubmit: handleProfileSubmit,
    formState: { errors: profileErrors, isSubmitting: profileSubmitting },
  } = useForm<ProfileFormData>({
    resolver: zodResolver(profileSchema),
    values: profile ? { first_name: profile.first_name, last_name: profile.last_name, phone: profile.phone } : undefined,
  })

  const updateProfileMutation = useMutation({
    mutationFn: ({ first_name, last_name, phone }: ProfileFormData) => settingsApi.updateProfile(first_name, last_name, phone),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['profile'] })
      setProfileSuccess(true)
      setTimeout(() => setProfileSuccess(false), 2000)
    },
    onError: (err: unknown) => {
      const e = err as { response?: { data?: { detail?: string } } }
      setProfileError(e.response?.data?.detail || 'Failed to update profile')
    },
  })

  const onProfileSubmit = (data: ProfileFormData) => {
    setProfileError('')
    setProfileSuccess(false)
    updateProfileMutation.mutate(data)
  }

  const handleAvatarChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const allowed = ['image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/heic', 'image/heif']
    if (!allowed.includes(file.type)) {
      setProfileError('Only JPEG, PNG, WebP, GIF, or HEIC allowed')
      return
    }
    if (file.size > 5 * 1024 * 1024) {
      setProfileError('Avatar must be under 5 MB')
      return
    }
    setAvatarUploading(true)
    try {
      await settingsApi.uploadAvatar(file)
      qc.invalidateQueries({ queryKey: ['profile'] })
    } catch {
      setProfileError('Failed to upload avatar')
    } finally {
      setAvatarUploading(false)
      if (avatarInputRef.current) avatarInputRef.current.value = ''
    }
  }

  // ── Accounts ────────────────────────────────────────────────────────────────
  const { data: accounts = [], isLoading: loadingAccounts } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })

  const deleteAccountMutation = useMutation({
    mutationFn: (id: number) => accountsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  })

  const [confirmDeleteAccount, setConfirmDeleteAccount] = useState<number | null>(null)

  const handleDeleteAccount = (id: number) => {
    if (confirmDeleteAccount === id) {
      deleteAccountMutation.mutate(id)
      setConfirmDeleteAccount(null)
    } else {
      setConfirmDeleteAccount(id)
    }
  }

  // ── Plaid ────────────────────────────────────────────────────────────────────
  const [plaidLoading, setPlaidLoading] = useState(false)
  const [plaidError, setPlaidError] = useState('')

  const handleConnectPlaid = async () => {
    setPlaidLoading(true)
    setPlaidError('')
    try {
      const { link_token, signed_token } = await plaidApi.linkToken()

      if (!plaidScriptLoaded.current) {
        await new Promise<void>((resolve, reject) => {
          const script = document.createElement('script')
          script.src = 'https://cdn.plaid.com/link/v2/stable/link-initialize.js'
          script.onload = () => { plaidScriptLoaded.current = true; resolve() }
          script.onerror = reject
          document.head.appendChild(script)
        })
      }

      const Plaid = (window as Window & { Plaid?: { create: (config: unknown) => { open: () => void } } }).Plaid
      if (!Plaid) throw new Error('Plaid script not loaded')

      const handler = Plaid.create({
        token: link_token,
        onSuccess: async (public_token: string, metadata: { institution: { name: string }; account: { subtype: string } | null; accounts: { subtype: string }[] }) => {
          try {
            const subtype = metadata.account?.subtype || metadata.accounts?.[0]?.subtype || 'bank'
            await plaidApi.exchange(public_token, metadata.institution?.name || 'Unknown', subtype, signed_token)
            qc.invalidateQueries({ queryKey: ['accounts'] })
            qc.invalidateQueries({ queryKey: ['institutions'] })
          } catch {
            setPlaidError('Failed to connect account')
          } finally {
            setPlaidLoading(false)
          }
        },
        onExit: () => setPlaidLoading(false),
        onLoad: () => {},
        onEvent: () => {},
      })
      handler.open()
    } catch {
      setPlaidError('Failed to initialize Plaid. Make sure the backend is running.')
      setPlaidLoading(false)
    }
  }

  // ── Password ────────────────────────────────────────────────────────────────
  const [passwordSuccess, setPasswordSuccess] = useState(false)
  const [passwordError, setPasswordError] = useState('')

  const {
    register: registerPw,
    handleSubmit: handlePwSubmit,
    reset: resetPw,
    formState: { errors: pwErrors, isSubmitting: pwSubmitting },
  } = useForm<PasswordFormData>({ resolver: zodResolver(passwordSchema) })

  const onPasswordSubmit = async (data: PasswordFormData) => {
    setPasswordError('')
    setPasswordSuccess(false)
    try {
      await settingsApi.changePassword(data.current_password, data.new_password)
      setPasswordSuccess(true)
      resetPw()
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } }
      setPasswordError(axiosErr.response?.data?.detail || 'Failed to change password')
    }
  }

  // ── User Categories ───────────────────────────────────────────────────────────
  const { data: userCategories = [], isLoading: loadingCategories } = useQuery({
    queryKey: ['user-categories'],
    queryFn: () => categoriesApi.userCategories(),
  })

  const [newCategoryName, setNewCategoryName] = useState('')
  const [categoryError, setCategoryError] = useState('')

  const addCategoryMutation = useMutation({
    mutationFn: (name: string) => categoriesApi.createUserCategory(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['user-categories'] })
      setNewCategoryName('')
      setCategoryError('')
    },
    onError: () => setCategoryError('Failed to add category'),
  })

  const deleteCategoryMutation = useMutation({
    mutationFn: (name: string) => categoriesApi.deleteUserCategory(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['user-categories'] }),
  })

  const handleAddCategory = () => {
    const name = newCategoryName.trim()
    if (!name) return
    if (userCategories.includes(name)) {
      setCategoryError('Category already exists')
      return
    }
    addCategoryMutation.mutate(name)
  }

  // ── Account Deletion ─────────────────────────────────────────────────────────
  const { data: deletionData, refetch: refetchDeletion } = useQuery({
    queryKey: ['deletion-status'],
    queryFn: () => settingsApi.deletionStatus(),
  })

  const [showDeleteModal, setShowDeleteModal] = useState(false)
  const [deleteConfirmText, setDeleteConfirmText] = useState('')

  const scheduleDeleteMutation = useMutation({
    mutationFn: () => settingsApi.deleteAccount(),
    onSuccess: () => {
      refetchDeletion()
      setShowDeleteModal(false)
      setDeleteConfirmText('')
      logout()
    },
  })

  const cancelDeletionMutation = useMutation({
    mutationFn: () => settingsApi.cancelDeletion(),
    onSuccess: () => refetchDeletion(),
  })

  const deletionScheduledAt = deletionData?.deletion_scheduled_at
    ? new Date(deletionData.deletion_scheduled_at)
    : null

  return (
    <div className="space-y-6 fade-in max-w-2xl mx-auto">
      <div>
        <h1 style={{ fontSize: 20, fontWeight: 700, color: 'var(--color-text-primary)' }}>Settings</h1>
        <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 2 }}>
          Manage your account and preferences
        </p>
      </div>

      {/* ── Section 0: Profile ──────────────────────────────────────────────── */}
      <Card>
        <SectionHeader icon={UserCircle} title="Profile" />

        {loadingProfile ? (
          <Spinner size={18} />
        ) : (
          <div className="flex items-start gap-6">
            {/* Avatar */}
            <div className="flex flex-col items-center gap-2 flex-shrink-0">
              <div
                className="w-16 h-16 rounded-full overflow-hidden flex items-center justify-center"
                style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)' }}
              >
                {profile?.avatar_url ? (
                  <img src={profile.avatar_url} alt="Avatar" className="w-full h-full object-cover" />
                ) : (
                  <UserCircle size={32} style={{ color: 'var(--color-text-muted)' }} />
                )}
              </div>
              <button
                onClick={() => avatarInputRef.current?.click()}
                disabled={avatarUploading}
                className="text-center transition-opacity disabled:opacity-60"
                style={{ fontSize: 11, color: 'var(--color-text-muted)', background: 'none', border: 'none', cursor: 'pointer' }}
              >
                {avatarUploading ? 'Uploading…' : 'Change photo'}
              </button>
              <input
                ref={avatarInputRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={handleAvatarChange}
              />
            </div>

            {/* Fields */}
            <form onSubmit={handleProfileSubmit(onProfileSubmit)} className="flex-1 space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block mb-1.5" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>First name</label>
                  <input {...registerProfile('first_name')} type="text" autoComplete="given-name" className="w-full" style={{ fontSize: 13 }} />
                  {profileErrors.first_name && <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>{profileErrors.first_name.message}</p>}
                </div>
                <div>
                  <label className="block mb-1.5" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>Last name</label>
                  <input {...registerProfile('last_name')} type="text" autoComplete="family-name" className="w-full" style={{ fontSize: 13 }} />
                  {profileErrors.last_name && <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>{profileErrors.last_name.message}</p>}
                </div>
              </div>
              <div>
                <label className="block mb-1.5" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>Phone</label>
                <input {...registerProfile('phone')} type="tel" autoComplete="tel" className="w-full" style={{ fontSize: 13 }} />
                {profileErrors.phone && <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>{profileErrors.phone.message}</p>}
              </div>
              <div>
                <label className="block mb-1.5" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>Email</label>
                <input
                  type="email"
                  value={profile?.email ?? ''}
                  disabled
                  className="w-full"
                  style={{ fontSize: 13, opacity: 0.6, cursor: 'not-allowed' }}
                />
                {profile && !profile.email_verified && (
                  <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>Email not verified</p>
                )}
              </div>

              {profileError && <p style={{ fontSize: 12, color: 'var(--color-negative)' }}>{profileError}</p>}
              {profileSuccess && <p style={{ fontSize: 12, color: 'var(--color-positive)' }}>Profile updated</p>}

              <button
                type="submit"
                disabled={profileSubmitting}
                className="px-4 py-2 rounded-lg font-medium transition-opacity disabled:opacity-60"
                style={{ background: 'var(--color-accent)', color: '#000', fontSize: 13 }}
              >
                {profileSubmitting ? 'Saving…' : 'Save changes'}
              </button>
            </form>
          </div>
        )}
      </Card>

      {/* ── Section 1: Connected Accounts ───────────────────────────────────── */}
      <Card>
        <SectionHeader icon={CreditCard} title="Connected Accounts" />

        {loadingAccounts ? (
          <Spinner size={18} />
        ) : accounts.length === 0 ? (
          <p style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>No accounts connected yet.</p>
        ) : (
          <div className="space-y-2 mb-4">
            {accounts.map((acc) => (
              <div
                key={acc.id}
                className="flex items-center justify-between px-3 py-2.5 rounded-lg"
                style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)' }}
              >
                <div>
                  <p style={{ fontSize: 13, fontWeight: 500, color: 'var(--color-text-primary)' }}>{acc.name}</p>
                  <p style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                    {acc.account_type} · Added {new Date(acc.created_at).toLocaleDateString()}
                  </p>
                </div>
                <button
                  onClick={() => handleDeleteAccount(acc.id)}
                  className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg transition-colors"
                  style={{
                    background: confirmDeleteAccount === acc.id ? 'rgba(232, 96, 96, 0.15)' : 'transparent',
                    color: 'var(--color-negative)',
                    fontSize: 12,
                    border: confirmDeleteAccount === acc.id ? '1px solid rgba(232, 96, 96, 0.3)' : '1px solid transparent',
                  }}
                >
                  <Trash2 size={12} />
                  {confirmDeleteAccount === acc.id ? 'Confirm?' : 'Remove'}
                </button>
              </div>
            ))}
          </div>
        )}

        {accounts.length === 0 && !loadingAccounts && (
          <div
            className="rounded-lg p-3 mb-3"
            style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)' }}
          >
            <p style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>
              Before you connect
            </p>
            <div className="space-y-2">
              {[
                { Icon: Lock,    text: 'Plaid handles your bank login — we never see your credentials.' },
                { Icon: Eye,     text: 'Read-only access. We can view transactions but cannot move money.' },
                { Icon: XCircle, text: 'Disconnect any account or delete your data anytime in Settings.' },
              ].map(({ Icon, text }) => (
                <div key={text} className="flex items-start gap-2">
                  <Icon size={13} style={{ color: 'var(--color-positive)', marginTop: 1, flexShrink: 0 }} />
                  <p style={{ fontSize: 12, color: 'var(--color-text-secondary)', lineHeight: 1.45 }}>{text}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        <button
          onClick={handleConnectPlaid}
          disabled={plaidLoading}
          className="flex items-center gap-2 px-4 py-2 rounded-lg font-medium transition-opacity disabled:opacity-60"
          style={{ background: 'var(--color-accent)', color: '#000', fontSize: 13 }}
        >
          {plaidLoading ? <Spinner size={14} /> : <Plus size={14} />}
          {plaidLoading ? 'Connecting…' : 'Connect Account'}
        </button>

        {plaidError && (
          <p className="mt-2" style={{ fontSize: 12, color: 'var(--color-negative)' }}>{plaidError}</p>
        )}
      </Card>

      {/* ── Section 2: Security ─────────────────────────────────────────────── */}
      <Card>
        <SectionHeader icon={Shield} title="Security" />

        <p className="mb-4" style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
          Logged in as <span style={{ color: 'var(--color-text-primary)', fontWeight: 500 }}>{user?.username}</span>
        </p>

        <form onSubmit={handlePwSubmit(onPasswordSubmit)} className="space-y-4">
          <div>
            <label className="block mb-1.5" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>
              Current Password
            </label>
            <input {...registerPw('current_password')} type="password" autoComplete="current-password" className="w-full max-w-sm" />
            {pwErrors.current_password && (
              <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>{pwErrors.current_password.message}</p>
            )}
          </div>
          <div>
            <label className="block mb-1.5" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>
              New Password
            </label>
            <input {...registerPw('new_password')} type="password" autoComplete="new-password" className="w-full max-w-sm" />
            {pwErrors.new_password && (
              <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>{pwErrors.new_password.message}</p>
            )}
          </div>
          <div>
            <label className="block mb-1.5" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>
              Confirm New Password
            </label>
            <input {...registerPw('confirm_password')} type="password" autoComplete="new-password" className="w-full max-w-sm" />
            {pwErrors.confirm_password && (
              <p className="mt-1" style={{ fontSize: 11, color: 'var(--color-negative)' }}>{pwErrors.confirm_password.message}</p>
            )}
          </div>

          {passwordError && <p style={{ fontSize: 12, color: 'var(--color-negative)' }}>{passwordError}</p>}
          {passwordSuccess && <p style={{ fontSize: 12, color: 'var(--color-positive)' }}>Password updated successfully</p>}

          <button
            type="submit"
            disabled={pwSubmitting}
            className="px-4 py-2 rounded-lg font-medium transition-opacity disabled:opacity-60"
            style={{ background: 'var(--color-accent)', color: '#000', fontSize: 13 }}
          >
            {pwSubmitting ? 'Updating…' : 'Update Password'}
          </button>
        </form>
      </Card>

      {/* ── Section 3: Appearance ───────────────────────────────────────────── */}
      <Card>
        <SectionHeader icon={Palette} title="Appearance" />

        {/* Mode toggle */}
        <p className="mb-2" style={{ fontSize: 12, color: 'var(--color-text-muted)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Mode</p>
        <div className="flex items-center gap-3 mb-6">
          <button
            onClick={() => setTheme('dark')}
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg font-medium transition-all"
            style={{
              background: theme === 'dark' ? 'var(--color-accent)' : 'var(--color-surface-raise)',
              color: theme === 'dark' ? '#fff' : 'var(--color-text-secondary)',
              border: theme === 'dark' ? '1px solid transparent' : '1px solid var(--color-border)',
              fontSize: 13,
            }}
          >
            <Moon size={14} /> Dark
          </button>
          <button
            onClick={() => setTheme('light')}
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg font-medium transition-all"
            style={{
              background: theme === 'light' ? 'var(--color-accent)' : 'var(--color-surface-raise)',
              color: theme === 'light' ? '#fff' : 'var(--color-text-secondary)',
              border: theme === 'light' ? '1px solid transparent' : '1px solid var(--color-border)',
              fontSize: 13,
            }}
          >
            <Sun size={14} /> Light
          </button>
        </div>

        {/* Color palette */}
        <p className="mb-3" style={{ fontSize: 12, color: 'var(--color-text-muted)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Color palette</p>
        {theme === 'dark' ? (
          <div className="flex flex-wrap gap-3">
            {DARK_PALETTES.map((p) => (
              <PaletteSwatch
                key={p.id}
                label={p.label}
                bg={p.bg}
                surface={p.surface}
                accent={p.accent}
                selected={darkPalette === p.id}
                onClick={() => setDarkPalette(p.id as DarkPalette)}
              />
            ))}
          </div>
        ) : (
          <div className="flex flex-wrap gap-3">
            {LIGHT_PALETTES.map((p) => (
              <PaletteSwatch
                key={p.id}
                label={p.label}
                bg={p.bg}
                surface={p.surface}
                accent={p.accent}
                selected={lightPalette === p.id}
                onClick={() => setLightPalette(p.id as LightPalette)}
              />
            ))}
          </div>
        )}
      </Card>

      {/* ── Section 4: My Categories ─────────────────────────────────────────── */}
      <Card>
        <SectionHeader icon={Tag} title="My Categories" />
        <p className="mb-5" style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
          These categories appear in all transaction dropdowns. Add your own or remove ones you don't use.
        </p>

        {loadingCategories ? (
          <Spinner size={16} />
        ) : (
          <div className="flex flex-wrap gap-2 mb-5">
            {userCategories.map((cat) => (
              <div
                key={cat}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-full"
                style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)' }}
              >
                <span style={{ fontSize: 13, color: 'var(--color-text-primary)' }}>{cat}</span>
                <button
                  onClick={() => deleteCategoryMutation.mutate(cat)}
                  title="Remove category"
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-text-muted)', padding: 0, display: 'flex', alignItems: 'center', lineHeight: 1 }}
                >
                  <Trash2 size={11} />
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-center gap-2">
          <input
            type="text"
            placeholder="New category name…"
            value={newCategoryName}
            onChange={(e) => { setNewCategoryName(e.target.value); setCategoryError('') }}
            onKeyDown={(e) => { if (e.key === 'Enter') handleAddCategory() }}
            style={{ flex: 1 }}
          />
          <button
            onClick={handleAddCategory}
            disabled={addCategoryMutation.isPending || !newCategoryName.trim()}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium transition-opacity disabled:opacity-50"
            style={{ background: 'var(--color-accent)', color: '#000', fontSize: 13, border: 'none', cursor: 'pointer', flexShrink: 0 }}
          >
            <Plus size={13} />
            Add
          </button>
        </div>
        {categoryError && <p className="mt-2" style={{ fontSize: 12, color: 'var(--color-negative)' }}>{categoryError}</p>}
      </Card>

      {/* ── Section 5: Danger Zone ───────────────────────────────────────────── */}
      <Card>
        <SectionHeader icon={AlertTriangle} title="Danger Zone" />

        {deletionScheduledAt ? (
          <div className="rounded-lg px-4 py-3" style={{ background: 'rgba(232, 96, 96, 0.08)', border: '1px solid rgba(232, 96, 96, 0.25)' }}>
            <p style={{ fontSize: 13, color: 'var(--color-negative)', fontWeight: 500, marginBottom: 4 }}>
              Account deletion scheduled
            </p>
            <p style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 12 }}>
              Your account and all data will be permanently deleted on{' '}
              <span style={{ color: 'var(--color-text-secondary)', fontWeight: 500 }}>
                {deletionScheduledAt.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })}
              </span>
              . You can cancel this at any time before that date.
            </p>
            <button
              onClick={() => cancelDeletionMutation.mutate()}
              disabled={cancelDeletionMutation.isPending}
              className="px-4 py-2 rounded-lg font-medium transition-opacity disabled:opacity-60"
              style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)', color: 'var(--color-text-primary)', fontSize: 13 }}
            >
              {cancelDeletionMutation.isPending ? 'Cancelling…' : 'Cancel deletion'}
            </button>
          </div>
        ) : (
          <>
            <p className="mb-4" style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
              Permanently remove your account and all associated data. This is a 30-day grace period deletion — you can restore your account within that window.
            </p>
            <button
              onClick={() => setShowDeleteModal(true)}
              className="flex items-center gap-2 px-4 py-2 rounded-lg font-medium"
              style={{ background: 'rgba(232, 96, 96, 0.1)', border: '1px solid rgba(232, 96, 96, 0.3)', color: 'var(--color-negative)', fontSize: 13 }}
            >
              <Trash2 size={14} />
              Delete my account
            </button>
          </>
        )}
      </Card>

      {/* ── Section 6: Legal ────────────────────────────────────────────────── */}
      <LegalSection />

      {/* ── Delete Account Modal ─────────────────────────────────────────────── */}
      {showDeleteModal && (
        <div
          className="fixed inset-0 flex items-center justify-center"
          style={{ background: 'rgba(0,0,0,0.5)', zIndex: 50 }}
          onClick={(e) => { if (e.target === e.currentTarget) { setShowDeleteModal(false); setDeleteConfirmText('') } }}
        >
          <div
            className="rounded-2xl p-6 w-full"
            style={{ maxWidth: 400, background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
          >
            <div className="flex items-center gap-2 mb-3">
              <AlertTriangle size={16} style={{ color: 'var(--color-negative)' }} />
              <h2 style={{ fontSize: 16, fontWeight: 600, color: 'var(--color-text-primary)' }}>Delete account</h2>
            </div>
            <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 16, lineHeight: 1.6 }}>
              This will schedule your account for deletion in <strong style={{ color: 'var(--color-text-secondary)' }}>30 days</strong>. All your transactions, accounts, and settings will be removed. You can cancel within the 30-day window.
            </p>
            <p className="mb-2" style={{ fontSize: 12, color: 'var(--color-text-secondary)', fontWeight: 500 }}>
              Type <span style={{ fontFamily: 'monospace', color: 'var(--color-text-primary)' }}>delete</span> to confirm
            </p>
            <input
              type="text"
              value={deleteConfirmText}
              onChange={(e) => setDeleteConfirmText(e.target.value)}
              placeholder="delete"
              className="w-full mb-4"
              style={{ fontSize: 13 }}
            />
            <div className="flex gap-2">
              <button
                onClick={() => { setShowDeleteModal(false); setDeleteConfirmText('') }}
                className="flex-1 py-2 rounded-lg font-medium"
                style={{ background: 'var(--color-surface-raise)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)', fontSize: 13, cursor: 'pointer' }}
              >
                Cancel
              </button>
              <button
                onClick={() => scheduleDeleteMutation.mutate()}
                disabled={deleteConfirmText !== 'delete' || scheduleDeleteMutation.isPending}
                className="flex-1 py-2 rounded-lg font-semibold transition-opacity disabled:opacity-40"
                style={{ background: 'var(--color-negative)', color: '#fff', fontSize: 13, cursor: 'pointer', border: 'none' }}
              >
                {scheduleDeleteMutation.isPending ? 'Scheduling…' : 'Delete my account'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

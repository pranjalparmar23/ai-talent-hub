import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { authAPI } from '../services/api'
import toast from 'react-hot-toast'
import { Loader2, UserPlus, Briefcase, GraduationCap } from 'lucide-react'

type Role = 'candidate' | 'recruiter'

export default function Register() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<Role>('candidate')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    if (password.length < 8) {
      toast.error('Password must be at least 8 characters')
      return
    }

    setLoading(true)
    try {
      await authAPI.register({ email, password, role })
      // Remember the role so Login can route correctly later
      localStorage.setItem('user_role', role)
      toast.success('Account created! Please sign in.')
      navigate('/login')
    } catch (err: any) {
      const detail = err?.response?.data?.detail
      if (Array.isArray(detail)) {
        // Pydantic validation error array
        toast.error(detail[0]?.msg || 'Invalid input')
      } else {
        toast.error(detail || 'Registration failed')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 flex items-center justify-center px-4 py-8">
      <div className="max-w-md w-full">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-slate-900">Create Account</h1>
          <p className="text-slate-500 mt-2">Join AI Talent Hub</p>
        </div>

        <div className="bg-white rounded-2xl shadow-sm border border-slate-200 p-8">
          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-2">I am a</label>
              <div className="grid grid-cols-2 gap-3">
                <RoleButton
                  active={role === 'candidate'}
                  icon={<GraduationCap className="w-5 h-5" />}
                  label="Candidate"
                  hint="Looking for jobs"
                  onClick={() => setRole('candidate')}
                />
                <RoleButton
                  active={role === 'recruiter'}
                  icon={<Briefcase className="w-5 h-5" />}
                  label="Recruiter"
                  hint="Hiring talent"
                  onClick={() => setRole('recruiter')}
                />
              </div>
            </div>

            <div>
              <label htmlFor="email" className="block text-sm font-medium text-slate-700 mb-1.5">
                Email
              </label>
              <input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full border border-slate-300 rounded-lg px-3.5 py-2.5 text-sm placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition"
              />
            </div>

            <div>
              <label htmlFor="password" className="block text-sm font-medium text-slate-700 mb-1.5">
                Password
              </label>
              <input
                id="password"
                type="password"
                autoComplete="new-password"
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Min. 8 characters"
                className="w-full border border-slate-300 rounded-lg px-3.5 py-2.5 text-sm placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition"
              />
              <p className="text-xs text-slate-400 mt-1.5">At least 8 characters</p>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-blue-600 hover:bg-blue-700 text-white rounded-lg px-4 py-2.5 text-sm font-medium flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Creating account…
                </>
              ) : (
                <>
                  <UserPlus className="w-4 h-4" />
                  Create Account
                </>
              )}
            </button>
          </form>

          <p className="text-center text-sm text-slate-500 mt-6">
            Already have an account?{' '}
            <Link to="/login" className="text-blue-600 hover:text-blue-700 font-medium">
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </div>
  )
}

// ── Role selector tile ───────────────────────────────────────
interface RoleButtonProps {
  active: boolean
  icon: React.ReactNode
  label: string
  hint: string
  onClick: () => void
}

function RoleButton({ active, icon, label, hint, onClick }: RoleButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex flex-col items-start gap-1.5 p-3.5 rounded-lg border-2 text-left transition-colors ${
        active
          ? 'border-blue-600 bg-blue-50 text-blue-900'
          : 'border-slate-200 hover:border-slate-300 text-slate-600'
      }`}
    >
      <div className={active ? 'text-blue-600' : 'text-slate-400'}>{icon}</div>
      <div>
        <div className="text-sm font-semibold">{label}</div>
        <div className="text-xs text-slate-500">{hint}</div>
      </div>
    </button>
  )
}
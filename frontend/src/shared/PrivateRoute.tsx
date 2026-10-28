import { Navigate } from 'react-router-dom'

export type UserRole = 'candidate' | 'recruiter'

interface PrivateRouteProps {
  children: React.ReactNode
  /** When set, only users with this role may open the page. */
  role?: UserRole
}

/**
 * Client-side guard: needs a token, and (optionally) the right role.
 * This is UX only — the backend enforces roles on every request (403).
 */
export default function PrivateRoute({ children, role }: PrivateRouteProps) {
  const token = localStorage.getItem('access_token')
  if (!token) return <Navigate to="/login" replace />

  if (role) {
    const current = localStorage.getItem('user_role')
    // Unknown role (e.g. stored before roles were tracked): log in again to set it.
    if (current !== 'candidate' && current !== 'recruiter') {
      return <Navigate to="/login" replace />
    }
    // Wrong side: send them to their own home instead of a 403 page.
    if (current !== role) return <Navigate to={`/${current}`} replace />
  }

  return <>{children}</>
}
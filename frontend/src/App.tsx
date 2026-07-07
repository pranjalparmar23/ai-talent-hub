import { Routes, Route, Navigate } from 'react-router-dom'

// Auth pages
import Login from './auth/Login'
import Register from './auth/Register'

// Candidate pages
import CandidateDashboard from './candidate/Dashboard'
import AnalysisView from './candidate/AnalysisView' 

// Recruiter pages
import RecruiterDashboard from './recruiter/Dashboard'

// Shared
import PrivateRoute from './shared/PrivateRoute'

export default function App() {
  return (
    <Routes>
      {/* Public */}
      <Route path="/login"    element={<Login />} />
      <Route path="/register" element={<Register />} />

      {/* Candidate (protected) */}
      <Route path="/candidate/*" element={
        <PrivateRoute>
          <CandidateDashboard />
        </PrivateRoute>
      } />

      <Route path="/candidate/analysis" element={          // ← add this
        <PrivateRoute>
          <AnalysisView />
        </PrivateRoute>
      } />

      {/* Recruiter (protected) */}
      <Route path="/recruiter/*" element={
        <PrivateRoute>
          <RecruiterDashboard />
        </PrivateRoute>
      } />

      {/* Default */}
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  )
}
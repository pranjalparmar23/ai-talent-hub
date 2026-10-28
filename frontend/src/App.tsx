import { Routes, Route, Navigate } from 'react-router-dom'

// Auth pages
import Login from './auth/Login'
import Register from './auth/Register'

// Candidate pages
import CandidateDashboard from './candidate/Dashboard'
import AnalysisView from './candidate/AnalysisView'
import InterviewSetup from './candidate/InterviewSetup'
import InterviewChat from './candidate/InterviewChat'
import InterviewFeedback from './candidate/InterviewFeedback'

// Recruiter pages
import RecruiterDashboard from './recruiter/Dashboard'
import CandidateRankings from './recruiter/CandidateRankings'
import CandidateDetail from './recruiter/CandidateDetail'

// Shared
import PrivateRoute from './shared/PrivateRoute'

export default function App() {
  return (
    <Routes>
      {/* Public */}
      <Route path="/login"    element={<Login />} />
      <Route path="/register" element={<Register />} />

      {/* Candidate (protected, candidate role only) */}
      <Route path="/candidate/*" element={
        <PrivateRoute role="candidate">
          <CandidateDashboard />
        </PrivateRoute>
      } />

      <Route path="/candidate/analysis" element={
        <PrivateRoute role="candidate">
          <AnalysisView />
        </PrivateRoute>
      } />

      <Route path="/candidate/interview" element={
        <PrivateRoute role="candidate">
          <InterviewSetup />
        </PrivateRoute>
      } />

      <Route path="/candidate/interview/:sessionId" element={
        <PrivateRoute role="candidate">
          <InterviewChat />
        </PrivateRoute>
      } />

      <Route path="/candidate/interview/:sessionId/feedback" element={
        <PrivateRoute role="candidate">
          <InterviewFeedback />
        </PrivateRoute>
      } />

      {/* Recruiter (protected, recruiter role only) */}
      <Route path="/recruiter" element={
        <PrivateRoute role="recruiter">
          <RecruiterDashboard />
        </PrivateRoute>
      } />

      <Route path="/recruiter/jd/:jdId" element={
        <PrivateRoute role="recruiter">
          <CandidateRankings />
        </PrivateRoute>
      } />

      <Route path="/recruiter/jd/:jdId/candidate/:candidateId" element={
        <PrivateRoute role="recruiter">
          <CandidateDetail />
        </PrivateRoute>
      } />

      {/* Unknown recruiter URL → recruiter home (the guard handles the role) */}
      <Route path="/recruiter/*" element={<Navigate to="/recruiter" replace />} />

      {/* Default */}
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  )
}
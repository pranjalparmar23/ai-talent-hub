import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Loader2,
  LogOut,
  RefreshCw,
  Users,
} from 'lucide-react'
import {
  recruiterAPI,
  type CandidateRankingItem,
  type CandidateRankingResponse,
} from '../services/api'
import { errorMessage, errorStatus } from '../shared/errors'

const SERIF = '"Instrument Serif", "Iowan Old Style", Georgia, serif'
const TOP_N_OPTIONS: { label: string; value: number | undefined }[] = [
  { label: 'All candidates', value: undefined },
  { label: 'Top 5', value: 5 },
  { label: 'Top 10', value: 10 },
  { label: 'Top 25', value: 25 },
  { label: 'Top 50', value: 50 },
  { label: 'Top 100', value: 100 },
]

export default function CandidateRankings() {
  const { jdId = '' } = useParams<{ jdId: string }>()
  const navigate = useNavigate()
  const [topN, setTopN] = useState<number | undefined>(undefined)
  const [data, setData] = useState<CandidateRankingResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  // Ranking is a POST that runs the pipeline; re-run when the role, the
  // "top N" choice or the reload button changes. `cancelled` drops stale
  // responses (React StrictMode double-invokes effects in development).
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    recruiterAPI
      .rankCandidates(jdId, topN)
      .then((res) => {
        if (!cancelled) setData(res.data)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setData(null)
        setError(rankingError(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [jdId, topN, reloadKey])

  const reload = useCallback(() => setReloadKey((k) => k + 1), [])

  function handleLogout() {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('user_role')
    navigate('/login')
  }

  const candidates = data?.ranked_candidates ?? []

  return (
    <div className="min-h-screen bg-[#0B1220] text-[#E7ECF3] font-sans">
      {/* ── Nav ─────────────────────────────────────────────── */}
      <nav className="border-b border-white/[0.06] px-8 py-4 flex items-center justify-between">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2 text-sm">
            <span className="text-[#7CFFC4]">◆</span>
            <span className="font-medium tracking-tight">Talent Hub</span>
          </div>
          <Link
            to="/recruiter"
            className="text-xs text-[#8996AD] hover:text-[#7CFFC4] flex items-center gap-1.5 transition-colors"
          >
            <ArrowLeft size={14} />
            All roles
          </Link>
        </div>
        <button
          onClick={handleLogout}
          className="text-xs text-[#8996AD] hover:text-[#E7ECF3] flex items-center gap-1.5 transition-colors"
        >
          <LogOut size={14} />
          Sign out
        </button>
      </nav>

      {/* ── Header ──────────────────────────────────────────── */}
      <section className="px-8 py-14 border-b border-white/[0.06]">
        <p className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-6">
          Candidate ranking
        </p>
        <h1
          className="text-4xl md:text-5xl leading-tight tracking-tight text-[#E7ECF3]"
          style={{ fontFamily: SERIF }}
        >
          {data ? data.jd_title || 'Untitled JD' : loading ? 'Ranking candidates…' : 'Ranking'}
        </h1>
        {data && !loading && (
          <p className="mt-4 text-sm text-[#8996AD]">
            <span className="text-[#7CFFC4]">→</span> Showing {candidates.length} of{' '}
            {data.total_candidates} {data.total_candidates === 1 ? 'candidate' : 'candidates'}
          </p>
        )}
      </section>

      {/* ── Controls + list ─────────────────────────────────── */}
      <section className="p-8 max-w-5xl">
        <div className="flex items-center justify-between gap-4 mb-6">
          <label className="flex items-center gap-3 text-[10px] tracking-[0.2em] uppercase text-[#8996AD]">
            Show
            <select
              value={topN ?? ''}
              onChange={(e) => setTopN(e.target.value ? Number(e.target.value) : undefined)}
              disabled={loading}
              className="bg-[#141B2D] border border-white/[0.08] rounded px-2.5 py-1.5 text-xs normal-case tracking-normal text-[#E7ECF3] focus:outline-none focus:border-[#7CFFC4]/50 disabled:opacity-50"
            >
              {TOP_N_OPTIONS.map((o) => (
                <option key={o.label} value={o.value ?? ''}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={reload}
            disabled={loading}
            className="text-xs text-[#8996AD] hover:text-[#7CFFC4] flex items-center gap-1.5 transition-colors disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            Re-rank
          </button>
        </div>

        {loading && !data ? (
          <ListSkeleton />
        ) : error ? (
          <ErrorState message={error} onRetry={reload} />
        ) : candidates.length === 0 ? (
          <EmptyState />
        ) : (
          <ul className={`space-y-3 transition-opacity ${loading ? 'opacity-50' : ''}`}>
            {candidates.map((c) => (
              <li key={c.candidate_id}>
                <CandidateCard jdId={jdId} candidate={c} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Candidate card
// ═════════════════════════════════════════════════════════════

const MAX_MATCHING_CHIPS = 5
const MAX_MISSING_CHIPS = 4

function CandidateCard({
  jdId,
  candidate: c,
}: {
  jdId: string
  candidate: CandidateRankingItem
}) {
  const scored = c.match_score !== null
  const matching = c.matching_skills.slice(0, MAX_MATCHING_CHIPS)
  const missing = c.missing_skills.slice(0, MAX_MISSING_CHIPS)

  return (
    <Link
      to={`/recruiter/jd/${jdId}/candidate/${c.candidate_id}`}
      state={{ name: c.name }}
      className="group block p-5 rounded border border-white/[0.06] hover:border-[#7CFFC4]/40 hover:bg-[#7CFFC4]/[0.03] transition-all"
    >
      <div className="flex items-start gap-5">
        {/* Rank */}
        <div
          className="w-10 shrink-0 text-3xl leading-none text-[#8996AD] text-center"
          style={{ fontFamily: SERIF }}
          aria-label={c.rank !== null ? `Rank ${c.rank}` : 'Not ranked'}
        >
          {c.rank ?? '—'}
        </div>

        {/* Main */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-[#E7ECF3] truncate">
              {c.name || 'Unnamed candidate'}
            </span>
            {!scored && (
              <span className="text-[10px] tracking-[0.15em] uppercase text-[#FFC857] border border-[#FFC857]/30 rounded px-1.5 py-0.5">
                Not scored
              </span>
            )}
          </div>

          {c.summary && (
            <p className="mt-1.5 text-xs text-[#8996AD] leading-relaxed line-clamp-2">
              {c.summary}
            </p>
          )}

          {scored && (
            <div className="mt-4 grid grid-cols-3 gap-4 max-w-md">
              <MiniBar label="Skills" value={c.skill_match} />
              <MiniBar label="Experience" value={c.experience_match} />
              <MiniBar
                label="Semantic"
                value={c.semantic_available ? c.semantic_match : null}
              />
            </div>
          )}

          {(matching.length > 0 || missing.length > 0) && (
            <div className="mt-4 flex flex-wrap gap-1.5">
              {matching.map((s) => (
                <Chip key={`m-${s}`} tone="match">
                  {s}
                </Chip>
              ))}
              {c.matching_skills.length > MAX_MATCHING_CHIPS && (
                <Chip tone="more">+{c.matching_skills.length - MAX_MATCHING_CHIPS}</Chip>
              )}
              {missing.map((s) => (
                <Chip key={`x-${s}`} tone="missing">
                  {s}
                </Chip>
              ))}
              {c.missing_skills.length > MAX_MISSING_CHIPS && (
                <Chip tone="more">+{c.missing_skills.length - MAX_MISSING_CHIPS}</Chip>
              )}
            </div>
          )}
        </div>

        {/* Scores */}
        <div className="shrink-0 text-right">
          <div
            className="text-4xl leading-none"
            style={{ fontFamily: SERIF, color: scoreColor(c.match_score) }}
          >
            {c.match_score !== null ? Math.round(c.match_score) : '—'}
          </div>
          <div className="text-[9px] tracking-[0.15em] uppercase text-[#8996AD] mt-1">
            Match
          </div>
          <div className="mt-3 text-xs text-[#E7ECF3]">
            {c.interview_score !== null ? (
              <>
                {c.interview_score.toFixed(1)}
                <span className="text-[#8996AD]">/10</span>
              </>
            ) : (
              <span className="text-[#8996AD]">—</span>
            )}
          </div>
          <div className="text-[9px] tracking-[0.15em] uppercase text-[#8996AD] mt-0.5">
            {c.interview_score !== null ? 'Interview' : 'No interview'}
          </div>
          <ArrowRight
            size={14}
            className="mt-3 ml-auto text-[#8996AD] group-hover:text-[#7CFFC4] transition-colors"
          />
        </div>
      </div>
    </Link>
  )
}

function MiniBar({ label, value }: { label: string; value: number | null }) {
  const pct = value === null ? 0 : Math.max(0, Math.min(100, value))
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <span className="text-[9px] tracking-[0.15em] uppercase text-[#8996AD]">{label}</span>
        <span className="text-[10px] text-[#8996AD]">
          {value === null ? 'n/a' : Math.round(pct)}
        </span>
      </div>
      <div className="h-1 rounded bg-white/[0.06] overflow-hidden">
        <div
          className="h-full rounded"
          style={{ width: `${pct}%`, backgroundColor: scoreColor(value) }}
        />
      </div>
    </div>
  )
}

function Chip({
  tone,
  children,
}: {
  tone: 'match' | 'missing' | 'more'
  children: React.ReactNode
}) {
  const styles = {
    match: 'text-[#7CFFC4] border-[#7CFFC4]/25 bg-[#7CFFC4]/[0.05]',
    missing: 'text-[#FF7A7A] border-[#FF7A7A]/25 bg-[#FF7A7A]/[0.05] line-through decoration-[#FF7A7A]/40',
    more: 'text-[#8996AD] border-white/[0.08]',
  }[tone]
  return <span className={`text-[11px] px-2 py-0.5 rounded border ${styles}`}>{children}</span>
}

// ═════════════════════════════════════════════════════════════
// States
// ═════════════════════════════════════════════════════════════

function EmptyState() {
  return (
    <div className="border border-dashed border-white/[0.08] rounded p-8 text-center">
      <div className="text-[#8996AD] flex justify-center mb-3">
        <Users size={20} />
      </div>
      <div className="text-sm font-medium text-[#E7ECF3] mb-1">No candidates to rank yet</div>
      <div className="text-xs text-[#8996AD]">
        Candidates appear here once they have uploaded a resume that was parsed successfully.
      </div>
    </div>
  )
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="border border-[#FF7A7A]/30 bg-[#FF7A7A]/[0.04] rounded p-6 flex items-center justify-between gap-4">
      <div className="flex items-center gap-2 text-sm text-[#FF7A7A]">
        <AlertCircle size={16} />
        {message}
      </div>
      <button
        onClick={onRetry}
        className="text-xs bg-white/[0.04] hover:bg-white/[0.08] text-[#E7ECF3] px-3 py-1.5 rounded border border-white/[0.08] transition-colors"
      >
        Try again
      </button>
    </div>
  )
}

function ListSkeleton() {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-xs text-[#8996AD] mb-2">
        <Loader2 size={14} className="animate-spin" />
        Scoring resumes against this role…
      </div>
      {[1, 2, 3].map((i) => (
        <div key={i} className="p-5 rounded border border-white/[0.06] animate-pulse">
          <div className="h-4 w-40 bg-white/[0.06] rounded mb-3" />
          <div className="h-3 w-3/4 bg-white/[0.06] rounded mb-2" />
          <div className="h-3 w-1/2 bg-white/[0.06] rounded" />
        </div>
      ))}
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Helpers
// ═════════════════════════════════════════════════════════════

/** mint ≥ 75 (HIRE band), amber ≥ 50 (CONSIDER band), coral below. */
function scoreColor(score: number | null): string {
  if (score === null) return '#8996AD'
  if (score >= 75) return '#7CFFC4'
  if (score >= 50) return '#FFC857'
  return '#FF7A7A'
}

function rankingError(err: unknown): string {
  switch (errorStatus(err)) {
    case 404:
      return 'This job description was not found.'
    case 403:
      return 'Only recruiter accounts can rank candidates.'
    case 429:
      return 'Too many requests. Wait a minute and try again.'
    default:
      return errorMessage(err, "Couldn't rank candidates. Please try again.")
  }
}
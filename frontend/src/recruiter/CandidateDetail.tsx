import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Info,
  Loader2,
  LogOut,
  ShieldAlert,
} from 'lucide-react'
import {
  recruiterAPI,
  type HiringRecommendationResponse,
  type HiringVerdict,
  type RecruiterQuestion,
  type RecruiterQuestionsResponse,
} from '../services/api'
import { errorMessage, errorStatus } from '../shared/errors'

const SERIF = '"Instrument Serif", "Iowan Old Style", Georgia, serif'

const VERDICT_STYLE: Record<HiringVerdict, { color: string; blurb: string }> = {
  HIRE: { color: '#7CFFC4', blurb: 'Strong fit — move forward.' },
  CONSIDER: { color: '#FFC857', blurb: 'Promising — worth a closer look.' },
  REJECT: { color: '#FF7A7A', blurb: 'Weak fit for this role.' },
}

const FLAG_LABELS: Record<string, string> = {
  no_interview: 'No mock interview taken',
  no_match_score: 'Resume could not be scored',
  downgraded_weak_interview: 'Downgraded: weak interview',
  insufficient_data: 'Not enough data',
}

type Async<T> =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: T }

export default function CandidateDetail() {
  const { jdId = '', candidateId = '' } = useParams<{ jdId: string; candidateId: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  // Passed by the rankings page; absent after a refresh or a direct visit.
  const name = (location.state as { name?: string | null } | null)?.name || null

  const [rec, setRec] = useState<Async<HiringRecommendationResponse>>({ status: 'loading' })
  const [qs, setQs] = useState<Async<RecruiterQuestionsResponse>>({ status: 'loading' })
  const [recKey, setRecKey] = useState(0)
  const [qsKey, setQsKey] = useState(0)

  // The two sections are independent (and both can take a few seconds because
  // they call the LLM), so they load and fail separately.
  useEffect(() => {
    let cancelled = false
    setRec({ status: 'loading' })
    recruiterAPI
      .getRecommendation(jdId, candidateId)
      .then((res) => !cancelled && setRec({ status: 'ready', data: res.data }))
      .catch((err: unknown) => !cancelled && setRec({ status: 'error', message: loadError(err, 'recommendation') }))
    return () => {
      cancelled = true
    }
  }, [jdId, candidateId, recKey])

  useEffect(() => {
    let cancelled = false
    setQs({ status: 'loading' })
    recruiterAPI
      .getQuestions(jdId, candidateId)
      .then((res) => !cancelled && setQs({ status: 'ready', data: res.data }))
      .catch((err: unknown) => !cancelled && setQs({ status: 'error', message: loadError(err, 'questions') }))
    return () => {
      cancelled = true
    }
  }, [jdId, candidateId, qsKey])

  function handleLogout() {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('user_role')
    navigate('/login')
  }

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
            to={`/recruiter/jd/${jdId}`}
            className="text-xs text-[#8996AD] hover:text-[#7CFFC4] flex items-center gap-1.5 transition-colors"
          >
            <ArrowLeft size={14} />
            Back to ranking
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
          Candidate review
        </p>
        <h1
          className="text-4xl md:text-5xl leading-tight tracking-tight text-[#E7ECF3]"
          style={{ fontFamily: SERIF }}
        >
          {name ?? 'Candidate'}
        </h1>
        {!name && (
          <p className="mt-3 text-xs text-[#8996AD]">ID {candidateId.slice(0, 8)}…</p>
        )}
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-2 divide-y lg:divide-y-0 lg:divide-x divide-white/[0.06]">
        {/* ── Recommendation ──────────────────────────────── */}
        <section className="p-8">
          <Eyebrow>Hiring recommendation</Eyebrow>
          {rec.status === 'loading' ? (
            <Loading label="Weighing the evidence…" />
          ) : rec.status === 'error' ? (
            <ErrorBox message={rec.message} onRetry={() => setRecKey((k) => k + 1)} />
          ) : (
            <RecommendationCard rec={rec.data} />
          )}
        </section>

        {/* ── Questions ───────────────────────────────────── */}
        <section className="p-8">
          <Eyebrow>Suggested interview questions</Eyebrow>
          {qs.status === 'loading' ? (
            <Loading label="Writing questions tailored to this candidate…" />
          ) : qs.status === 'error' ? (
            <ErrorBox message={qs.message} onRetry={() => setQsKey((k) => k + 1)} />
          ) : qs.data.parse_error || qs.data.questions.length === 0 ? (
            <ErrorBox
              message="Questions couldn't be generated this time."
              onRetry={() => setQsKey((k) => k + 1)}
            />
          ) : (
            <ol className="space-y-4">
              {qs.data.questions.map((q, i) => (
                <QuestionCard key={i} index={i + 1} q={q} />
              ))}
            </ol>
          )}
        </section>
      </div>
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Recommendation
// ═════════════════════════════════════════════════════════════

function RecommendationCard({ rec }: { rec: HiringRecommendationResponse }) {
  const style = VERDICT_STYLE[rec.recommendation]

  return (
    <div>
      <div className="flex items-end justify-between gap-4">
        <div>
          <div
            className="text-6xl leading-none tracking-tight"
            style={{ fontFamily: SERIF, color: style.color }}
          >
            {rec.recommendation}
          </div>
          <p className="mt-2 text-sm text-[#8996AD]">{style.blurb}</p>
        </div>
        <div className="text-right shrink-0">
          <div className="text-2xl leading-none text-[#E7ECF3]" style={{ fontFamily: SERIF }}>
            {rec.confidence}%
          </div>
          <div className="text-[9px] tracking-[0.15em] uppercase text-[#8996AD] mt-1">
            Confidence
          </div>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-3 gap-px bg-white/[0.06] border border-white/[0.06] rounded overflow-hidden">
        <Stat label="Combined" value={fmtScore(rec.combined_score)} />
        <Stat label="Resume match" value={fmtScore(rec.match_score)} />
        <Stat
          label="Interview"
          value={rec.interview_score !== null ? rec.interview_score.toFixed(1) : '—'}
          suffix={rec.interview_score !== null ? '/10' : undefined}
        />
      </div>

      {rec.flags.length > 0 && (
        <div className="mt-5 flex flex-wrap gap-1.5">
          {rec.flags.map((f) => (
            <span
              key={f}
              className="text-[11px] px-2 py-0.5 rounded border text-[#FFC857] border-[#FFC857]/30 bg-[#FFC857]/[0.05]"
            >
              {FLAG_LABELS[f] ?? f.replace(/_/g, ' ')}
            </span>
          ))}
        </div>
      )}

      {rec.reasoning && (
        <p className="mt-6 text-sm leading-relaxed text-[#E7ECF3]/90">{rec.reasoning}</p>
      )}

      <PointList title="Strengths" items={rec.strengths} tone="good" />
      <PointList title="Risks" items={rec.risks} tone="bad" />

      <div className="mt-8 flex items-start gap-2 text-[11px] leading-relaxed text-[#8996AD]/80">
        <Info size={13} className="mt-0.5 shrink-0" />
        <span>
          {rec.reasoning_source === 'template'
            ? 'The verdict comes from fixed scoring rules; this explanation is a standard template. '
            : 'The verdict comes from fixed scoring rules; the explanation was written by AI. '}
          This is decision support — a person makes the final call.
        </span>
      </div>
    </div>
  )
}

function Stat({ label, value, suffix }: { label: string; value: string; suffix?: string }) {
  return (
    <div className="bg-[#0B1220] px-4 py-3">
      <div className="text-xl leading-none text-[#E7ECF3]" style={{ fontFamily: SERIF }}>
        {value}
        {suffix && <span className="text-xs text-[#8996AD]">{suffix}</span>}
      </div>
      <div className="text-[9px] tracking-[0.15em] uppercase text-[#8996AD] mt-1.5">{label}</div>
    </div>
  )
}

function PointList({
  title,
  items,
  tone,
}: {
  title: string
  items: string[]
  tone: 'good' | 'bad'
}) {
  if (items.length === 0) return null
  const color = tone === 'good' ? '#7CFFC4' : '#FF7A7A'
  const Icon = tone === 'good' ? CheckCircle2 : ShieldAlert
  return (
    <div className="mt-6">
      <div className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-3">{title}</div>
      <ul className="space-y-2">
        {items.map((item, i) => (
          <li key={i} className="flex items-start gap-2.5 text-sm text-[#E7ECF3]/90">
            <Icon size={15} className="mt-0.5 shrink-0" style={{ color }} />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Questions
// ═════════════════════════════════════════════════════════════

const DIFFICULTY_COLOR: Record<string, string> = {
  easy: '#7CFFC4',
  medium: '#FFC857',
  hard: '#FF7A7A',
}

function QuestionCard({ index, q }: { index: number; q: RecruiterQuestion }) {
  const diffColor = DIFFICULTY_COLOR[q.difficulty] ?? '#8996AD'
  return (
    <li className="p-4 rounded border border-white/[0.06]">
      <div className="flex items-center gap-2 mb-2.5">
        <span className="text-xs text-[#8996AD]" style={{ fontFamily: SERIF }}>
          {String(index).padStart(2, '0')}
        </span>
        <span className="text-[10px] tracking-[0.15em] uppercase text-[#8996AD] border border-white/[0.08] rounded px-1.5 py-0.5">
          {q.category.replace(/-/g, ' ')}
        </span>
        <span
          className="text-[10px] tracking-[0.15em] uppercase rounded px-1.5 py-0.5 border"
          style={{ color: diffColor, borderColor: `${diffColor}40` }}
        >
          {q.difficulty}
        </span>
      </div>
      <p className="text-sm leading-relaxed text-[#E7ECF3]">{q.question}</p>
      {q.rationale && (
        <p className="mt-2.5 text-xs leading-relaxed text-[#8996AD]">
          <span className="text-[#7CFFC4]">Why ask: </span>
          {q.rationale}
        </p>
      )}
    </li>
  )
}

// ═════════════════════════════════════════════════════════════
// Small pieces
// ═════════════════════════════════════════════════════════════

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-6">{children}</div>
  )
}

function Loading({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2.5 text-sm text-[#8996AD]">
      <Loader2 size={16} className="animate-spin" />
      {label}
    </div>
  )
}

function ErrorBox({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="border border-[#FF7A7A]/30 bg-[#FF7A7A]/[0.04] rounded p-5 flex items-center justify-between gap-4">
      <div className="flex items-center gap-2 text-sm text-[#FF7A7A]">
        <AlertCircle size={16} className="shrink-0" />
        {message}
      </div>
      <button
        onClick={onRetry}
        className="shrink-0 text-xs bg-white/[0.04] hover:bg-white/[0.08] text-[#E7ECF3] px-3 py-1.5 rounded border border-white/[0.08] transition-colors"
      >
        Try again
      </button>
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Helpers
// ═════════════════════════════════════════════════════════════

function fmtScore(score: number | null): string {
  return score === null ? '—' : String(Math.round(score))
}

function loadError(err: unknown, what: string): string {
  switch (errorStatus(err)) {
    case 404:
      return 'This candidate or job description was not found.'
    case 403:
      return 'Only recruiter accounts can view this.'
    case 429:
      return 'Too many requests. Wait a minute and try again.'
    default:
      return errorMessage(err, `Couldn't load the ${what}. Please try again.`)
  }
}
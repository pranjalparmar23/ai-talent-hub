import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import { ArrowLeft, Check, Loader2, RotateCcw, TrendingUp } from 'lucide-react'
import { candidateAPI, type InterviewFeedback as Feedback } from '../services/api'
import { errorMessage } from '../shared/errors'

const SERIF = { fontFamily: '"Instrument Serif", "Iowan Old Style", Georgia, serif' }

// Display order + labels. Keys match the backend EvaluationAgent dimensions.
const DIMENSIONS: { key: string; label: string; short: string }[] = [
  { key: 'communication', label: 'Communication', short: 'Comm.' },
  { key: 'technical_knowledge', label: 'Technical knowledge', short: 'Technical' },
  { key: 'problem_solving', label: 'Problem solving', short: 'Problem solving' },
  { key: 'confidence', label: 'Confidence', short: 'Confidence' },
]

export default function InterviewFeedback() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)

  const load = useCallback(async () => {
    if (!sessionId) return
    setLoading(true)
    setFailed(false)
    try {
      const { data } = await candidateAPI.getInterviewFeedback(sessionId)
      if (data.parse_error) {
        setFailed(true)
      } else {
        setFeedback(data)
      }
    } catch (err: unknown) {
      toast.error(errorMessage(err, "Couldn't generate your feedback"))
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  useEffect(() => {
    void load()
  }, [load])

  if (loading) {
    return (
      <div className="min-h-screen bg-[#0B1220] text-[#8996AD] flex flex-col items-center justify-center gap-4">
        <Loader2 size={24} className="animate-spin text-[#7CFFC4]" />
        <p className="text-sm">Reviewing your interview…</p>
        <p className="text-xs text-[#8996AD]/60">This usually takes 5–10 seconds</p>
      </div>
    )
  }

  if (failed || !feedback) {
    return (
      <div className="min-h-screen bg-[#0B1220] text-[#E7ECF3] flex flex-col items-center justify-center gap-5 px-8 text-center">
        <h1 className="text-3xl" style={SERIF}>
          We couldn't score this one.
        </h1>
        <p className="text-sm text-[#8996AD] max-w-md">
          The review didn't come back in a usable form. Your answers are saved — try again.
        </p>
        <div className="flex gap-3">
          <button
            onClick={() => void load()}
            className="bg-[#7CFFC4] text-[#0B1220] px-5 py-2 rounded font-medium text-sm flex items-center gap-2 hover:bg-[#5DE9A8] transition-colors"
          >
            <RotateCcw size={14} />
            Try again
          </button>
          <button
            onClick={() => navigate(`/candidate/interview/${sessionId}`)}
            className="text-sm text-[#8996AD] hover:text-[#E7ECF3] px-3 transition-colors"
          >
            Back to interview
          </button>
        </div>
      </div>
    )
  }

  const values = DIMENSIONS.map((d) => feedback.scores[d.key] ?? 0)

  return (
    <div className="min-h-screen bg-[#0B1220] text-[#E7ECF3] font-sans pb-16">
      <nav className="border-b border-white/[0.06] px-8 py-4 flex items-center justify-between">
        <button
          onClick={() => navigate('/candidate')}
          className="text-xs text-[#8996AD] hover:text-[#E7ECF3] flex items-center gap-1.5 transition-colors"
        >
          <ArrowLeft size={14} />
          Workspace
        </button>
        <button
          onClick={() => navigate('/candidate/interview')}
          className="text-xs text-[#8996AD] hover:text-[#7CFFC4] flex items-center gap-1.5 transition-colors"
        >
          <RotateCcw size={14} />
          Practise again
        </button>
      </nav>

      {/* ── Overall ───────────────────────────────────────── */}
      <section className="px-8 py-16 border-b border-white/[0.06] grid grid-cols-1 md:grid-cols-[auto_1fr] gap-10 items-center">
        <div>
          <p className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-4">
            Overall score
          </p>
          <div className="flex items-baseline gap-2">
            <span
              className="text-8xl leading-none"
              style={{ ...SERIF, color: scoreColor(feedback.overall_score) }}
            >
              {feedback.overall_score.toFixed(1)}
            </span>
            <span className="text-2xl text-[#8996AD]" style={SERIF}>
              / 10
            </span>
          </div>
          <p className="mt-3 text-xs" style={{ color: scoreColor(feedback.overall_score) }}>
            {verdict(feedback.overall_score)}
          </p>
        </div>
        {feedback.summary && (
          <p className="text-xl md:text-2xl leading-snug text-[#E7ECF3]/90 max-w-2xl" style={SERIF}>
            {feedback.summary}
          </p>
        )}
      </section>

      {/* ── Radar + score cards ───────────────────────────── */}
      <section className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-white/[0.06] border-b border-white/[0.06]">
        <div className="p-8 flex items-center justify-center">
          <Radar labels={DIMENSIONS.map((d) => d.short)} values={values} />
        </div>
        <div className="p-8 space-y-5">
          <p className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD]">Breakdown</p>
          {DIMENSIONS.map((d, i) => (
            <ScoreRow key={d.key} label={d.label} value={values[i]} />
          ))}
        </div>
      </section>

      {/* ── Strengths / improvements ──────────────────────── */}
      <section className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-white/[0.06]">
        <ListColumn
          eyebrow="What worked"
          icon={<Check size={14} className="text-[#7CFFC4]" />}
          items={feedback.strengths}
          empty="No standout strengths were identified."
        />
        <ListColumn
          eyebrow="Work on next"
          icon={<TrendingUp size={14} className="text-[#FFC857]" />}
          items={feedback.improvements}
          empty="No specific improvements were identified."
        />
      </section>
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Sub-components
// ═════════════════════════════════════════════════════════════

function ScoreRow({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="flex items-baseline justify-between mb-2">
        <span className="text-sm text-[#E7ECF3]">{label}</span>
        <span className="text-lg leading-none" style={{ ...SERIF, color: scoreColor(value) }}>
          {value.toFixed(1)}
        </span>
      </div>
      <div className="h-1 bg-white/[0.06] rounded overflow-hidden">
        <div
          className="h-1 rounded transition-all duration-700"
          style={{ width: `${(value / 10) * 100}%`, background: scoreColor(value) }}
        />
      </div>
    </div>
  )
}

function ListColumn({
  eyebrow,
  icon,
  items,
  empty,
}: {
  eyebrow: string
  icon: React.ReactNode
  items: string[]
  empty: string
}) {
  return (
    <div className="p-8">
      <p className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-5">{eyebrow}</p>
      {items.length === 0 ? (
        <p className="text-sm text-[#8996AD]">{empty}</p>
      ) : (
        <ul className="space-y-4">
          {items.map((item, i) => (
            <li key={i} className="flex gap-3 text-sm leading-relaxed text-[#E7ECF3]/90">
              <span className="mt-1 shrink-0">{icon}</span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Dependency-free SVG radar chart. Values are 0–10, one axis per label. */
function Radar({ labels, values }: { labels: string[]; values: number[] }) {
  const width = 400
  const height = 320
  const cx = width / 2
  const cy = height / 2
  const radius = 100
  const n = labels.length

  const point = (i: number, v: number): [number, number] => {
    const angle = (Math.PI * 2 * i) / n - Math.PI / 2
    const r = (Math.max(0, Math.min(10, v)) / 10) * radius
    return [cx + r * Math.cos(angle), cy + r * Math.sin(angle)]
  }
  const polygon = (v: number) =>
    labels.map((_, i) => point(i, v).join(',')).join(' ')
  const shape = values.map((v, i) => point(i, v).join(',')).join(' ')

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="w-full max-w-[400px]"
      role="img"
      aria-label={`Radar chart: ${labels.map((l, i) => `${l} ${values[i]}`).join(', ')}`}
    >
      {[2.5, 5, 7.5, 10].map((ring) => (
        <polygon
          key={ring}
          points={polygon(ring)}
          fill="none"
          stroke="rgba(255,255,255,0.08)"
          strokeWidth={1}
        />
      ))}
      {labels.map((_, i) => {
        const [x, y] = point(i, 10)
        return <line key={i} x1={cx} y1={cy} x2={x} y2={y} stroke="rgba(255,255,255,0.08)" />
      })}
      <polygon
        points={shape}
        fill="rgba(124,255,196,0.14)"
        stroke="#7CFFC4"
        strokeWidth={1.5}
        strokeLinejoin="round"
      />
      {values.map((v, i) => {
        const [x, y] = point(i, v)
        return <circle key={i} cx={x} cy={y} r={3} fill="#7CFFC4" />
      })}
      {labels.map((label, i) => {
        const [x, y] = point(i, 12.4)
        const anchor = Math.abs(x - cx) < 4 ? 'middle' : x > cx ? 'start' : 'end'
        return (
          <text
            key={label}
            x={x}
            y={y}
            textAnchor={anchor}
            dominantBaseline="middle"
            fontSize={11}
            fill="#8996AD"
          >
            {label}
          </text>
        )
      })}
    </svg>
  )
}

// ═════════════════════════════════════════════════════════════
// Helpers
// ═════════════════════════════════════════════════════════════

function scoreColor(score: number): string {
  if (score >= 7.5) return '#7CFFC4'
  if (score >= 5) return '#FFC857'
  return '#FF7A7A'
}

function verdict(score: number): string {
  if (score >= 8.5) return 'Offer-ready performance'
  if (score >= 7) return 'Strong — a few things to sharpen'
  if (score >= 5) return 'Promising, with clear room to grow'
  return 'Needs focused practice'
}

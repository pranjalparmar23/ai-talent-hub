import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import {
  ArrowLeft,
  Check,
  Loader2,
  Target,
  Layers,
  Map,
  AlertCircle,
} from 'lucide-react'
import {
  candidateAPI,
  type ATSScoreResponse,
  type SkillGapResponse,
  type LearningPlanResponse,
} from '../services/api'

type Section = 'ats' | 'gap' | 'plan'

export default function AnalysisView() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const resumeId = params.get('resume')
  const jdId = params.get('jd')

  const [active, setActive] = useState<Section>('ats')
  const [ats, setAts] = useState<ATSScoreResponse | null>(null)
  const [gap, setGap] = useState<SkillGapResponse | null>(null)
  const [plan, setPlan] = useState<LearningPlanResponse | null>(null)
  const [loading, setLoading] = useState<Record<Section, boolean>>({
    ats: false,
    gap: false,
    plan: false,
  })

  useEffect(() => {
    if (!resumeId || !jdId) {
      navigate('/candidate')
      return
    }
    // Kick off ATS first since it's the "hero" number
    void loadSection('ats')
  }, [resumeId, jdId])

  async function loadSection(section: Section) {
    if (!resumeId || !jdId) return
    setLoading((s) => ({ ...s, [section]: true }))
    try {
      if (section === 'ats' && !ats) {
        const res = await candidateAPI.getATSScore(resumeId, jdId)
        setAts(res.data)
      } else if (section === 'gap' && !gap) {
        const res = await candidateAPI.getSkillGap(resumeId, jdId)
        setGap(res.data)
      } else if (section === 'plan' && !plan) {
        const res = await candidateAPI.getLearningPlan(resumeId, jdId)
        setPlan(res.data)
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'Analysis failed'
      toast.error(detail)
    } finally {
      setLoading((s) => ({ ...s, [section]: false }))
    }
  }

  function handleTabClick(section: Section) {
    setActive(section)
    void loadSection(section)
  }

  return (
    <div className="min-h-screen bg-[#0B1220] text-[#E7ECF3]">
      {/* ── Nav ─────────────────────────────────────────────── */}
      <nav className="border-b border-white/[0.06] px-8 py-4 flex items-center gap-4">
        <button
          onClick={() => navigate('/candidate')}
          className="text-[#8996AD] hover:text-[#E7ECF3] flex items-center gap-1.5 text-sm transition-colors"
        >
          <ArrowLeft size={14} />
          Workspace
        </button>
      </nav>

      {/* ── Header ──────────────────────────────────────────── */}
      <section className="px-8 pt-12 pb-6 border-b border-white/[0.06]">
        <p className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-4">
          Analysis
        </p>
        <h1
          className="text-4xl md:text-5xl leading-tight tracking-tight"
          style={{ fontFamily: '"Instrument Serif", Georgia, serif' }}
        >
          How this resume fits this role.
        </h1>
      </section>

      {/* ── Section tabs ────────────────────────────────────── */}
      <div className="px-8 border-b border-white/[0.06] flex gap-6">
        <Tab
          label="ATS score"
          icon={<Target size={14} />}
          active={active === 'ats'}
          onClick={() => handleTabClick('ats')}
        />
        <Tab
          label="Skill gap"
          icon={<Layers size={14} />}
          active={active === 'gap'}
          onClick={() => handleTabClick('gap')}
        />
        <Tab
          label="Learning plan"
          icon={<Map size={14} />}
          active={active === 'plan'}
          onClick={() => handleTabClick('plan')}
        />
      </div>

      {/* ── Section content ─────────────────────────────────── */}
      <div className="px-8 py-10">
        {active === 'ats' && (
          <ATSPanel data={ats} loading={loading.ats} />
        )}
        {active === 'gap' && (
          <GapPanel data={gap} loading={loading.gap} />
        )}
        {active === 'plan' && (
          <PlanPanel data={plan} loading={loading.plan} />
        )}
      </div>
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Tab
// ═════════════════════════════════════════════════════════════

function Tab({
  label,
  icon,
  active,
  onClick,
}: {
  label: string
  icon: React.ReactNode
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`py-4 flex items-center gap-2 text-sm border-b-2 transition-colors ${
        active
          ? 'border-[#7CFFC4] text-[#E7ECF3]'
          : 'border-transparent text-[#8996AD] hover:text-[#E7ECF3]'
      }`}
    >
      {icon}
      {label}
    </button>
  )
}

// ═════════════════════════════════════════════════════════════
// ATS panel
// ═════════════════════════════════════════════════════════════

function ATSPanel({
  data,
  loading,
}: {
  data: ATSScoreResponse | null
  loading: boolean
}) {
  if (loading) return <PanelSkeleton lines={5} />
  if (!data) return <EmptyPanel message="Loading analysis..." />

  const scoreColor = scoreToneColor(data.ats_score)

  return (
    <div className="max-w-4xl">
      {/* Score hero */}
      <div className="grid grid-cols-1 md:grid-cols-[auto_1fr] gap-8 items-baseline mb-12">
        <div>
          <div
            className="text-[8rem] leading-none"
            style={{
              fontFamily: '"Instrument Serif", Georgia, serif',
              color: scoreColor,
            }}
          >
            {data.ats_score}
          </div>
          <div className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mt-2">
            out of 100
          </div>
        </div>
        {data.summary && (
          <p className="text-lg text-[#E7ECF3] leading-relaxed max-w-xl">
            {data.summary}
          </p>
        )}
      </div>

      {/* Keyword breakdown */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-8 mb-10">
        <KeywordList
          eyebrow="Matched"
          items={data.matching_keywords}
          tone="positive"
        />
        <KeywordList
          eyebrow="Missing"
          items={data.missing_keywords}
          tone="warning"
        />
      </div>

      {/* Recommendations */}
      {data.recommendations.length > 0 && (
        <div>
          <p className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-4">
            Recommendations
          </p>
          <ul className="space-y-3">
            {data.recommendations.map((r, i) => (
              <li key={i} className="flex gap-3 text-sm text-[#E7ECF3] leading-relaxed">
                <span className="text-[#7CFFC4] shrink-0 mt-0.5">·</span>
                {r}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Formatting issues */}
      {data.formatting_issues.length > 0 && (
        <div className="mt-10 pt-6 border-t border-white/[0.06]">
          <p className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-4">
            Formatting notes
          </p>
          <ul className="space-y-2">
            {data.formatting_issues.map((f, i) => (
              <li key={i} className="flex gap-3 text-sm text-[#8996AD]">
                <AlertCircle size={14} className="shrink-0 mt-0.5" />
                {f}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function KeywordList({
  eyebrow,
  items,
  tone,
}: {
  eyebrow: string
  items: string[]
  tone: 'positive' | 'warning'
}) {
  const colors =
    tone === 'positive'
      ? { border: 'border-[#7CFFC4]/20', text: 'text-[#7CFFC4]' }
      : { border: 'border-[#FF6B6B]/20', text: 'text-[#FF6B6B]' }

  return (
    <div>
      <p className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-3">
        {eyebrow} · {items.length}
      </p>
      {items.length === 0 ? (
        <p className="text-sm text-[#8996AD]">None</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {items.map((k, i) => (
            <span
              key={i}
              className={`text-xs px-2.5 py-1 rounded border ${colors.border} ${colors.text}`}
            >
              {k}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Skill Gap panel
// ═════════════════════════════════════════════════════════════

function GapPanel({
  data,
  loading,
}: {
  data: SkillGapResponse | null
  loading: boolean
}) {
  if (loading) return <PanelSkeleton lines={4} />
  if (!data) return <EmptyPanel message="Loading skill comparison..." />

  const total = data.matching_skills.length + data.missing_skills.length
  const matchPct =
    total === 0 ? 0 : Math.round((data.matching_skills.length / total) * 100)

  return (
    <div className="max-w-4xl">
      {/* Gap hero */}
      <div className="grid grid-cols-1 md:grid-cols-[auto_1fr] gap-8 items-baseline mb-12">
        <div>
          <div
            className="text-[8rem] leading-none text-[#E7ECF3]"
            style={{ fontFamily: '"Instrument Serif", Georgia, serif' }}
          >
            {matchPct}
            <span className="text-[#8996AD] text-4xl">%</span>
          </div>
          <div className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mt-2">
            skills matched
          </div>
        </div>
        <div className="max-w-xl">
          <p className="text-lg text-[#E7ECF3] leading-relaxed">
            You have {data.matching_skills.length} of the {total} skills this role
            asks for.
          </p>
          <p className="text-sm text-[#8996AD] mt-2">
            {data.gap_percentage}% gap — meaningful but bridgeable in 4–8 weeks.
          </p>
        </div>
      </div>

      {/* Visual bar */}
      <div className="mb-10">
        <div className="h-2 rounded-full bg-white/[0.06] overflow-hidden flex">
          <div
            className="bg-[#7CFFC4]"
            style={{ width: `${matchPct}%` }}
          />
          <div
            className="bg-[#FF6B6B]/50"
            style={{ width: `${100 - matchPct}%` }}
          />
        </div>
        <div className="flex justify-between mt-2 text-[10px] tracking-[0.15em] uppercase text-[#8996AD]">
          <span>{data.matching_skills.length} matched</span>
          <span>{data.missing_skills.length} to learn</span>
        </div>
      </div>

      {/* Skill lists */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
        <SkillList
          eyebrow="You have"
          items={data.matching_skills}
          tone="positive"
        />
        <SkillList
          eyebrow="You need"
          items={data.missing_skills}
          tone="warning"
        />
      </div>
    </div>
  )
}

function SkillList({
  eyebrow,
  items,
  tone,
}: {
  eyebrow: string
  items: string[]
  tone: 'positive' | 'warning'
}) {
  return (
    <div>
      <p className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-4">
        {eyebrow} · {items.length}
      </p>
      {items.length === 0 ? (
        <p className="text-sm text-[#8996AD]">None</p>
      ) : (
        <ul className="space-y-2">
          {items.map((s, i) => (
            <li key={i} className="flex items-center gap-2 text-sm text-[#E7ECF3]">
              {tone === 'positive' ? (
                <Check size={14} className="text-[#7CFFC4] shrink-0" />
              ) : (
                <span className="w-3.5 h-3.5 rounded-full border border-[#FF6B6B]/50 shrink-0" />
              )}
              {s}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Learning Plan panel
// ═════════════════════════════════════════════════════════════

function PlanPanel({
  data,
  loading,
}: {
  data: LearningPlanResponse | null
  loading: boolean
}) {
  if (loading) return <PanelSkeleton lines={6} />
  if (!data) return <EmptyPanel message="Building your learning plan..." />

  const totalHours = data.weeks.reduce(
    (sum, w) => sum + (w.estimated_hours || 0),
    0,
  )

  if (data.weeks.length === 0) {
    return (
      <div className="max-w-2xl">
        <p className="text-lg text-[#E7ECF3]">
          {data.summary || 'No plan available — try again in a moment.'}
        </p>
      </div>
    )
  }

  return (
    <div className="max-w-4xl">
      {/* Plan hero */}
      <div className="mb-12">
        <p className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-3">
          {data.target_role || 'Learning plan'}
        </p>
        <h2
          className="text-3xl leading-tight tracking-tight"
          style={{ fontFamily: '"Instrument Serif", Georgia, serif' }}
        >
          <span>{data.weeks.length}</span>
          <span className="text-[#8996AD] mx-2">weeks</span>
          <span>{totalHours}</span>
          <span className="text-[#8996AD] mx-2 text-2xl">hours total</span>
        </h2>
        {data.summary && (
          <p className="mt-4 text-sm text-[#8996AD] leading-relaxed max-w-2xl">
            {data.summary}
          </p>
        )}
      </div>

      {/* Weeks timeline */}
      <div className="space-y-6">
        {data.weeks.map((week) => (
          <WeekCard key={week.week} week={week} />
        ))}
      </div>
    </div>
  )
}

function WeekCard({ week }: { week: LearningPlanResponse['weeks'][number] }) {
  return (
    <div className="grid grid-cols-[auto_1fr] gap-6 pb-6 border-b border-white/[0.06] last:border-b-0">
      {/* Week number gutter */}
      <div className="pt-1">
        <div
          className="text-3xl text-[#7CFFC4] leading-none"
          style={{ fontFamily: '"Instrument Serif", Georgia, serif' }}
        >
          {String(week.week).padStart(2, '0')}
        </div>
        <div className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mt-1">
          Week
        </div>
      </div>

      {/* Content */}
      <div className="min-w-0">
        <div className="flex items-baseline justify-between gap-4 mb-2">
          <h3 className="text-lg font-medium text-[#E7ECF3]">{week.topic}</h3>
          {week.estimated_hours && (
            <span className="text-xs text-[#8996AD] shrink-0">
              {week.estimated_hours}h
            </span>
          )}
        </div>

        {week.goal && (
          <p className="text-sm text-[#8996AD] mb-4 leading-relaxed">
            {week.goal}
          </p>
        )}

        {week.tasks.length > 0 && (
          <ul className="space-y-1.5 mb-4">
            {week.tasks.map((task, i) => (
              <li
                key={i}
                className="flex gap-2 text-sm text-[#E7ECF3] leading-relaxed"
              >
                <span className="text-[#8996AD] shrink-0">·</span>
                {task}
              </li>
            ))}
          </ul>
        )}

        {week.resources.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {week.resources.map((r, i) => (
              <span
                key={i}
                className="text-[10px] tracking-[0.1em] uppercase text-[#8996AD] px-2 py-1 rounded border border-white/[0.06]"
                title={r.source || ''}
              >
                {r.title}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Shared UI
// ═════════════════════════════════════════════════════════════

function PanelSkeleton({ lines }: { lines: number }) {
  return (
    <div className="max-w-4xl space-y-6">
      <div className="flex items-center gap-3 text-[#8996AD]">
        <Loader2 size={16} className="animate-spin" />
        <span className="text-sm">Analyzing…</span>
      </div>
      <div className="space-y-3">
        {Array.from({ length: lines }).map((_, i) => (
          <div
            key={i}
            className="h-4 bg-white/[0.04] rounded animate-pulse"
            style={{ width: `${60 + Math.random() * 30}%` }}
          />
        ))}
      </div>
    </div>
  )
}

function EmptyPanel({ message }: { message: string }) {
  return (
    <div className="text-sm text-[#8996AD] flex items-center gap-2">
      <Loader2 size={14} className="animate-spin" />
      {message}
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Helpers
// ═════════════════════════════════════════════════════════════

function scoreToneColor(score: number): string {
  if (score >= 75) return '#7CFFC4'  // signal green
  if (score >= 50) return '#E7ECF3'  // neutral
  return '#FF6B6B'                    // warning coral
}
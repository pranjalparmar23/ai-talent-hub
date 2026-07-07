import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { LogOut, Plus, FileText, Briefcase, ArrowRight, Loader2 } from 'lucide-react'
import {
  candidateAPI,
  type ResumeSummary,
  type JDSummary,
} from '../services/api'
import ResumeUpload from '../candidate/ResumeUpload'

type SelectionState = {
  resumeId: string | null
  jdId: string | null
}

export default function CandidateDashboard() {
  const navigate = useNavigate()
  const [resumes, setResumes] = useState<ResumeSummary[]>([])
  const [jds, setJds] = useState<JDSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [selection, setSelection] = useState<SelectionState>({ resumeId: null, jdId: null })
  const [showUpload, setShowUpload] = useState<'resume' | 'jd' | null>(null)

  useEffect(() => {
    void refresh()
  }, [])

  async function refresh() {
    try {
      const [resumesRes, jdsRes] = await Promise.all([
        candidateAPI.listResumes(),
        candidateAPI.listJDs(),
      ])
      setResumes(resumesRes.data)
      setJds(jdsRes.data)
    } catch (err) {
      toast.error("Couldn't load your workspace")
    } finally {
      setLoading(false)
    }
  }

  function handleLogout() {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    navigate('/login')
  }

  function handleAnalyze() {
    if (!selection.resumeId || !selection.jdId) return
    navigate(
      `/candidate/analysis?resume=${selection.resumeId}&jd=${selection.jdId}`,
    )
  }

  const statusSentence = useMemo(() => buildStatus(resumes, jds), [resumes, jds])
  const canAnalyze = selection.resumeId && selection.jdId

  return (
    <div className="min-h-screen bg-[#0B1220] text-[#E7ECF3] font-sans">
      {/* ── Nav ─────────────────────────────────────────────── */}
      <nav className="border-b border-white/[0.06] px-8 py-4 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-[#7CFFC4]">◆</span>
          <span className="font-medium tracking-tight">Talent Hub</span>
        </div>
        <button
          onClick={handleLogout}
          className="text-xs text-[#8996AD] hover:text-[#E7ECF3] flex items-center gap-1.5 transition-colors"
        >
          <LogOut size={14} />
          Sign out
        </button>
      </nav>

      {/* ── Status hero ─────────────────────────────────────── */}
      <section className="px-8 py-16 border-b border-white/[0.06]">
        <p className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-6">
          Workspace
        </p>
        <h1
          className="text-4xl md:text-5xl leading-tight tracking-tight text-[#E7ECF3]"
          style={{ fontFamily: '"Instrument Serif", "Iowan Old Style", Georgia, serif' }}
        >
          {loading ? <SkeletonHero /> : statusSentence.headline}
        </h1>
        {!loading && statusSentence.action && (
          <p className="mt-4 text-sm text-[#8996AD]">
            <span className="text-[#7CFFC4]">→</span> {statusSentence.action}
          </p>
        )}
      </section>

      {/* ── Two-column workspace ────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-white/[0.06]">
        {/* Resumes column */}
        <Column
          eyebrow="Resumes"
          count={resumes.length}
          onAdd={() => setShowUpload('resume')}
          addLabel="Upload PDF"
        >
          {loading ? (
            <ColumnSkeleton />
          ) : resumes.length === 0 ? (
            <EmptyState
              icon={<FileText size={20} />}
              title="No resumes yet"
              hint="Upload a PDF to get started."
              cta="Upload PDF"
              onClick={() => setShowUpload('resume')}
            />
          ) : (
            <div className="space-y-3">
              {resumes.map((r) => (
                <ResumeCard
                  key={r.resume_id}
                  resume={r}
                  selected={selection.resumeId === r.resume_id}
                  onSelect={() =>
                    setSelection((s) => ({ ...s, resumeId: r.resume_id }))
                  }
                />
              ))}
            </div>
          )}
        </Column>

        {/* JDs column */}
        <Column
          eyebrow="Target roles"
          count={jds.length}
          onAdd={() => setShowUpload('jd')}
          addLabel="Add JD"
        >
          {loading ? (
            <ColumnSkeleton />
          ) : jds.length === 0 ? (
            <EmptyState
              icon={<Briefcase size={20} />}
              title="No target roles yet"
              hint="Paste a job description to compare against."
              cta="Add JD"
              onClick={() => setShowUpload('jd')}
            />
          ) : (
            <div className="space-y-3">
              {jds.map((j) => (
                <JDCard
                  key={j.jd_id}
                  jd={j}
                  selected={selection.jdId === j.jd_id}
                  onSelect={() =>
                    setSelection((s) => ({ ...s, jdId: j.jd_id }))
                  }
                />
              ))}
            </div>
          )}
        </Column>
      </div>

      {/* ── Analyze bar (fixed bottom when both selected) ──── */}
      {canAnalyze && (
        <div className="fixed bottom-0 inset-x-0 border-t border-white/[0.06] bg-[#141B2D]/95 backdrop-blur px-8 py-4 flex items-center justify-between">
          <div className="text-sm text-[#8996AD]">
            Ready to analyze this pairing
          </div>
          <button
            onClick={handleAnalyze}
            className="bg-[#7CFFC4] text-[#0B1220] px-5 py-2 rounded font-medium text-sm flex items-center gap-2 hover:bg-[#5DE9A8] transition-colors"
          >
            Analyze
            <ArrowRight size={16} />
          </button>
        </div>
      )}

      {/* ── Upload modal ────────────────────────────────────── */}
      {showUpload && (
        <ResumeUpload
          mode={showUpload}
          onClose={() => setShowUpload(null)}
          onSuccess={() => {
            setShowUpload(null)
            void refresh()
          }}
        />
      )}
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Sub-components
// ═════════════════════════════════════════════════════════════

function Column({
  eyebrow,
  count,
  onAdd,
  addLabel,
  children,
}: {
  eyebrow: string
  count: number
  onAdd: () => void
  addLabel: string
  children: React.ReactNode
}) {
  return (
    <div className="p-8 min-h-[400px]">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-baseline gap-3">
          <span className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD]">
            {eyebrow}
          </span>
          <span
            className="text-lg text-[#E7ECF3]"
            style={{ fontFamily: '"Instrument Serif", Georgia, serif' }}
          >
            {count}
          </span>
        </div>
        <button
          onClick={onAdd}
          className="text-xs text-[#8996AD] hover:text-[#7CFFC4] flex items-center gap-1.5 transition-colors"
        >
          <Plus size={14} />
          {addLabel}
        </button>
      </div>
      {children}
    </div>
  )
}

function ResumeCard({
  resume,
  selected,
  onSelect,
}: {
  resume: ResumeSummary
  selected: boolean
  onSelect: () => void
}) {
  return (
    <button
      onClick={onSelect}
      className={`w-full text-left p-4 rounded border transition-all ${selected
          ? 'border-[#7CFFC4] bg-[#7CFFC4]/[0.04]'
          : 'border-white/[0.06] hover:border-white/[0.12]'
        }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[10px] tracking-[0.15em] uppercase text-[#8996AD] mb-1">
            Uploaded {formatDate(resume.created_at)}
          </div>
          <div className="text-sm font-medium text-[#E7ECF3] truncate">
            {resume.name}
          </div>
        </div>
        {resume.ats_score !== null && (
          <div className="text-right shrink-0">
            <div
              className="text-2xl leading-none text-[#7CFFC4]"
              style={{ fontFamily: '"Instrument Serif", Georgia, serif' }}
            >
              {resume.ats_score}
            </div>
            <div className="text-[9px] tracking-[0.15em] uppercase text-[#8996AD] mt-0.5">
              ATS
            </div>
          </div>
        )}
      </div>
    </button>
  )
}

function JDCard({
  jd,
  selected,
  onSelect,
}: {
  jd: JDSummary
  selected: boolean
  onSelect: () => void
}) {
  return (
    <button
      onClick={onSelect}
      className={`w-full text-left p-4 rounded border transition-all ${selected
          ? 'border-[#7CFFC4] bg-[#7CFFC4]/[0.04]'
          : 'border-white/[0.06] hover:border-white/[0.12]'
        }`}
    >
      <div className="text-[10px] tracking-[0.15em] uppercase text-[#8996AD] mb-1">
        Added {formatDate(jd.created_at)}
      </div>
      <div className="text-sm font-medium text-[#E7ECF3] truncate">
        {jd.title}
      </div>
    </button>
  )
}

function EmptyState({
  icon,
  title,
  hint,
  cta,
  onClick,
}: {
  icon: React.ReactNode
  title: string
  hint: string
  cta: string
  onClick: () => void
}) {
  return (
    <div className="border border-dashed border-white/[0.08] rounded p-8 text-center">
      <div className="text-[#8996AD] flex justify-center mb-3">{icon}</div>
      <div className="text-sm font-medium text-[#E7ECF3] mb-1">{title}</div>
      <div className="text-xs text-[#8996AD] mb-4">{hint}</div>
      <button
        onClick={onClick}
        className="text-xs bg-white/[0.04] hover:bg-white/[0.08] text-[#E7ECF3] px-3 py-1.5 rounded border border-white/[0.08] transition-colors"
      >
        {cta}
      </button>
    </div>
  )
}

function SkeletonHero() {
  return (
    <div className="flex items-center gap-3">
      <Loader2 size={24} className="animate-spin text-[#8996AD]" />
      <span className="text-[#8996AD]">Loading workspace…</span>
    </div>
  )
}

function ColumnSkeleton() {
  return (
    <div className="space-y-3">
      {[1, 2].map((i) => (
        <div key={i} className="p-4 rounded border border-white/[0.06] animate-pulse">
          <div className="h-3 w-20 bg-white/[0.06] rounded mb-2" />
          <div className="h-4 w-32 bg-white/[0.06] rounded" />
        </div>
      ))}
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Helpers
// ═════════════════════════════════════════════════════════════

function formatDate(iso: string): string {
  try {
    const d = new Date(iso)
    const now = new Date()
    const diffMs = now.getTime() - d.getTime()
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))
    if (diffDays === 0) return 'today'
    if (diffDays === 1) return 'yesterday'
    if (diffDays < 7) return `${diffDays} days ago`
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  } catch {
    return ''
  }
}

function buildStatus(
  resumes: ResumeSummary[],
  jds: JDSummary[],
): { headline: string; action: string | null } {
  const rCount = resumes.length
  const jCount = jds.length

  if (rCount === 0 && jCount === 0) {
    return {
      headline: 'Set up your workspace.',
      action: 'Upload a resume and paste a target job description to begin.',
    }
  }

  if (rCount === 0) {
    return {
      headline: `${jCount} target ${plural('role', jCount)}, no resume yet.`,
      action: 'Upload your resume to run an analysis.',
    }
  }

  if (jCount === 0) {
    return {
      headline: `${rCount} ${plural('resume', rCount)}, no target roles yet.`,
      action: 'Add a job description to compare against.',
    }
  }

  return {
    headline: `${rCount} ${plural('resume', rCount)}, ${jCount} target ${plural('role', jCount)}.`,
    action: 'Select a pairing to run analysis.',
  }
}

function plural(word: string, n: number): string {
  return n === 1 ? word : `${word}s`
}
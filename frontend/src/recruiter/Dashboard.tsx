import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import {
  AlertCircle,
  ArrowRight,
  Briefcase,
  FileText,
  Loader2,
  LogOut,
  Plus,
  Upload,
  Users,
  X,
} from 'lucide-react'
import { recruiterAPI, type JDSummary } from '../services/api'
import { errorMessage } from '../shared/errors'

const MAX_BYTES = 5 * 1024 * 1024
const ACCEPT = '.pdf,.txt,.md,application/pdf,text/plain,text/markdown'
const SERIF = '"Instrument Serif", "Iowan Old Style", Georgia, serif'

export default function RecruiterDashboard() {
  const navigate = useNavigate()
  const [jds, setJds] = useState<JDSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [showUpload, setShowUpload] = useState(false)

  const refresh = useCallback(async () => {
    setLoadError(null)
    try {
      const res = await recruiterAPI.listJDs()
      setJds(res.data)
    } catch (err: unknown) {
      setLoadError(errorMessage(err, "Couldn't load your job descriptions."))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  function handleLogout() {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    localStorage.removeItem('user_role')
    navigate('/login')
  }

  // Newest first, whatever order the API returns.
  const sorted = useMemo(
    () =>
      [...jds].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      ),
    [jds],
  )

  return (
    <div className="min-h-screen bg-[#0B1220] text-[#E7ECF3] font-sans">
      {/* ── Nav ─────────────────────────────────────────────── */}
      <nav className="border-b border-white/[0.06] px-8 py-4 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-[#7CFFC4]">◆</span>
          <span className="font-medium tracking-tight">Talent Hub</span>
          <span className="ml-2 text-[10px] tracking-[0.2em] uppercase text-[#8996AD]">
            Recruiter
          </span>
        </div>
        <button
          onClick={handleLogout}
          className="text-xs text-[#8996AD] hover:text-[#E7ECF3] flex items-center gap-1.5 transition-colors"
        >
          <LogOut size={14} />
          Sign out
        </button>
      </nav>

      {/* ── Hero ────────────────────────────────────────────── */}
      <section className="px-8 py-16 border-b border-white/[0.06]">
        <p className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-6">
          Hiring
        </p>
        <h1
          className="text-4xl md:text-5xl leading-tight tracking-tight text-[#E7ECF3]"
          style={{ fontFamily: SERIF }}
        >
          {loading ? (
            <span className="flex items-center gap-3 text-[#8996AD]">
              <Loader2 size={24} className="animate-spin" />
              Loading roles…
            </span>
          ) : (
            headline(jds.length)
          )}
        </h1>
        {!loading && !loadError && (
          <p className="mt-4 text-sm text-[#8996AD]">
            <span className="text-[#7CFFC4]">→</span>{' '}
            {jds.length === 0
              ? 'Upload a job description to rank your candidates against it.'
              : 'Open a role to rank candidates, get interview questions and a hiring recommendation.'}
          </p>
        )}
      </section>

      {/* ── Roles ───────────────────────────────────────────── */}
      <section className="p-8 max-w-4xl">
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-baseline gap-3">
            <span className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD]">
              Open roles
            </span>
            <span className="text-lg text-[#E7ECF3]" style={{ fontFamily: SERIF }}>
              {jds.length}
            </span>
          </div>
          <button
            onClick={() => setShowUpload(true)}
            className="text-xs text-[#8996AD] hover:text-[#7CFFC4] flex items-center gap-1.5 transition-colors"
          >
            <Plus size={14} />
            Upload JD
          </button>
        </div>

        {loading ? (
          <ListSkeleton />
        ) : loadError ? (
          <ErrorState message={loadError} onRetry={() => void refresh()} />
        ) : sorted.length === 0 ? (
          <EmptyState onUpload={() => setShowUpload(true)} />
        ) : (
          <ul className="space-y-3">
            {sorted.map((jd) => (
              <li key={jd.jd_id}>
                <Link
                  to={`/recruiter/jd/${jd.jd_id}`}
                  className="group flex items-center justify-between gap-4 p-4 rounded border border-white/[0.06] hover:border-[#7CFFC4]/40 hover:bg-[#7CFFC4]/[0.03] transition-all"
                >
                  <div className="min-w-0">
                    <div className="text-[10px] tracking-[0.15em] uppercase text-[#8996AD] mb-1">
                      Added {formatDate(jd.created_at)}
                    </div>
                    <div className="text-sm font-medium text-[#E7ECF3] truncate">
                      {jd.title || 'Untitled JD'}
                    </div>
                  </div>
                  <span className="shrink-0 flex items-center gap-2 text-xs text-[#8996AD] group-hover:text-[#7CFFC4] transition-colors">
                    <Users size={14} />
                    Rank candidates
                    <ArrowRight size={14} />
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      {showUpload && (
        <UploadModal
          onClose={() => setShowUpload(false)}
          onSuccess={() => {
            setShowUpload(false)
            void refresh()
          }}
        />
      )}
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Upload modal
// ═════════════════════════════════════════════════════════════

function UploadModal({
  onClose,
  onSuccess,
}: {
  onClose: () => void
  onSuccess: () => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  function validateAndSet(f: File) {
    if (!isSupported(f)) {
      toast.error('Upload a PDF, .txt or .md file')
      return
    }
    if (f.size === 0) {
      toast.error('That file is empty')
      return
    }
    if (f.size > MAX_BYTES) {
      toast.error('File exceeds 5 MB limit')
      return
    }
    setFile(f)
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) validateAndSet(f)
  }

  function handleFileInput(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (f) validateAndSet(f)
    e.target.value = '' // allow re-picking the same file after clearing
  }

  async function handleUpload() {
    if (!file || uploading) return
    setUploading(true)
    try {
      const res = await recruiterAPI.uploadJD(file)
      if (res.data.parse_error) {
        toast('Added, but some fields need review.', { icon: '⚠️' })
      } else {
        toast.success('Job description added')
      }
      onSuccess()
    } catch (err: unknown) {
      toast.error(errorMessage(err, 'Upload failed'))
      setUploading(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
      onClick={uploading ? undefined : onClose}
    >
      <div
        className="w-full max-w-lg bg-[#141B2D] border border-white/[0.08] rounded-lg shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Upload job description"
      >
        <div className="flex items-start justify-between p-6 border-b border-white/[0.06]">
          <div>
            <div className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-1">
              Add role
            </div>
            <h2 className="text-2xl text-[#E7ECF3] leading-tight" style={{ fontFamily: SERIF }}>
              Upload a job description
            </h2>
          </div>
          <button
            onClick={onClose}
            disabled={uploading}
            className="text-[#8996AD] hover:text-[#E7ECF3] transition-colors -mt-1 -mr-1 p-1 disabled:opacity-40"
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        <div className="p-6">
          {!file ? (
            <label
              onDragOver={(e) => {
                e.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={handleDrop}
              className={`block border-2 border-dashed rounded-lg p-10 text-center cursor-pointer transition-all ${
                dragging
                  ? 'border-[#7CFFC4] bg-[#7CFFC4]/[0.04]'
                  : 'border-white/[0.1] hover:border-white/[0.2]'
              }`}
            >
              <input
                ref={inputRef}
                type="file"
                accept={ACCEPT}
                onChange={handleFileInput}
                className="hidden"
              />
              <Upload size={22} className="mx-auto mb-3 text-[#8996AD]" />
              <div className="text-sm text-[#E7ECF3] mb-1">
                Drop a file here, or <span className="text-[#7CFFC4]">browse</span>
              </div>
              <div className="text-xs text-[#8996AD]">PDF, .txt or .md · max 5 MB</div>
            </label>
          ) : (
            <div className="border border-white/[0.08] rounded-lg p-4 flex items-center gap-3">
              <div className="w-10 h-10 rounded bg-[#7CFFC4]/[0.08] text-[#7CFFC4] flex items-center justify-center shrink-0">
                <FileText size={18} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-sm text-[#E7ECF3] truncate">{file.name}</div>
                <div className="text-xs text-[#8996AD]">{formatFileSize(file.size)}</div>
              </div>
              <button
                onClick={() => setFile(null)}
                disabled={uploading}
                className="text-[#8996AD] hover:text-[#E7ECF3] p-1 transition-colors disabled:opacity-40"
                aria-label="Remove file"
              >
                <X size={16} />
              </button>
            </div>
          )}

          <div className="mt-5 flex items-center gap-1.5 text-xs text-[#8996AD]/70">
            <AlertCircle size={12} />
            <span>The role title is read from the document. Scanned PDFs won&apos;t parse.</span>
          </div>

          <div className="mt-6 flex justify-end gap-2">
            <button
              onClick={onClose}
              disabled={uploading}
              className="text-xs text-[#8996AD] hover:text-[#E7ECF3] px-3 py-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={() => void handleUpload()}
              disabled={!file || uploading}
              className="bg-[#7CFFC4] text-[#0B1220] px-4 py-2 rounded text-sm font-medium hover:bg-[#5DE9A8] disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
            >
              {uploading ? (
                <>
                  <Loader2 size={14} className="animate-spin" />
                  Analyzing
                </>
              ) : (
                'Add role'
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Small pieces
// ═════════════════════════════════════════════════════════════

function EmptyState({ onUpload }: { onUpload: () => void }) {
  return (
    <div className="border border-dashed border-white/[0.08] rounded p-8 text-center">
      <div className="text-[#8996AD] flex justify-center mb-3">
        <Briefcase size={20} />
      </div>
      <div className="text-sm font-medium text-[#E7ECF3] mb-1">No roles yet</div>
      <div className="text-xs text-[#8996AD] mb-4">
        Upload a job description (PDF or text) to get started.
      </div>
      <button
        onClick={onUpload}
        className="text-xs bg-white/[0.04] hover:bg-white/[0.08] text-[#E7ECF3] px-3 py-1.5 rounded border border-white/[0.08] transition-colors"
      >
        Upload JD
      </button>
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
        Retry
      </button>
    </div>
  )
}

function ListSkeleton() {
  return (
    <div className="space-y-3">
      {[1, 2, 3].map((i) => (
        <div key={i} className="p-4 rounded border border-white/[0.06] animate-pulse">
          <div className="h-3 w-20 bg-white/[0.06] rounded mb-2" />
          <div className="h-4 w-48 bg-white/[0.06] rounded" />
        </div>
      ))}
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Helpers
// ═════════════════════════════════════════════════════════════

function headline(count: number): string {
  if (count === 0) return 'No open roles yet.'
  return `${count} open ${count === 1 ? 'role' : 'roles'}.`
}

function isSupported(f: File): boolean {
  const name = f.name.toLowerCase()
  return (
    f.type === 'application/pdf' ||
    f.type.startsWith('text/') ||
    name.endsWith('.pdf') ||
    name.endsWith('.txt') ||
    name.endsWith('.md')
  )
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const diffDays = Math.floor((Date.now() - d.getTime()) / (1000 * 60 * 60 * 24))
  if (diffDays <= 0) return 'today'
  if (diffDays === 1) return 'yesterday'
  if (diffDays < 7) return `${diffDays} days ago`
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
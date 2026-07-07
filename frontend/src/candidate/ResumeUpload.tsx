import { useRef, useState } from 'react'
import toast from 'react-hot-toast'
import { X, Upload, FileText, Briefcase, Loader2, AlertCircle } from 'lucide-react'
import { candidateAPI } from '../services/api'

type Mode = 'resume' | 'jd'

interface Props {
  mode: Mode
  onClose: () => void
  onSuccess: () => void
}

export default function ResumeUpload({ mode, onClose, onSuccess }: Props) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
      <div
        className="w-full max-w-lg bg-[#141B2D] border border-white/[0.08] rounded-lg shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <Header mode={mode} onClose={onClose} />
        {mode === 'resume' ? (
          <ResumeUploader onSuccess={onSuccess} />
        ) : (
          <JDComposer onSuccess={onSuccess} />
        )}
      </div>
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Header
// ═════════════════════════════════════════════════════════════

function Header({ mode, onClose }: { mode: Mode; onClose: () => void }) {
  const config =
    mode === 'resume'
      ? { eyebrow: 'Add resume', title: 'Upload a PDF' }
      : { eyebrow: 'Add target role', title: 'Paste a job description' }

  return (
    <div className="flex items-start justify-between p-6 border-b border-white/[0.06]">
      <div>
        <div className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-1">
          {config.eyebrow}
        </div>
        <h2
          className="text-2xl text-[#E7ECF3] leading-tight"
          style={{ fontFamily: '"Instrument Serif", Georgia, serif' }}
        >
          {config.title}
        </h2>
      </div>
      <button
        onClick={onClose}
        className="text-[#8996AD] hover:text-[#E7ECF3] transition-colors -mt-1 -mr-1 p-1"
        aria-label="Close"
      >
        <X size={18} />
      </button>
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Resume uploader (drag-and-drop PDF)
// ═════════════════════════════════════════════════════════════

function ResumeUploader({ onSuccess }: { onSuccess: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  function validateAndSet(f: File): boolean {
    if (f.type !== 'application/pdf') {
      toast.error('Only PDF files are supported')
      return false
    }
    if (f.size > 5 * 1024 * 1024) {
      toast.error('File exceeds 5 MB limit')
      return false
    }
    setFile(f)
    return true
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
  }

  async function handleUpload() {
    if (!file) return
    setUploading(true)
    try {
      const res = await candidateAPI.uploadResume(file)
      if (res.data.parse_error) {
        toast('Uploaded, but some fields need review.', { icon: '⚠️' })
      } else {
        toast.success('Resume added to your workspace')
      }
      onSuccess()
    } catch (err: any) {
      const detail = err?.response?.data?.detail || 'Upload failed'
      toast.error(detail)
    } finally {
      setUploading(false)
    }
  }

  return (
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
            accept="application/pdf"
            onChange={handleFileInput}
            className="hidden"
          />
          <Upload size={22} className="mx-auto mb-3 text-[#8996AD]" />
          <div className="text-sm text-[#E7ECF3] mb-1">
            Drop a PDF here, or <span className="text-[#7CFFC4]">browse</span>
          </div>
          <div className="text-xs text-[#8996AD]">
            Max 5 MB · text-based PDFs only
          </div>
        </label>
      ) : (
        <FilePreview file={file} onClear={() => setFile(null)} />
      )}

      <div className="mt-5 flex items-center justify-between text-xs text-[#8996AD]">
        <InlineTip
          text="Scanned images won't parse. Export your resume as text-based PDF."
        />
      </div>

      <div className="mt-6 flex justify-end gap-2">
        <button
          onClick={() => setFile(null)}
          disabled={!file || uploading}
          className="text-xs text-[#8996AD] hover:text-[#E7ECF3] px-3 py-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          Clear
        </button>
        <button
          onClick={handleUpload}
          disabled={!file || uploading}
          className="bg-[#7CFFC4] text-[#0B1220] px-4 py-2 rounded text-sm font-medium hover:bg-[#5DE9A8] disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
        >
          {uploading ? (
            <>
              <Loader2 size={14} className="animate-spin" />
              Uploading
            </>
          ) : (
            'Add to workspace'
          )}
        </button>
      </div>
    </div>
  )
}

function FilePreview({ file, onClear }: { file: File; onClear: () => void }) {
  return (
    <div className="border border-white/[0.08] rounded-lg p-4 flex items-center gap-3">
      <div className="w-10 h-10 rounded bg-[#7CFFC4]/[0.08] text-[#7CFFC4] flex items-center justify-center shrink-0">
        <FileText size={18} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-sm text-[#E7ECF3] truncate">{file.name}</div>
        <div className="text-xs text-[#8996AD]">
          {formatFileSize(file.size)}
        </div>
      </div>
      <button
        onClick={onClear}
        className="text-[#8996AD] hover:text-[#E7ECF3] p-1 transition-colors"
        aria-label="Remove file"
      >
        <X size={16} />
      </button>
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// JD composer (paste text)
// ═════════════════════════════════════════════════════════════

function JDComposer({ onSuccess }: { onSuccess: () => void }) {
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const textOK = text.trim().length >= 20
  const titleOK = title.trim().length >= 1
  const canSubmit = textOK && titleOK && !submitting

  async function handleSubmit() {
    if (!canSubmit) return
    setSubmitting(true)
    try {
      const res = await candidateAPI.createJD({
        title: title.trim(),
        raw_text: text.trim(),
      })
      if (res.data.parse_error) {
        toast('Added, but some fields need review.', { icon: '⚠️' })
      } else {
        toast.success('Target role added')
      }
      onSuccess()
    } catch (err: any) {
      const detail = err?.response?.data?.detail
      toast.error(
        typeof detail === 'string' ? detail : "Couldn't save this role",
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="p-6">
      <div className="space-y-4">
        <Field label="Title" hint="Your reference — how you'll spot this role later.">
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Senior Backend @ Stripe"
            className="w-full bg-[#0B1220] border border-white/[0.08] rounded px-3 py-2 text-sm text-[#E7ECF3] placeholder-[#8996AD]/50 focus:outline-none focus:border-[#7CFFC4]/50 transition-colors"
          />
        </Field>

        <Field
          label="Job description"
          hint={
            textOK
              ? `${text.trim().length} chars`
              : `${text.trim().length} / 20 chars minimum`
          }
        >
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste the full job description — responsibilities, requirements, nice-to-haves."
            rows={9}
            className="w-full bg-[#0B1220] border border-white/[0.08] rounded px-3 py-2 text-sm text-[#E7ECF3] placeholder-[#8996AD]/50 focus:outline-none focus:border-[#7CFFC4]/50 transition-colors resize-none"
          />
        </Field>
      </div>

      <div className="mt-6 flex justify-end gap-2">
        <button
          onClick={() => {
            setTitle('')
            setText('')
          }}
          disabled={submitting || (!title && !text)}
          className="text-xs text-[#8996AD] hover:text-[#E7ECF3] px-3 py-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          Clear
        </button>
        <button
          onClick={handleSubmit}
          disabled={!canSubmit}
          className="bg-[#7CFFC4] text-[#0B1220] px-4 py-2 rounded text-sm font-medium hover:bg-[#5DE9A8] disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
        >
          {submitting ? (
            <>
              <Loader2 size={14} className="animate-spin" />
              Analyzing
            </>
          ) : (
            'Add to workspace'
          )}
        </button>
      </div>
    </div>
  )
}

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1.5">
        <label className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD]">
          {label}
        </label>
        {hint && (
          <span className="text-[10px] text-[#8996AD]/70">{hint}</span>
        )}
      </div>
      {children}
    </div>
  )
}

function InlineTip({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-1.5 text-[#8996AD]/70">
      <AlertCircle size={12} />
      <span>{text}</span>
    </div>
  )
}

// ═════════════════════════════════════════════════════════════
// Helpers
// ═════════════════════════════════════════════════════════════

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
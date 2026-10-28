import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import { ArrowLeft, ArrowRight, Loader2, Send } from 'lucide-react'
import { candidateAPI, type InterviewMessage } from '../services/api'
import { errorMessage, errorStatus } from '../shared/errors'

const SERIF = { fontFamily: '"Instrument Serif", "Iowan Old Style", Georgia, serif' }

type Meta = {
  company: string
  role: string
  questionNumber: number
  maxQuestions: number
  complete: boolean
}

export default function InterviewChat() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()

  const [messages, setMessages] = useState<InterviewMessage[]>([])
  const [meta, setMeta] = useState<Meta | null>(null)
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  // Restore the conversation (also makes a page refresh safe).
  const load = useCallback(async () => {
    if (!sessionId) return
    try {
      const { data } = await candidateAPI.getInterviewSession(sessionId)
      setMessages(data.messages)
      setMeta({
        company: data.company,
        role: data.role,
        questionNumber: data.question_number,
        maxQuestions: data.max_questions,
        complete: data.session_complete,
      })
    } catch (err: unknown) {
      toast.error(errorMessage(err, "Couldn't load this interview"))
      navigate('/candidate/interview')
    } finally {
      setLoading(false)
    }
  }, [sessionId, navigate])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length, sending])

  async function handleSend() {
    const text = draft.trim()
    if (!text || sending || !sessionId || !meta || meta.complete) return

    setSending(true)
    setDraft('')
    setMessages((m) => [...m, { role: 'candidate', content: text }])

    try {
      const { data } = await candidateAPI.respondToInterview(sessionId, text)
      setMessages((m) => [...m, { role: 'interviewer', content: data.question }])
      setMeta((prev) =>
        prev
          ? {
              ...prev,
              questionNumber: data.question_number,
              maxQuestions: data.max_questions,
              complete: data.session_complete,
            }
          : prev,
      )
    } catch (err: unknown) {
      // Roll back the optimistic message and give the answer back to the user.
      setMessages((m) => m.slice(0, -1))
      setDraft(text)
      if (errorStatus(err) === 409) {
        toast.error('This interview is already finished')
        void load()
      } else {
        toast.error(errorMessage(err, "Couldn't send your answer. Please try again."))
      }
    } finally {
      setSending(false)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void handleSend()
    }
  }

  const answered = messages.filter((m) => m.role === 'candidate').length
  const canFinishEarly = answered > 0 && meta !== null && !meta.complete

  if (loading || !meta) {
    return (
      <div className="min-h-screen bg-[#0B1220] text-[#8996AD] flex items-center justify-center gap-3">
        <Loader2 size={20} className="animate-spin" />
        Loading interview…
      </div>
    )
  }

  const progress = meta.complete
    ? 100
    : Math.round(((meta.questionNumber - 1) / meta.maxQuestions) * 100)

  return (
    <div className="h-screen flex flex-col bg-[#0B1220] text-[#E7ECF3] font-sans">
      {/* ── Header ────────────────────────────────────────── */}
      <header className="border-b border-white/[0.06] px-8 py-4 flex items-center justify-between gap-4 shrink-0">
        <button
          onClick={() => navigate('/candidate')}
          className="text-xs text-[#8996AD] hover:text-[#E7ECF3] flex items-center gap-1.5 transition-colors"
        >
          <ArrowLeft size={14} />
          Leave
        </button>
        <div className="text-center min-w-0">
          <div className="text-sm truncate" style={SERIF}>
            {meta.role} · {meta.company}
          </div>
          <div className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mt-0.5">
            {meta.complete
              ? 'Interview complete'
              : `Question ${meta.questionNumber} of ${meta.maxQuestions}`}
          </div>
        </div>
        {canFinishEarly ? (
          <button
            onClick={() => navigate(`/candidate/interview/${sessionId}/feedback`)}
            className="text-xs text-[#8996AD] hover:text-[#7CFFC4] transition-colors"
          >
            End &amp; get feedback
          </button>
        ) : (
          <span className="w-24" />
        )}
      </header>
      <div className="h-px bg-white/[0.06] shrink-0">
        <div
          className="h-px bg-[#7CFFC4] transition-all duration-500"
          style={{ width: `${progress}%` }}
        />
      </div>

      {/* ── Transcript ────────────────────────────────────── */}
      <main className="flex-1 overflow-y-auto px-6 py-8">
        <div className="max-w-2xl mx-auto space-y-8">
          {messages.map((m, i) => (
            <Bubble key={i} message={m} />
          ))}
          {sending && (
            <div className="flex items-center gap-2 text-xs text-[#8996AD]">
              <Loader2 size={14} className="animate-spin" />
              Interviewer is thinking…
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </main>

      {/* ── Composer / finish ─────────────────────────────── */}
      <footer className="border-t border-white/[0.06] bg-[#141B2D]/95 px-6 py-4 shrink-0">
        <div className="max-w-2xl mx-auto">
          {meta.complete ? (
            <div className="flex items-center justify-between">
              <span className="text-sm text-[#8996AD]">
                That's the last question. Nicely done.
              </span>
              <button
                onClick={() => navigate(`/candidate/interview/${sessionId}/feedback`)}
                className="bg-[#7CFFC4] text-[#0B1220] px-5 py-2 rounded font-medium text-sm flex items-center gap-2 hover:bg-[#5DE9A8] transition-colors"
              >
                See my feedback
                <ArrowRight size={16} />
              </button>
            </div>
          ) : (
            <div className="flex items-end gap-3">
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={sending}
                rows={3}
                maxLength={4000}
                placeholder="Type your answer…  (Enter to send, Shift+Enter for a new line)"
                className="flex-1 resize-none bg-[#0B1220] border border-white/[0.08] focus:border-[#7CFFC4] outline-none rounded p-3 text-sm placeholder:text-[#8996AD]/60 transition-colors disabled:opacity-50"
              />
              <button
                onClick={() => void handleSend()}
                disabled={!draft.trim() || sending}
                aria-label="Send answer"
                className="bg-[#7CFFC4] text-[#0B1220] h-10 w-10 rounded flex items-center justify-center hover:bg-[#5DE9A8] transition-colors disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
              >
                <Send size={16} />
              </button>
            </div>
          )}
        </div>
      </footer>
    </div>
  )
}

function Bubble({ message }: { message: InterviewMessage }) {
  const isInterviewer = message.role === 'interviewer'
  return (
    <div className={isInterviewer ? '' : 'flex justify-end'}>
      <div className={isInterviewer ? 'max-w-[85%]' : 'max-w-[85%] text-right'}>
        <div className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-2">
          {isInterviewer ? 'Interviewer' : 'You'}
        </div>
        <p
          className={
            isInterviewer
              ? 'text-xl leading-snug text-[#E7ECF3]'
              : 'inline-block text-left text-sm leading-relaxed bg-white/[0.04] border border-white/[0.06] rounded px-4 py-3 whitespace-pre-wrap'
          }
          style={isInterviewer ? SERIF : undefined}
        >
          {message.content}
        </p>
      </div>
    </div>
  )
}

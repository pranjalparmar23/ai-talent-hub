import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { ArrowLeft, ArrowRight, Loader2 } from 'lucide-react'
import { candidateAPI } from '../services/api'
import { errorMessage } from '../shared/errors'

const SERIF = { fontFamily: '"Instrument Serif", "Iowan Old Style", Georgia, serif' }

const COMPANIES = ['Amazon', 'Google', 'Meta', 'Microsoft', 'Apple', 'Netflix', 'Stripe', 'Uber']
const ROLES = [
  'Backend Engineer',
  'Frontend Engineer',
  'Full-Stack Engineer',
  'Data Scientist',
  'ML Engineer',
  'DevOps / SRE',
  'Product Manager',
]

export default function InterviewSetup() {
  const navigate = useNavigate()
  const [company, setCompany] = useState('')
  const [role, setRole] = useState('')
  const [starting, setStarting] = useState(false)

  const ready = company.trim().length > 0 && role.trim().length > 0

  async function handleStart() {
    if (!ready || starting) return
    setStarting(true)
    try {
      const { data } = await candidateAPI.startInterview(company.trim(), role.trim())
      navigate(`/candidate/interview/${data.session_id}`)
    } catch (err: unknown) {
      toast.error(errorMessage(err, "Couldn't start the interview. Please try again."))
      setStarting(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#0B1220] text-[#E7ECF3] font-sans pb-28">
      <nav className="border-b border-white/[0.06] px-8 py-4 flex items-center justify-between">
        <button
          onClick={() => navigate('/candidate')}
          className="text-xs text-[#8996AD] hover:text-[#E7ECF3] flex items-center gap-1.5 transition-colors"
        >
          <ArrowLeft size={14} />
          Workspace
        </button>
        <div className="flex items-center gap-2 text-sm">
          <span className="text-[#7CFFC4]">◆</span>
          <span className="font-medium tracking-tight">Talent Hub</span>
        </div>
      </nav>

      <section className="px-8 py-16 border-b border-white/[0.06]">
        <p className="text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-6">
          Mock interview
        </p>
        <h1 className="text-4xl md:text-5xl leading-tight tracking-tight" style={SERIF}>
          Pick the room you want to walk into.
        </h1>
        <p className="mt-4 text-sm text-[#8996AD] max-w-xl">
          Six questions, asked one at a time, adapting to your answers. You get a scored
          report at the end.
        </p>
      </section>

      <div className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-white/[0.06]">
        <Picker
          eyebrow="Company"
          placeholder="e.g. Amazon, or any company"
          options={COMPANIES}
          value={company}
          onChange={setCompany}
          disabled={starting}
        />
        <Picker
          eyebrow="Role"
          placeholder="e.g. Backend Engineer"
          options={ROLES}
          value={role}
          onChange={setRole}
          disabled={starting}
        />
      </div>

      <div className="fixed bottom-0 inset-x-0 border-t border-white/[0.06] bg-[#141B2D]/95 backdrop-blur px-8 py-4 flex items-center justify-between">
        <div className="text-sm text-[#8996AD] truncate pr-4">
          {ready ? (
            <>
              <span className="text-[#E7ECF3]">{role.trim()}</span> at{' '}
              <span className="text-[#E7ECF3]">{company.trim()}</span> · about 15 minutes
            </>
          ) : (
            'Choose a company and a role to begin'
          )}
        </div>
        <button
          onClick={handleStart}
          disabled={!ready || starting}
          className="bg-[#7CFFC4] text-[#0B1220] px-5 py-2 rounded font-medium text-sm flex items-center gap-2 hover:bg-[#5DE9A8] transition-colors disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
        >
          {starting ? (
            <>
              <Loader2 size={16} className="animate-spin" />
              Preparing your interviewer…
            </>
          ) : (
            <>
              Start interview
              <ArrowRight size={16} />
            </>
          )}
        </button>
      </div>
    </div>
  )
}

function Picker({
  eyebrow,
  placeholder,
  options,
  value,
  onChange,
  disabled,
}: {
  eyebrow: string
  placeholder: string
  options: string[]
  value: string
  onChange: (v: string) => void
  disabled: boolean
}) {
  return (
    <div className="p-8">
      <label className="block text-[10px] tracking-[0.2em] uppercase text-[#8996AD] mb-4">
        {eyebrow}
      </label>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        maxLength={100}
        disabled={disabled}
        placeholder={placeholder}
        className="w-full bg-transparent border-b border-white/[0.12] focus:border-[#7CFFC4] outline-none text-2xl pb-2 mb-6 placeholder:text-[#8996AD]/50 transition-colors disabled:opacity-50"
        style={SERIF}
      />
      <div className="flex flex-wrap gap-2">
        {options.map((opt) => {
          const selected = value.trim().toLowerCase() === opt.toLowerCase()
          return (
            <button
              key={opt}
              type="button"
              disabled={disabled}
              onClick={() => onChange(opt)}
              className={`text-xs px-3 py-1.5 rounded border transition-all disabled:opacity-50 ${
                selected
                  ? 'border-[#7CFFC4] bg-[#7CFFC4]/[0.06] text-[#7CFFC4]'
                  : 'border-white/[0.08] text-[#8996AD] hover:border-white/[0.2] hover:text-[#E7ECF3]'
              }`}
            >
              {opt}
            </button>
          )
        })}
      </div>
    </div>
  )
}

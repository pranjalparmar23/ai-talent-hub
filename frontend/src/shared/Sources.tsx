// Small pieces that show where an answer came from (Phase 7, task 16).
// Pure rules (which links are safe, what the credit line says) live in ./sourceHelpers.

import { Info } from 'lucide-react'
import type { SourceItem } from '../services/api'
import { creditLine, hostOf, safeHref, uniqueSources } from './sourceHelpers'

const LINK_CLASS =
  'text-[#E7ECF3] underline decoration-white/20 underline-offset-2 hover:decoration-[#7CFFC4] transition-colors'

/** A page link that opens in a new tab, or plain text when the address is not safe. */
export function PageLink({
  url,
  children,
  className = LINK_CLASS,
}: {
  url: string | null | undefined
  children: React.ReactNode
  className?: string
}) {
  const href = safeHref(url)
  if (!href) return <span>{children}</span>
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={className}>
      {children}
    </a>
  )
}

/**
 * The note shown when an answer is not based on a reviewed source.
 * The wording comes from the backend and is shown as it is.
 */
export function Notice({ text, className = '' }: { text: string | null | undefined; className?: string }) {
  if (!text || !text.trim()) return null
  return (
    <div
      role="note"
      className={`flex gap-2.5 rounded-lg border border-[#F5C26B]/25 bg-[#F5C26B]/[0.06] px-4 py-3 text-sm leading-relaxed text-[#E7ECF3] ${className}`}
    >
      <Info size={16} className="mt-0.5 shrink-0 text-[#F5C26B]" aria-hidden="true" />
      <p>{text}</p>
    </div>
  )
}

/** The reviewed pages an answer drew on, each with its link and credit line. */
export function SourceList({
  sources,
  heading = 'Sources',
  className = '',
}: {
  sources: readonly SourceItem[] | null | undefined
  heading?: string
  className?: string
}) {
  const items = uniqueSources(sources)
  if (items.length === 0) return null
  return (
    <section className={className} aria-label={heading}>
      <p className="mb-3 text-[10px] uppercase tracking-[0.2em] text-[#8996AD]">{heading}</p>
      <ul className="space-y-2.5">
        {items.map((source) => {
          const host = hostOf(source.url)
          const credit = creditLine(source)
          return (
            <li key={source.url || source.title} className="text-sm leading-relaxed">
              <PageLink url={source.url}>{source.title}</PageLink>
              {host && <span className="ml-2 text-xs text-[#8996AD]">{host}</span>}
              {credit && <p className="text-xs text-[#8996AD]">{credit}</p>}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
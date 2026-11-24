// Helpers for showing where an answer came from (Phase 7, task 16).
//
// The backend returns `sources` (one reviewed page each: title, url, licence and a
// credit line) and a `notice` (set when the answer is not based on any reviewed
// source). These helpers keep the screens simple and keep unsafe links out.

import type { SourceItem } from '../services/api'

/** What the opening interview question was based on, as saved with the session. */
export interface OpeningMaterial {
  sources: SourceItem[]
  notice: string | null
}

/**
 * The link to use for a page, or null when it is not safe to link.
 *
 * Only http and https pages are linked. A relative path, `javascript:`, `data:` and
 * the like give null, and so does an address with a name or password in it. A page
 * that gives null is shown as plain text.
 */
export function safeHref(url: string | null | undefined): string | null {
  if (typeof url !== 'string') return null
  const text = url.trim()
  if (!text) return null
  let parsed: URL
  try {
    parsed = new URL(text)
  } catch {
    return null
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null
  if (parsed.username || parsed.password) return null
  return text
}

/** 'docs.docker.com' for 'https://docs.docker.com/get-started/', or null. */
export function hostOf(url: string | null | undefined): string | null {
  const href = safeHref(url)
  if (!href) return null
  return new URL(href).hostname.replace(/^www\./, '')
}

/** The credit line to show next to a source: its attribution, else its licence. */
export function creditLine(source: Pick<SourceItem, 'attribution' | 'licence'>): string {
  const attribution = (source.attribution || '').trim()
  if (attribution) return attribution
  const licence = (source.licence || '').trim()
  return licence ? `Licence: ${licence}` : ''
}

/** Each page once, in the order given. Pages are told apart by link, else by title. */
export function uniqueSources(sources: readonly SourceItem[] | null | undefined): SourceItem[] {
  const seen = new Set<string>()
  const kept: SourceItem[] = []
  for (const source of sources ?? []) {
    const key = (source.url || '').trim() || (source.title || '').trim()
    if (!key || seen.has(key)) continue
    seen.add(key)
    kept.push(source)
  }
  return kept
}

function asSource(value: unknown): SourceItem | null {
  if (typeof value !== 'object' || value === null) return null
  const item = value as Record<string, unknown>
  if (typeof item.title !== 'string' || !item.title.trim()) return null
  return {
    title: item.title,
    url: typeof item.url === 'string' ? item.url : '',
    licence: typeof item.licence === 'string' ? item.licence : '',
    attribution: typeof item.attribution === 'string' ? item.attribution : '',
  }
}

/**
 * The interview's opening material from an API value, or null.
 *
 * The value is checked and rebuilt rather than trusted (an older backend, or a broken
 * response, may send something else). Nothing is shown when there is neither a source
 * nor a notice.
 */
export function readOpening(state: unknown): OpeningMaterial | null {
  if (typeof state !== 'object' || state === null) return null
  const raw = state as Record<string, unknown>
  const list = Array.isArray(raw.sources) ? raw.sources : []
  const sources = uniqueSources(list.map(asSource).filter((s): s is SourceItem => s !== null))
  const notice = typeof raw.notice === 'string' && raw.notice.trim() ? raw.notice.trim() : null
  if (sources.length === 0 && notice === null) return null
  return { sources, notice }
}
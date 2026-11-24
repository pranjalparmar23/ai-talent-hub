import { describe, expect, it } from 'vitest'
import type { SourceItem } from '../services/api'
import { creditLine, hostOf, readOpening, safeHref, uniqueSources } from './sourceHelpers'

const src = (over: Partial<SourceItem> = {}): SourceItem => ({
  title: 'Get started',
  url: 'https://docs.docker.com/get-started/',
  licence: 'proprietary',
  attribution: 'Docker documentation',
  ...over,
})

describe('safeHref', () => {
  it('keeps http and https pages', () => {
    expect(safeHref('https://docs.docker.com/get-started/')).toBe('https://docs.docker.com/get-started/')
    expect(safeHref('http://example.com/a')).toBe('http://example.com/a')
  })

  it('trims spaces', () => {
    expect(safeHref('  https://roadmap.sh/docker  ')).toBe('https://roadmap.sh/docker')
  })

  it.each([
    'javascript:alert(1)',
    'JavaScript:alert(1)',
    ' javascript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:msgbox(1)',
    'file:///etc/passwd',
    'ftp://example.com/file',
    'mailto:a@b.com',
  ])('refuses %s', (url) => {
    expect(safeHref(url)).toBeNull()
  })

  it.each(['', '   ', '/relative/path', '//evil.example/x', 'not a url', 'docker.com'])(
    'refuses a value that is not a full address: %j',
    (url) => {
      expect(safeHref(url)).toBeNull()
    },
  )

  it('refuses an address with a name or password in it', () => {
    expect(safeHref('https://user:pass@example.com/')).toBeNull()
    expect(safeHref('https://user@example.com/')).toBeNull()
  })

  it('refuses a missing value', () => {
    expect(safeHref(null)).toBeNull()
    expect(safeHref(undefined)).toBeNull()
    expect(safeHref(42 as unknown as string)).toBeNull()
  })
})

describe('hostOf', () => {
  it('gives the site without www', () => {
    expect(hostOf('https://www.freecodecamp.org/news/tag/docker')).toBe('freecodecamp.org')
    expect(hostOf('https://docs.docker.com/get-started/')).toBe('docs.docker.com')
  })

  it('gives null for an address that is not safe', () => {
    expect(hostOf('javascript:alert(1)')).toBeNull()
    expect(hostOf('')).toBeNull()
  })
})

describe('creditLine', () => {
  it('prefers the attribution', () => {
    expect(creditLine(src())).toBe('Docker documentation')
  })

  it('falls back to the licence', () => {
    expect(creditLine(src({ attribution: '  ', licence: 'MIT' }))).toBe('Licence: MIT')
  })

  it('is empty when there is neither', () => {
    expect(creditLine(src({ attribution: '', licence: '' }))).toBe('')
  })
})

describe('uniqueSources', () => {
  it('keeps each page once, in order', () => {
    const a = src({ url: 'https://a.example/' })
    const b = src({ url: 'https://b.example/', title: 'B' })
    expect(uniqueSources([a, b, { ...a }])).toEqual([a, b])
  })

  it('tells pages without a link apart by title', () => {
    const one = src({ url: '', title: 'One' })
    const two = src({ url: '', title: 'Two' })
    expect(uniqueSources([one, two, one])).toEqual([one, two])
  })

  it('drops an entry with neither a link nor a title', () => {
    expect(uniqueSources([src({ url: '', title: '' })])).toEqual([])
  })

  it('handles a missing list', () => {
    expect(uniqueSources(undefined)).toEqual([])
    expect(uniqueSources(null)).toEqual([])
  })
})

describe('readOpening', () => {
  it('reads sources and a notice', () => {
    const state = { sources: [src()], notice: null }
    expect(readOpening(state)).toEqual({ sources: [src()], notice: null })
    expect(readOpening({ sources: [], notice: ' Not based on our reviewed sources. ' })).toEqual({
      sources: [],
      notice: 'Not based on our reviewed sources.',
    })
  })

  it('gives null when there is nothing to show', () => {
    expect(readOpening({ sources: [], notice: null })).toBeNull()
    expect(readOpening({ sources: [], notice: '   ' })).toBeNull()
    expect(readOpening({})).toBeNull()
  })

  it.each([null, undefined, 'text', 5, true])('gives null for state %j', (state) => {
    expect(readOpening(state)).toBeNull()
  })

  it('rebuilds each source and drops bad entries', () => {
    const state = {
      sources: [
        { title: 'Good', url: 'https://a.example/', extra: 'ignored' },
        { url: 'https://no-title.example/' },
        { title: '   ' },
        'a string',
        null,
        { title: 'Wrong types', url: 5, licence: [], attribution: {} },
      ],
      notice: 42,
    }
    expect(readOpening(state)).toEqual({
      sources: [
        { title: 'Good', url: 'https://a.example/', licence: '', attribution: '' },
        { title: 'Wrong types', url: '', licence: '', attribution: '' },
      ],
      notice: null,
    })
  })

  it('ignores sources that are not a list', () => {
    expect(readOpening({ sources: 'nope', notice: 'x' })).toEqual({ sources: [], notice: 'x' })
  })

  it('does not trust a link in the state: the screen checks it with safeHref', () => {
    const opening = readOpening({ sources: [src({ url: 'javascript:alert(1)' })], notice: null })
    expect(opening?.sources[0]?.url).toBe('javascript:alert(1)')
    expect(safeHref(opening?.sources[0]?.url)).toBeNull()
  })
})
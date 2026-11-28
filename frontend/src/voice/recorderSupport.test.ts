import { describe, expect, it } from 'vitest'
import {
  ACCEPTED_TYPES,
  AUTO_STOP_MARGIN_SECONDS,
  DEFAULT_MAX_SECONDS,
  MIME_CANDIDATES,
  MIN_RECORDING_SECONDS,
  autoStopDelayMs,
  baseType,
  formatClock,
  isAcceptedType,
  isRecordingSupported,
  measuredSeconds,
  micErrorCode,
  normalizeMaxSeconds,
  normalizeMinSeconds,
  pickMimeType,
  wholeSeconds,
  type RecorderEnv,
} from './recorderSupport'

const named = (name: string) => Object.assign(new Error('x'), { name })

describe('constants', () => {
  it('uses the server default of 120 seconds', () => {
    expect(DEFAULT_MAX_SECONDS).toBe(120)
  })

  it('ignores recordings of less than half a second', () => {
    expect(MIN_RECORDING_SECONDS).toBe(0.5)
  })

  it('stops a little before the maximum', () => {
    expect(AUTO_STOP_MARGIN_SECONDS).toBeGreaterThan(0)
    expect(AUTO_STOP_MARGIN_SECONDS).toBeLessThan(2) // the server allows 2 s of grace
  })

  it('only asks the browser for formats the server accepts', () => {
    for (const type of MIME_CANDIDATES) expect(isAcceptedType(type)).toBe(true)
  })

  it('lists the formats the API reference lists', () => {
    expect([...ACCEPTED_TYPES].sort()).toEqual(
      ['audio/flac', 'audio/mp4', 'audio/mpeg', 'audio/ogg', 'audio/wav', 'audio/webm'].sort(),
    )
  })

  it('prefers webm with opus, then webm, ogg and mp4', () => {
    expect([...MIME_CANDIDATES]).toEqual([
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/ogg;codecs=opus',
      'audio/mp4',
    ])
  })
})

describe('baseType', () => {
  it.each([
    ['audio/webm;codecs=opus', 'audio/webm'],
    ['audio/webm; codecs=opus', 'audio/webm'],
    ['Audio/WebM', 'audio/webm'],
    ['  audio/mp4  ', 'audio/mp4'],
    ['audio/ogg;codecs=opus;rate=48000', 'audio/ogg'],
    ['', ''],
  ])('%s → %s', (input, expected) => {
    expect(baseType(input)).toBe(expected)
  })
})

describe('isAcceptedType', () => {
  it.each([
    'audio/webm',
    'audio/webm;codecs=opus',
    'AUDIO/WEBM;CODECS=OPUS',
    'audio/ogg;codecs=opus',
    'audio/mp4',
    'audio/mp4;codecs=mp4a.40.2',
    'audio/mpeg',
    'audio/wav',
    'audio/flac',
  ])('accepts %s', (type) => {
    expect(isAcceptedType(type)).toBe(true)
  })

  it.each([
    '',
    'video/webm',
    'video/mp4',
    'audio/x-unknown',
    'audio/aac',
    'application/octet-stream',
    'audio/webmx',
    'xaudio/webm',
  ])('refuses %s', (type) => {
    expect(isAcceptedType(type)).toBe(false)
  })
})

describe('pickMimeType', () => {
  const supports = (...types: string[]) => (type: string) => types.includes(type)

  it('takes the first format in the list that the browser supports', () => {
    const supported = supports('audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus')
    expect(pickMimeType(supported)).toBe('audio/webm')
  })

  it('takes webm with opus first (Chrome, Edge, Firefox)', () => {
    expect(pickMimeType(supports(...MIME_CANDIDATES))).toBe('audio/webm;codecs=opus')
  })

  it('falls back to mp4 on Safari, which has no webm', () => {
    expect(pickMimeType(supports('audio/mp4'))).toBe('audio/mp4')
  })

  it('falls back to ogg when there is no webm', () => {
    expect(pickMimeType(supports('audio/ogg;codecs=opus', 'audio/mp4'))).toBe(
      'audio/ogg;codecs=opus',
    )
  })

  it('returns undefined when no format is supported', () => {
    expect(pickMimeType(() => false)).toBeUndefined()
  })

  it('returns undefined when the browser cannot say', () => {
    expect(pickMimeType(undefined)).toBeUndefined()
  })

  it('treats a function that throws as "not supported"', () => {
    const throwing = (type: string) => {
      if (type === 'audio/webm;codecs=opus') throw new Error('boom')
      return type === 'audio/webm'
    }
    expect(pickMimeType(throwing)).toBe('audio/webm')
  })

  it('only accepts a real true', () => {
    expect(pickMimeType((() => 'maybe') as unknown as (t: string) => boolean)).toBeUndefined()
  })

  it('asks about each format at most once, in order', () => {
    const asked: string[] = []
    pickMimeType((type) => {
      asked.push(type)
      return false
    })
    expect(asked).toEqual([...MIME_CANDIDATES])
  })
})

describe('isRecordingSupported', () => {
  const full: RecorderEnv = {
    isSecureContext: true,
    mediaDevices: { getUserMedia: () => undefined },
    MediaRecorder: function MediaRecorder() {},
  }

  it('is true when the page has everything', () => {
    expect(isRecordingSupported(full)).toBe(true)
  })

  it('is false on a page that is not https or localhost', () => {
    expect(isRecordingSupported({ ...full, isSecureContext: false })).toBe(false)
  })

  it('is false when the page does not say whether it is secure', () => {
    expect(isRecordingSupported({ ...full, isSecureContext: undefined })).toBe(false)
  })

  it.each([
    ['no mediaDevices', { mediaDevices: undefined }],
    ['null mediaDevices', { mediaDevices: null }],
    ['no getUserMedia', { mediaDevices: {} }],
    ['getUserMedia that is not a function', { mediaDevices: { getUserMedia: 'yes' } }],
    ['no MediaRecorder', { MediaRecorder: undefined }],
    ['MediaRecorder that is not a function', { MediaRecorder: {} }],
  ])('is false with %s', (_label, change) => {
    expect(isRecordingSupported({ ...full, ...change })).toBe(false)
  })

  it('is false for an empty page', () => {
    expect(isRecordingSupported({})).toBe(false)
  })
})

describe('micErrorCode', () => {
  it.each([
    ['NotAllowedError', 'permission_denied'],
    ['PermissionDeniedError', 'permission_denied'],
    ['SecurityError', 'permission_denied'],
    ['NotFoundError', 'no_microphone'],
    ['DevicesNotFoundError', 'no_microphone'],
    ['OverconstrainedError', 'no_microphone'],
    ['NotReadableError', 'mic_in_use'],
    ['TrackStartError', 'mic_in_use'],
    ['AbortError', 'mic_in_use'],
    ['TypeError', 'unsupported'],
    ['SomethingElse', 'recorder_failed'],
    ['', 'recorder_failed'],
  ])('%s → %s', (name, code) => {
    expect(micErrorCode(named(name))).toBe(code)
  })

  it('reads the name of any object with one (DOMException is not always an Error)', () => {
    expect(micErrorCode({ name: 'NotAllowedError' })).toBe('permission_denied')
  })

  it.each([undefined, null, 'NotAllowedError', 42, {}, []])('gives recorder_failed for %j', (value) => {
    expect(micErrorCode(value)).toBe('recorder_failed')
  })
})

describe('normalizeMaxSeconds', () => {
  it.each([
    [30, 30],
    [0.5, 0.5],
    [300, 300],
    [undefined, 120],
    [0, 120],
    [-5, 120],
    [Number.NaN, 120],
    [Number.POSITIVE_INFINITY, 120],
  ])('%s → %s', (input, expected) => {
    expect(normalizeMaxSeconds(input)).toBe(expected)
  })
})

describe('normalizeMinSeconds', () => {
  it.each([
    [2, 2],
    [0.2, 0.2],
    [0, 0], // 0 keeps every recording
    [undefined, 0.5],
    [-1, 0.5],
    [Number.NaN, 0.5],
    [Number.POSITIVE_INFINITY, 0.5],
  ])('%s → %s', (input, expected) => {
    expect(normalizeMinSeconds(input)).toBe(expected)
  })
})

describe('autoStopDelayMs', () => {
  it('stops half a second before the maximum', () => {
    expect(autoStopDelayMs(120)).toBe(119_500)
    expect(autoStopDelayMs(10)).toBe(9_500)
  })

  it('never goes below zero for a very short maximum', () => {
    expect(autoStopDelayMs(0.2)).toBe(0)
  })

  it('uses the default for a bad maximum', () => {
    expect(autoStopDelayMs(Number.NaN)).toBe(119_500)
    expect(autoStopDelayMs(-1)).toBe(119_500)
  })
})

describe('measuredSeconds', () => {
  it('is the time between the two readings, in seconds', () => {
    expect(measuredSeconds(1_000, 9_000, 120)).toBe(8)
  })

  it('rounds to two decimals', () => {
    expect(measuredSeconds(0, 8_084, 120)).toBe(8.08)
    expect(measuredSeconds(0, 8_086, 120)).toBe(8.09)
  })

  it('never goes past the maximum', () => {
    expect(measuredSeconds(0, 130_000, 120)).toBe(120)
    expect(measuredSeconds(0, 120_000, 120)).toBe(120)
  })

  it.each([
    [5_000, 5_000],
    [5_000, 4_000],
    [Number.NaN, 4_000],
    [0, Number.NaN],
    [0, Number.POSITIVE_INFINITY],
  ])('is 0 for a reading that makes no sense (%s, %s)', (start, stop) => {
    expect(measuredSeconds(start, stop, 120)).toBe(0)
  })

  it('uses the default maximum when the given one is bad', () => {
    expect(measuredSeconds(0, 200_000, Number.NaN)).toBe(120)
  })
})

describe('wholeSeconds', () => {
  it.each([
    [0, 0, 0],
    [0, 999, 0],
    [0, 1_000, 1],
    [0, 7_400, 7],
    [1_000, 61_000, 60],
    [5_000, 1_000, 0],
    [0, Number.NaN, 0],
    [0, Number.POSITIVE_INFINITY, 0],
  ])('from %s to %s is %s', (start, now, expected) => {
    expect(wholeSeconds(start, now)).toBe(expected)
  })
})

describe('formatClock', () => {
  it.each([
    [0, '0:00'],
    [7, '0:07'],
    [7.9, '0:07'],
    [59, '0:59'],
    [60, '1:00'],
    [125, '2:05'],
    [600, '10:00'],
    [-3, '0:00'],
    [Number.NaN, '0:00'],
  ])('%s → %s', (seconds, text) => {
    expect(formatClock(seconds)).toBe(text)
  })
})
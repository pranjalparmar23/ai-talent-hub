/**
 * Small pure helpers for recording a spoken answer (Phase 8, task 6).
 *
 * `useVoiceRecorder` does the recording; everything that can be decided without a
 * microphone lives here so it can be tested without one: which audio format to ask
 * for, what a microphone error means, whether the browser can record at all, and how
 * long a recording was. Nothing here touches the page, so every function takes what it
 * needs as an argument.
 *
 * See docs/architecture/voice.md ("Voice recorder").
 */

/** The longest answer, in seconds. Matches the server's VOICE_MAX_RECORDING_SECONDS. */
export const DEFAULT_MAX_SECONDS = 120

/**
 * A recording shorter than this is not sent: it is a click that was meant to be
 * something else, and a recording costs the voice allowance at least 10 seconds.
 */
export const MIN_RECORDING_SECONDS = 0.5

/**
 * The recording stops this long before the maximum. The length sent to the server is
 * measured by the clock, so a recording stopped exactly at the maximum could be a few
 * milliseconds over what the server allows (the maximum plus 2 s of grace).
 */
export const AUTO_STOP_MARGIN_SECONDS = 0.5

/**
 * The formats to ask the browser for, best first: Chrome, Edge and Firefox record
 * webm, Firefox can also record ogg, Safari records mp4.
 */
export const MIME_CANDIDATES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/mp4',
] as const

/** The types POST /interview/{id}/transcribe accepts (docs/api/api-reference.md). */
export const ACCEPTED_TYPES = [
  'audio/webm',
  'audio/ogg',
  'audio/mp4',
  'audio/mpeg',
  'audio/wav',
  'audio/flac',
] as const

/** Why a recording could not start or went wrong. The screen writes the message. */
export type RecorderErrorCode =
  | 'unsupported' // no recording in this browser, or the page is not https/localhost
  | 'permission_denied' // the candidate (or the browser's policy) blocked the microphone
  | 'no_microphone' // no input device
  | 'mic_in_use' // another app has the microphone, or it could not be started
  | 'recorder_failed' // the recorder failed, gave no audio, or the device went away
  | 'too_short' // stopped almost at once, so nothing was kept

/** What the page offers, as far as recording is concerned. Tests pass their own. */
export interface RecorderEnv {
  isSecureContext?: boolean
  mediaDevices?: { getUserMedia?: unknown } | null
  MediaRecorder?: unknown
}

/** The type without its parameters, in lower case: "Audio/WebM;codecs=opus" → "audio/webm". */
export function baseType(contentType: string): string {
  return (contentType.split(';')[0] ?? '').trim().toLowerCase()
}

/** Whether the server accepts a recording of this type. */
export function isAcceptedType(contentType: string): boolean {
  return (ACCEPTED_TYPES as readonly string[]).includes(baseType(contentType))
}

/**
 * The first format of MIME_CANDIDATES the browser can record, or undefined when it can
 * record none of them (the recorder then picks its own default, which the caller must
 * check with isAcceptedType). A browser without isTypeSupported, or one that throws, is
 * treated as supporting nothing.
 */
export function pickMimeType(
  isTypeSupported: ((type: string) => boolean) | undefined,
): string | undefined {
  if (typeof isTypeSupported !== 'function') return undefined
  return MIME_CANDIDATES.find((type) => {
    try {
      return isTypeSupported(type) === true
    } catch {
      return false
    }
  })
}

/**
 * Can this page record? It needs the microphone API, MediaRecorder, and a secure
 * context (https or localhost): browsers hide the microphone from any other page.
 * A page that does not say whether it is secure is not assumed to be.
 */
export function isRecordingSupported(env: RecorderEnv): boolean {
  return (
    env.isSecureContext === true &&
    typeof env.mediaDevices?.getUserMedia === 'function' &&
    typeof env.MediaRecorder === 'function'
  )
}

/** What a failed getUserMedia call means, from the error's name. */
export function micErrorCode(error: unknown): RecorderErrorCode {
  const name =
    typeof error === 'object' && error !== null && 'name' in error
      ? String((error as { name: unknown }).name)
      : ''
  switch (name) {
    case 'NotAllowedError':
    case 'PermissionDeniedError':
    case 'SecurityError':
      return 'permission_denied'
    case 'NotFoundError':
    case 'DevicesNotFoundError':
    case 'OverconstrainedError':
      return 'no_microphone'
    case 'NotReadableError':
    case 'TrackStartError':
    case 'AbortError':
      return 'mic_in_use'
    case 'TypeError':
      return 'unsupported' // getUserMedia on a page that may not use it
    default:
      return 'recorder_failed'
  }
}

/** The shortest recording to keep: the given one when it is a number of 0 or more, else the default. */
export function normalizeMinSeconds(value: number | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? value
    : MIN_RECORDING_SECONDS
}

/** The maximum length to use: the given one when it is a positive number, else the default. */
export function normalizeMaxSeconds(value: number | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? value
    : DEFAULT_MAX_SECONDS
}

/** How long to record before stopping by itself, in milliseconds. */
export function autoStopDelayMs(maxSeconds: number): number {
  return Math.max(0, normalizeMaxSeconds(maxSeconds) - AUTO_STOP_MARGIN_SECONDS) * 1000
}

/**
 * Length of a recording in seconds, from two clock readings in milliseconds: two
 * decimals, never negative, never more than `maxSeconds`. This is the value sent as
 * X-Audio-Duration.
 */
export function measuredSeconds(startedMs: number, stoppedMs: number, maxSeconds: number): number {
  const seconds = (stoppedMs - startedMs) / 1000
  if (!Number.isFinite(seconds) || seconds <= 0) return 0
  return Math.round(Math.min(seconds, normalizeMaxSeconds(maxSeconds)) * 100) / 100
}

/** Whole seconds for a running timer: 0 while less than a second has passed. */
export function wholeSeconds(startedMs: number, nowMs: number): number {
  const seconds = Math.floor((nowMs - startedMs) / 1000)
  return Number.isFinite(seconds) && seconds > 0 ? seconds : 0
}

/** "0:07", "2:00": the timer as shown on the screen. */
export function formatClock(seconds: number): string {
  const whole = Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds) : 0
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}
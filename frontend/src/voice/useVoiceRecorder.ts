/**
 * useVoiceRecorder — records one spoken answer in the browser (Phase 8, task 6).
 *
 *   const rec = useVoiceRecorder({ onRecording: (r) => upload(r) })
 *   <button onClick={rec.status === 'recording' ? rec.stop : rec.start} />
 *
 * Click to start, click to stop. The hook only records: it returns the audio, its type
 * and its length through `onRecording`, and the caller sends it to the server
 * (POST /interview/{id}/transcribe). It asks nobody for consent and shows nothing.
 *
 * - `onRecording` is called once for every kept recording, whether the candidate
 *   stopped it or it reached `maxSeconds` (it stops 0.5 s early, see recorderSupport).
 * - `durationSeconds` is measured with the clock between start and stop. It is the
 *   value for the X-Audio-Duration header.
 * - A recording shorter than `minSeconds`, or without audio, is not delivered: `error`
 *   says `too_short` or `recorder_failed`. `cancel()` clears the error. Typing stays available for every error.
 * - The audio exists only in memory, in the `blob` handed to `onRecording`. Nothing is
 *   stored or logged, and the hook keeps no copy after delivering it.
 * - The microphone is released on stop, cancel, error and unmount, so the browser's
 *   recording light goes off. A permission answer that arrives after `cancel` or an
 *   unmount is released at once.
 *
 * See docs/architecture/voice.md ("Voice recorder").
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  autoStopDelayMs,
  isAcceptedType,
  isRecordingSupported,
  measuredSeconds,
  micErrorCode,
  normalizeMaxSeconds,
  normalizeMinSeconds,
  pickMimeType,
  wholeSeconds,
  type RecorderErrorCode,
} from './recorderSupport'

/** How often the timer on the screen is refreshed. */
const TICK_MS = 250
/** After stop() the recorder must say it has finished within this time, or it failed. */
const STOP_TIMEOUT_MS = 3000

export interface VoiceRecording {
  /** The audio, as the browser recorded it. */
  blob: Blob
  /** The recorder's own type, for the Content-Type header, e.g. "audio/webm;codecs=opus". */
  contentType: string
  /** Seconds between start and stop, measured by the clock. For X-Audio-Duration. */
  durationSeconds: number
}

export interface UseVoiceRecorderOptions {
  /** Longest answer in seconds. Default 120, the server's default. */
  maxSeconds?: number
  /** Shortest recording to keep, in seconds. Default 0.5. */
  minSeconds?: number
  /** Called with every kept recording. The latest function passed is the one called. */
  onRecording?: (recording: VoiceRecording) => void
}

export type RecorderStatus = 'idle' | 'requesting' | 'recording'

export interface VoiceRecorder {
  /** False when this browser or page cannot record; start() then sets `unsupported`. */
  supported: boolean
  /** `requesting`: waiting for the candidate to allow the microphone. */
  status: RecorderStatus
  /** Whole seconds recorded so far; 0 when not recording. */
  elapsedSeconds: number
  /** The longest answer, in seconds. */
  maxSeconds: number
  /** Why the last attempt did not give a recording. Cleared by the next start(). */
  error: RecorderErrorCode | null
  /** Ask for the microphone and start recording. Ignored while already busy. */
  start: () => Promise<void>
  /** Finish the recording; `onRecording` is called when the recorder has flushed. */
  stop: () => void
  /** Throw the recording away (or stop waiting for the microphone). Nothing is delivered. */
  cancel: () => void
  /** Forget the last error. */
  clearError: () => void
}

/** One recording in progress. `ended` means: ignore anything it still reports. */
interface Session {
  stream: MediaStream
  recorder: MediaRecorder
  chunks: Blob[]
  contentType: string
  startedMs: number
  stoppedMs: number | null
  ticker: ReturnType<typeof setInterval> | null
  limiter: ReturnType<typeof setTimeout> | null
  watchdog: ReturnType<typeof setTimeout> | null
  ended: boolean
}

function stopTracks(stream: MediaStream): void {
  for (const track of stream.getTracks()) {
    track.onended = null
    track.stop()
  }
}

/** Stop the timer and the automatic stop. */
function clearTimers(session: Session): void {
  if (session.ticker !== null) clearInterval(session.ticker)
  if (session.limiter !== null) clearTimeout(session.limiter)
  session.ticker = session.limiter = null
}

/** Stop everything the session owns and make it ignore anything it still reports. */
function release(session: Session): void {
  session.ended = true
  clearTimers(session)
  if (session.watchdog !== null) clearTimeout(session.watchdog)
  session.watchdog = null
  stopTracks(session.stream)
}

/** Stop the recorder. The browser throws if it has already stopped. */
function stopRecorder(recorder: MediaRecorder): void {
  try {
    recorder.stop()
  } catch {
    // already stopped: nothing to do
  }
}

function detectSupport(): boolean {
  if (typeof window === 'undefined' || typeof navigator === 'undefined') return false
  return isRecordingSupported({
    isSecureContext: window.isSecureContext,
    mediaDevices: navigator.mediaDevices,
    MediaRecorder: (globalThis as { MediaRecorder?: unknown }).MediaRecorder,
  })
}

export function useVoiceRecorder(options: UseVoiceRecorderOptions = {}): VoiceRecorder {
  const maxSeconds = normalizeMaxSeconds(options.maxSeconds)
  const minSeconds = normalizeMinSeconds(options.minSeconds)

  const supported = useMemo(detectSupport, [])
  const [status, setStatus] = useState<RecorderStatus>('idle')
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [error, setError] = useState<RecorderErrorCode | null>(null)

  // The latest options, read by callbacks that outlive a render.
  const latest = useRef({ maxSeconds, minSeconds, onRecording: options.onRecording })
  useEffect(() => {
    latest.current = { maxSeconds, minSeconds, onRecording: options.onRecording }
  })

  // Which start() is current. cancel() and unmount move it on, so an answer to an
  // older microphone request is recognised and released.
  const attemptRef = useRef(0)
  const busyRef = useRef(false)
  const sessionRef = useRef<Session | null>(null)

  /** Back to idle, with the microphone released. */
  const reset = useCallback((nextError: RecorderErrorCode | null) => {
    const session = sessionRef.current
    sessionRef.current = null
    busyRef.current = false
    if (session) {
      release(session)
      stopRecorder(session.recorder)
    }
    setStatus('idle')
    setElapsedSeconds(0)
    setError(nextError)
  }, [])

  const finish = useCallback((session: Session) => {
    if (session.ended) return
    const stoppedMs = session.stoppedMs ?? performance.now()
    const contentType = session.recorder.mimeType || session.contentType
    const blob = new Blob(session.chunks, { type: contentType })
    const durationSeconds = measuredSeconds(
      session.startedMs,
      stoppedMs,
      latest.current.maxSeconds,
    )
    const { minSeconds: min, onRecording } = latest.current

    // Back to idle first, so the caller sees a finished recorder.
    sessionRef.current = null
    busyRef.current = false
    release(session)
    setStatus('idle')
    setElapsedSeconds(0)

    if (durationSeconds < min) setError('too_short')
    else if (blob.size === 0) setError('recorder_failed')
    else onRecording?.({ blob, contentType, durationSeconds })
  }, [])

  const stop = useCallback(() => {
    const session = sessionRef.current
    if (!session || session.stoppedMs !== null) return
    session.stoppedMs = performance.now()
    clearTimers(session)
    // If the recorder never reports that it has finished, give up instead of
    // leaving the microphone on.
    session.watchdog = setTimeout(() => reset('recorder_failed'), STOP_TIMEOUT_MS)
    stopRecorder(session.recorder)
  }, [reset])

  const cancel = useCallback(() => {
    attemptRef.current += 1
    reset(null)
  }, [reset])

  const clearError = useCallback(() => setError(null), [])

  const start = useCallback(async () => {
    if (busyRef.current) return
    setError(null)
    if (!detectSupport()) {
      setError('unsupported')
      return
    }
    busyRef.current = true
    const attempt = ++attemptRef.current
    setStatus('requesting')

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (err) {
      if (attempt === attemptRef.current) reset(micErrorCode(err))
      return
    }
    if (attempt !== attemptRef.current) {
      stopTracks(stream) // cancelled or unmounted while the candidate was deciding
      return
    }

    const mimeType = pickMimeType((type) => MediaRecorder.isTypeSupported(type))
    let recorder: MediaRecorder
    try {
      recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
    } catch {
      stopTracks(stream)
      reset('recorder_failed')
      return
    }

    const session: Session = {
      stream,
      recorder,
      chunks: [],
      contentType: recorder.mimeType || mimeType || '',
      startedMs: 0,
      stoppedMs: null,
      ticker: null,
      limiter: null,
      watchdog: null,
      ended: false,
    }
    sessionRef.current = session

    recorder.ondataavailable = (event: BlobEvent) => {
      if (!session.ended) session.chunks.push(event.data)
    }
    recorder.onstop = () => finish(session)
    recorder.onerror = () => {
      if (!session.ended) reset('recorder_failed')
    }
    for (const track of stream.getTracks()) {
      // The microphone was unplugged, or the browser took it away.
      track.onended = () => {
        if (!session.ended) reset('recorder_failed')
      }
    }

    try {
      recorder.start()
    } catch {
      reset('recorder_failed')
      return
    }
    // Some browsers only report the type once the recorder is running.
    session.contentType = recorder.mimeType || session.contentType
    if (!isAcceptedType(session.contentType)) {
      reset('unsupported') // a format the server cannot read
      return
    }

    session.startedMs = performance.now()
    session.ticker = setInterval(() => {
      setElapsedSeconds(
        Math.min(wholeSeconds(session.startedMs, performance.now()), latest.current.maxSeconds),
      )
    }, TICK_MS)
    session.limiter = setTimeout(stop, autoStopDelayMs(latest.current.maxSeconds))
    setStatus('recording')
  }, [finish, reset, stop])

  // Leaving the screen ends the recording and releases the microphone.
  useEffect(() => {
    return () => {
      attemptRef.current += 1
      const session = sessionRef.current
      sessionRef.current = null
      busyRef.current = false
      if (session) {
        release(session)
        stopRecorder(session.recorder)
      }
    }
  }, [])

  return { supported, status, elapsedSeconds, maxSeconds, error, start, stop, cancel, clearError }
}
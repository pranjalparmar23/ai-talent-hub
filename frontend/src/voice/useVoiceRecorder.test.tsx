// @vitest-environment jsdom
import { StrictMode, type ReactNode } from 'react'
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useVoiceRecorder, type UseVoiceRecorderOptions, type VoiceRecording } from './useVoiceRecorder'

// ── Fakes for what jsdom does not have: the microphone and the recorder ──────────

class FakeTrack {
  stop = vi.fn()
  onended: (() => void) | null = null
}

class FakeStream {
  tracks = [new FakeTrack()]
  getTracks() {
    return this.tracks
  }
}

class FakeRecorder {
  static instances: FakeRecorder[] = []
  static supported: string[] = []
  static defaultType = 'audio/webm'
  static throwOnConstruct = false
  static throwOnStart = false
  static neverFinishes = false
  static flushDelayMs = 0
  static typeOnlyWhenRunning = false
  static data: () => Blob[] = () => [new Blob([new Uint8Array(1000)])]

  static isTypeSupported(type: string) {
    return FakeRecorder.supported.includes(type)
  }

  state: 'inactive' | 'recording' = 'inactive'
  mimeType: string
  stopCalls = 0
  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  onerror: (() => void) | null = null

  constructor(
    public stream: FakeStream,
    public options?: { mimeType?: string },
  ) {
    if (FakeRecorder.throwOnConstruct) throw new Error('cannot record')
    this.mimeType = FakeRecorder.typeOnlyWhenRunning ? '' : (options?.mimeType ?? FakeRecorder.defaultType)
    FakeRecorder.instances.push(this)
  }

  start() {
    if (FakeRecorder.throwOnStart) throw new Error('cannot start')
    this.state = 'recording'
    if (!this.mimeType) this.mimeType = FakeRecorder.defaultType
  }

  stop() {
    if (this.state === 'inactive') throw new DOMException('not recording', 'InvalidStateError')
    this.state = 'inactive'
    this.stopCalls += 1
    if (FakeRecorder.neverFinishes) return
    const flush = () => {
      for (const data of FakeRecorder.data()) this.ondataavailable?.({ data })
      this.onstop?.()
    }
    if (FakeRecorder.flushDelayMs > 0) setTimeout(flush, FakeRecorder.flushDelayMs)
    else queueMicrotask(flush)
  }
}

let stream: FakeStream
let getUserMedia: ReturnType<typeof vi.fn>

const recorder = () => FakeRecorder.instances[FakeRecorder.instances.length - 1]!

function install(options: { secure?: boolean; withRecorder?: boolean; withDevices?: boolean } = {}) {
  const { secure = true, withRecorder = true, withDevices = true } = options
  Object.defineProperty(window, 'isSecureContext', { value: secure, configurable: true })
  Object.defineProperty(navigator, 'mediaDevices', {
    value: withDevices ? { getUserMedia } : undefined,
    configurable: true,
  })
  if (withRecorder) vi.stubGlobal('MediaRecorder', FakeRecorder)
  else vi.stubGlobal('MediaRecorder', undefined)
}

beforeEach(() => {
  vi.useFakeTimers({
    toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'performance', 'Date'],
  })
  FakeRecorder.instances = []
  FakeRecorder.supported = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4']
  FakeRecorder.defaultType = 'audio/webm'
  FakeRecorder.throwOnConstruct = false
  FakeRecorder.throwOnStart = false
  FakeRecorder.neverFinishes = false
  FakeRecorder.flushDelayMs = 0
  FakeRecorder.typeOnlyWhenRunning = false
  FakeRecorder.data = () => [new Blob([new Uint8Array(1000)])]
  stream = new FakeStream()
  getUserMedia = vi.fn(async () => stream)
  install()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
  vi.restoreAllMocks()
  Reflect.deleteProperty(navigator, 'mediaDevices')
})

function setup(options: UseVoiceRecorderOptions = {}) {
  const onRecording = vi.fn<(recording: VoiceRecording) => void>()
  const view = renderHook((props: UseVoiceRecorderOptions) => useVoiceRecorder(props), {
    initialProps: { onRecording, ...options },
  })
  return { ...view, onRecording }
}

/** Let time pass, and every timer and promise that comes due run. */
async function wait(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

async function startRecording(hook: ReturnType<typeof setup>) {
  await act(async () => {
    await hook.result.current.start()
  })
}

async function stopRecording(hook: ReturnType<typeof setup>) {
  await act(async () => {
    hook.result.current.stop()
    await vi.advanceTimersByTimeAsync(0)
  })
}

// ── Support ──────────────────────────────────────────────────────────────────────

describe('support', () => {
  it('is supported on a secure page with a recorder and a microphone API', () => {
    expect(setup().result.current.supported).toBe(true)
  })

  it.each([
    ['an insecure page', { secure: false }],
    ['no MediaRecorder', { withRecorder: false }],
    ['no microphone API', { withDevices: false }],
  ])('is not supported with %s, and start() says so without asking for the microphone', async (_label, change) => {
    install(change)
    const hook = setup()
    expect(hook.result.current.supported).toBe(false)

    await startRecording(hook)

    expect(hook.result.current.error).toBe('unsupported')
    expect(hook.result.current.status).toBe('idle')
    expect(getUserMedia).not.toHaveBeenCalled()
  })
})

// ── Starting ─────────────────────────────────────────────────────────────────────

describe('start', () => {
  it('starts idle', () => {
    const { result } = setup()
    expect(result.current).toMatchObject({
      status: 'idle',
      elapsedSeconds: 0,
      error: null,
      maxSeconds: 120,
    })
  })

  it('asks for the microphone, audio only, and then records', async () => {
    const hook = setup()
    await startRecording(hook)

    expect(getUserMedia).toHaveBeenCalledTimes(1)
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true })
    expect(hook.result.current.status).toBe('recording')
    expect(recorder().state).toBe('recording')
  })

  it('shows "requesting" while the candidate decides', async () => {
    let allow: (value: FakeStream) => void = () => {}
    getUserMedia.mockImplementation(() => new Promise((resolve) => (allow = resolve)))
    const hook = setup()

    let started: Promise<void> = Promise.resolve()
    act(() => {
      started = hook.result.current.start()
    })
    expect(hook.result.current.status).toBe('requesting')
    expect(FakeRecorder.instances).toHaveLength(0)

    await act(async () => {
      allow(stream)
      await started
    })
    expect(hook.result.current.status).toBe('recording')
  })

  it('ignores a second start while the first is running', async () => {
    const hook = setup()
    await act(async () => {
      await Promise.all([hook.result.current.start(), hook.result.current.start()])
    })
    await startRecording(hook)

    expect(getUserMedia).toHaveBeenCalledTimes(1)
    expect(FakeRecorder.instances).toHaveLength(1)
  })

  it('asks the recorder for the best format the browser supports', async () => {
    const hook = setup()
    await startRecording(hook)
    expect(recorder().options).toEqual({ mimeType: 'audio/webm;codecs=opus' })
  })

  it('asks for mp4 in a browser that can record nothing else (Safari)', async () => {
    FakeRecorder.supported = ['audio/mp4']
    const hook = setup()
    await startRecording(hook)
    expect(recorder().options).toEqual({ mimeType: 'audio/mp4' })
  })

  it('lets the browser choose when it supports none of the formats, if the server can read its choice', async () => {
    FakeRecorder.supported = []
    FakeRecorder.defaultType = 'audio/ogg'
    const hook = setup()
    await startRecording(hook)

    expect(recorder().options).toBeUndefined()
    expect(hook.result.current.status).toBe('recording')
  })

  it('refuses a default format that the server cannot read, and releases the microphone', async () => {
    FakeRecorder.supported = []
    FakeRecorder.defaultType = 'audio/x-unknown'
    const hook = setup()
    await startRecording(hook)

    expect(hook.result.current.error).toBe('unsupported')
    expect(hook.result.current.status).toBe('idle')
    expect(stream.tracks[0]!.stop).toHaveBeenCalled()
    expect(recorder().state).toBe('inactive')
  })

  it('forgets the last error when it starts again', async () => {
    getUserMedia.mockRejectedValueOnce(Object.assign(new Error('x'), { name: 'NotAllowedError' }))
    const hook = setup()
    await startRecording(hook)
    expect(hook.result.current.error).toBe('permission_denied')

    await startRecording(hook)

    expect(hook.result.current.error).toBeNull()
    expect(hook.result.current.status).toBe('recording')
  })

  it('can clear the error by itself', async () => {
    getUserMedia.mockRejectedValueOnce(Object.assign(new Error('x'), { name: 'NotFoundError' }))
    const hook = setup()
    await startRecording(hook)
    act(() => hook.result.current.clearError())
    expect(hook.result.current.error).toBeNull()
  })
})

// ── When the microphone cannot be used ───────────────────────────────────────────

describe('microphone errors', () => {
  it.each([
    ['NotAllowedError', 'permission_denied'],
    ['SecurityError', 'permission_denied'],
    ['NotFoundError', 'no_microphone'],
    ['NotReadableError', 'mic_in_use'],
    ['TypeError', 'unsupported'],
    ['Whatever', 'recorder_failed'],
  ])('%s gives %s and goes back to idle', async (name, code) => {
    getUserMedia.mockRejectedValue(Object.assign(new Error('x'), { name }))
    const hook = setup()
    await startRecording(hook)

    expect(hook.result.current.error).toBe(code)
    expect(hook.result.current.status).toBe('idle')
    expect(FakeRecorder.instances).toHaveLength(0)
  })

  it('can try again after a refusal', async () => {
    getUserMedia.mockRejectedValueOnce(Object.assign(new Error('x'), { name: 'NotAllowedError' }))
    const hook = setup()
    await startRecording(hook)
    await startRecording(hook)
    expect(hook.result.current.status).toBe('recording')
  })

  it('releases the microphone when the recorder cannot be made', async () => {
    FakeRecorder.throwOnConstruct = true
    const hook = setup()
    await startRecording(hook)

    expect(hook.result.current.error).toBe('recorder_failed')
    expect(hook.result.current.status).toBe('idle')
    expect(stream.tracks[0]!.stop).toHaveBeenCalled()
  })

  it('releases the microphone when the recorder cannot start', async () => {
    FakeRecorder.throwOnStart = true
    const hook = setup()
    await startRecording(hook)

    expect(hook.result.current.error).toBe('recorder_failed')
    expect(stream.tracks[0]!.stop).toHaveBeenCalled()
  })

  it('fails when the recorder reports an error while recording', async () => {
    const hook = setup()
    await startRecording(hook)

    act(() => recorder().onerror?.())

    expect(hook.result.current.error).toBe('recorder_failed')
    expect(hook.result.current.status).toBe('idle')
    expect(stream.tracks[0]!.stop).toHaveBeenCalled()
    expect(hook.onRecording).not.toHaveBeenCalled()
  })

  it('fails when the microphone is unplugged while recording', async () => {
    const hook = setup()
    await startRecording(hook)
    await wait(3000)

    act(() => stream.tracks[0]!.onended?.())
    await wait(0)

    expect(hook.result.current.error).toBe('recorder_failed')
    expect(hook.result.current.status).toBe('idle')
    expect(hook.onRecording).not.toHaveBeenCalled()
  })
})

// ── The timer ────────────────────────────────────────────────────────────────────

describe('timer', () => {
  it('counts whole seconds while recording', async () => {
    const hook = setup()
    await startRecording(hook)
    expect(hook.result.current.elapsedSeconds).toBe(0)

    await wait(1000)
    expect(hook.result.current.elapsedSeconds).toBe(1)
    await wait(2200)
    expect(hook.result.current.elapsedSeconds).toBe(3)
  })

  it('stops counting, and goes back to 0, when the recording ends', async () => {
    const hook = setup()
    await startRecording(hook)
    await wait(4000)
    await stopRecording(hook)

    expect(hook.result.current.elapsedSeconds).toBe(0)
    await wait(5000)
    expect(hook.result.current.elapsedSeconds).toBe(0)
  })

  it('never shows more than the maximum', async () => {
    const hook = setup({ maxSeconds: 10 })
    await startRecording(hook)
    await wait(9400)
    expect(hook.result.current.elapsedSeconds).toBeLessThanOrEqual(10)
  })

  it('never shows more than the maximum when the timer ran late', async () => {
    const hook = setup({ maxSeconds: 10 })
    await startRecording(hook)
    const startedAt = performance.now()
    vi.spyOn(performance, 'now').mockReturnValue(startedAt + 300_000)
    await wait(300)
    expect(hook.result.current.elapsedSeconds).toBe(10)
  })

  it('does not start counting before the microphone is allowed', async () => {
    let allow: (value: FakeStream) => void = () => {}
    getUserMedia.mockImplementation(() => new Promise((resolve) => (allow = resolve)))
    const hook = setup()
    act(() => {
      void hook.result.current.start()
    })
    await wait(5000)

    await act(async () => {
      allow(stream)
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(hook.result.current.elapsedSeconds).toBe(0)
    await wait(2000)
    expect(hook.result.current.elapsedSeconds).toBe(2)
  })
})

// ── Stopping ─────────────────────────────────────────────────────────────────────

describe('stop', () => {
  it('delivers the recording once, with its type, size and length', async () => {
    FakeRecorder.data = () => [new Blob([new Uint8Array(600)]), new Blob([new Uint8Array(900)])]
    const hook = setup()
    await startRecording(hook)
    await wait(8000)
    await stopRecording(hook)

    expect(hook.onRecording).toHaveBeenCalledTimes(1)
    const recording = hook.onRecording.mock.calls[0]![0]
    expect(recording.blob.size).toBe(1500)
    expect(recording.contentType).toBe('audio/webm;codecs=opus')
    expect(recording.blob.type).toBe('audio/webm;codecs=opus')
    expect(recording.durationSeconds).toBe(8)
    expect(hook.result.current.status).toBe('idle')
  })

  it('reports the type the recorder really used, not the one that was asked for', async () => {
    const hook = setup()
    await startRecording(hook)
    recorder().mimeType = 'audio/webm;codecs=vp8'
    await wait(2000)
    await stopRecording(hook)

    expect(hook.onRecording.mock.calls[0]![0].contentType).toBe('audio/webm;codecs=vp8')
  })

  it('measures the length when the button is clicked, not when the recorder finishes', async () => {
    FakeRecorder.flushDelayMs = 1500 // the recorder is slow to flush
    const hook = setup()
    await startRecording(hook)
    await wait(5000)
    act(() => hook.result.current.stop())
    await wait(1500)
    expect(hook.onRecording).toHaveBeenCalledTimes(1)
    expect(hook.onRecording.mock.calls[0]![0].durationSeconds).toBe(5)
  })

  it('freezes the timer at the click, while the recorder flushes', async () => {
    FakeRecorder.flushDelayMs = 1500
    const hook = setup()
    await startRecording(hook)
    await wait(2100)
    expect(hook.result.current.elapsedSeconds).toBe(2)
    act(() => hook.result.current.stop())
    await wait(1000)
    expect(hook.result.current.elapsedSeconds).toBe(2)
    expect(hook.result.current.status).toBe('recording') // not finished yet
  })

  it('delivers a recording that the browser ended by itself, measured when it ended', async () => {
    const hook = setup()
    await startRecording(hook)
    await wait(4000)
    await act(async () => {
      recorder().state = 'inactive'
      recorder().ondataavailable?.({ data: new Blob([new Uint8Array(50)]) })
      recorder().onstop?.()
    })

    expect(hook.onRecording).toHaveBeenCalledTimes(1)
    expect(hook.onRecording.mock.calls[0]![0].durationSeconds).toBe(4)
    expect(hook.result.current.status).toBe('idle')
  })

  it('takes the type from the recorder once it is running, for browsers that only say then', async () => {
    FakeRecorder.supported = []
    FakeRecorder.typeOnlyWhenRunning = true
    FakeRecorder.defaultType = 'audio/ogg'
    const hook = setup()
    await startRecording(hook)
    await wait(2000)
    await stopRecording(hook)

    expect(hook.result.current.error).toBeNull()
    expect(hook.onRecording.mock.calls[0]![0].contentType).toBe('audio/ogg')
  })

  it('releases the microphone and the recorder', async () => {
    const hook = setup()
    await startRecording(hook)
    await wait(2000)
    await stopRecording(hook)

    expect(stream.tracks[0]!.stop).toHaveBeenCalled()
    expect(recorder().state).toBe('inactive')
  })

  it('delivers only once when stop is clicked twice', async () => {
    const hook = setup()
    await startRecording(hook)
    await wait(2000)
    await act(async () => {
      hook.result.current.stop()
      hook.result.current.stop()
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(hook.onRecording).toHaveBeenCalledTimes(1)
    expect(recorder().stopCalls).toBe(1)
    await wait(10_000) // the watchdog of the second click must not fire
    expect(hook.result.current.error).toBeNull()
  })

  it('does not call stop again on the recorder when the second click comes before it has flushed', async () => {
    FakeRecorder.flushDelayMs = 1500
    const hook = setup()
    await startRecording(hook)
    await wait(2000)
    act(() => hook.result.current.stop())
    await wait(300)
    act(() => hook.result.current.stop())
    await wait(2000)

    expect(recorder().stopCalls).toBe(1)
    expect(hook.onRecording).toHaveBeenCalledTimes(1)
    expect(hook.result.current.error).toBeNull()
  })

  it('never reports more than the maximum, even when the timers ran late', async () => {
    const hook = setup({ maxSeconds: 10 })
    await startRecording(hook)
    const startedAt = performance.now()
    vi.spyOn(performance, 'now').mockReturnValue(startedAt + 300_000) // a tab that slept
    await stopRecording(hook)

    expect(hook.onRecording.mock.calls[0]![0].durationSeconds).toBe(10)
  })

  it('does nothing when it is not recording', async () => {
    const hook = setup()
    act(() => hook.result.current.stop())
    expect(hook.result.current.status).toBe('idle')
    expect(hook.onRecording).not.toHaveBeenCalled()
  })

  it('can record again after a recording', async () => {
    const hook = setup()
    await startRecording(hook)
    await wait(2000)
    await stopRecording(hook)
    stream = new FakeStream()
    await startRecording(hook)
    await wait(3000)
    await stopRecording(hook)

    expect(FakeRecorder.instances).toHaveLength(2)
    expect(hook.onRecording).toHaveBeenCalledTimes(2)
    expect(hook.onRecording.mock.calls[1]![0].durationSeconds).toBe(3)
  })

  it('calls the latest onRecording, not the one from the first render', async () => {
    const hook = setup()
    await startRecording(hook)
    const later = vi.fn()
    hook.rerender({ onRecording: later })
    await wait(2000)
    await stopRecording(hook)

    expect(hook.onRecording).not.toHaveBeenCalled()
    expect(later).toHaveBeenCalledTimes(1)
  })

  it('keeps no copy of the audio', async () => {
    const hook = setup()
    await startRecording(hook)
    await wait(2000)
    await stopRecording(hook)

    expect(Object.keys(hook.result.current).sort()).toEqual(
      ['cancel', 'clearError', 'elapsedSeconds', 'error', 'maxSeconds', 'start', 'status', 'stop', 'supported'].sort(),
    )
  })

  it('gives up, and releases the microphone, when the recorder never finishes', async () => {
    FakeRecorder.neverFinishes = true
    const hook = setup()
    await startRecording(hook)
    await wait(2000)
    act(() => hook.result.current.stop())
    expect(hook.result.current.status).toBe('recording')

    await wait(3100)

    expect(hook.result.current.status).toBe('idle')
    expect(hook.result.current.error).toBe('recorder_failed')
    expect(stream.tracks[0]!.stop).toHaveBeenCalled()
    expect(hook.onRecording).not.toHaveBeenCalled()
  })

  it('does not give up when the recorder finishes in time', async () => {
    const hook = setup()
    await startRecording(hook)
    await wait(2000)
    await stopRecording(hook)
    await wait(5000)
    expect(hook.result.current.error).toBeNull()
  })
})

// ── Recordings that are not kept ─────────────────────────────────────────────────

describe('recordings that are not kept', () => {
  it('drops a recording of less than half a second', async () => {
    const hook = setup()
    await startRecording(hook)
    await wait(200)
    await stopRecording(hook)

    expect(hook.onRecording).not.toHaveBeenCalled()
    expect(hook.result.current.error).toBe('too_short')
    expect(hook.result.current.status).toBe('idle')
    expect(stream.tracks[0]!.stop).toHaveBeenCalled()
  })

  it('keeps a recording of exactly the minimum', async () => {
    const hook = setup()
    await startRecording(hook)
    await wait(500)
    await stopRecording(hook)
    expect(hook.onRecording).toHaveBeenCalledTimes(1)
  })

  it('uses the minimum it is given', async () => {
    const hook = setup({ minSeconds: 3 })
    await startRecording(hook)
    await wait(2500)
    await stopRecording(hook)
    expect(hook.result.current.error).toBe('too_short')

    await startRecording(hook)
    await wait(3000)
    await stopRecording(hook)
    expect(hook.onRecording).toHaveBeenCalledTimes(1)
  })

  it('keeps everything when the minimum is 0', async () => {
    const hook = setup({ minSeconds: 0 })
    await startRecording(hook)
    await stopRecording(hook)
    expect(hook.onRecording).toHaveBeenCalledTimes(1)
    expect(hook.onRecording.mock.calls[0]![0].durationSeconds).toBe(0)
  })

  it('drops a recording with no audio in it', async () => {
    FakeRecorder.data = () => [new Blob([])]
    const hook = setup()
    await startRecording(hook)
    await wait(3000)
    await stopRecording(hook)

    expect(hook.onRecording).not.toHaveBeenCalled()
    expect(hook.result.current.error).toBe('recorder_failed')
  })

  it('drops a recording when the recorder gives no data at all', async () => {
    FakeRecorder.data = () => []
    const hook = setup()
    await startRecording(hook)
    await wait(3000)
    await stopRecording(hook)
    expect(hook.result.current.error).toBe('recorder_failed')
  })
})

// ── The maximum length ───────────────────────────────────────────────────────────

describe('maximum length', () => {
  it('stops by itself half a second before the maximum and delivers the recording', async () => {
    const hook = setup({ maxSeconds: 10 })
    await startRecording(hook)

    await wait(9400)
    expect(hook.result.current.status).toBe('recording')
    await wait(200)

    expect(hook.result.current.status).toBe('idle')
    expect(hook.onRecording).toHaveBeenCalledTimes(1)
    expect(hook.onRecording.mock.calls[0]![0].durationSeconds).toBe(9.5)
    expect(stream.tracks[0]!.stop).toHaveBeenCalled()
  })

  it('is 120 seconds unless told otherwise, and never reports more', async () => {
    const hook = setup()
    await startRecording(hook)
    await wait(119_000)
    expect(hook.result.current.status).toBe('recording')
    await wait(1000)

    expect(hook.onRecording).toHaveBeenCalledTimes(1)
    expect(hook.onRecording.mock.calls[0]![0].durationSeconds).toBe(119.5)
  })

  it('shows the maximum it uses', () => {
    expect(setup({ maxSeconds: 30 }).result.current.maxSeconds).toBe(30)
    expect(setup({ maxSeconds: Number.NaN }).result.current.maxSeconds).toBe(120)
    expect(setup({ maxSeconds: -4 }).result.current.maxSeconds).toBe(120)
  })

  it('does nothing more when stop is clicked after the automatic stop', async () => {
    const hook = setup({ maxSeconds: 5 })
    await startRecording(hook)
    await wait(5000)
    await stopRecording(hook)
    expect(hook.onRecording).toHaveBeenCalledTimes(1)
  })

  it('does not stop by itself after a manual stop', async () => {
    const hook = setup({ maxSeconds: 10 })
    await startRecording(hook)
    await wait(2000)
    await stopRecording(hook)
    await wait(20_000)
    expect(hook.onRecording).toHaveBeenCalledTimes(1)
  })
})

// ── Cancelling and leaving the screen ────────────────────────────────────────────

describe('cancel', () => {
  it('throws the recording away and releases the microphone', async () => {
    const hook = setup()
    await startRecording(hook)
    await wait(3000)
    act(() => hook.result.current.cancel())
    await wait(0)

    expect(hook.onRecording).not.toHaveBeenCalled()
    expect(hook.result.current.status).toBe('idle')
    expect(hook.result.current.elapsedSeconds).toBe(0)
    expect(hook.result.current.error).toBeNull()
    expect(stream.tracks[0]!.stop).toHaveBeenCalled()
    expect(recorder().state).toBe('inactive')
  })

  it('does not deliver data that arrives after the cancel', async () => {
    const hook = setup()
    await startRecording(hook)
    await wait(3000)
    const handlers = { data: recorder().ondataavailable, stop: recorder().onstop }
    act(() => hook.result.current.cancel())
    handlers.data?.({ data: new Blob([new Uint8Array(10)]) })
    handlers.stop?.()
    await wait(0)

    expect(hook.onRecording).not.toHaveBeenCalled()
    expect(hook.result.current.error).toBeNull()
    expect(hook.result.current.status).toBe('idle')
  })

  it('stops waiting for the microphone, and releases it when it arrives', async () => {
    let allow: (value: FakeStream) => void = () => {}
    getUserMedia.mockImplementation(() => new Promise((resolve) => (allow = resolve)))
    const hook = setup()
    let started: Promise<void> = Promise.resolve()
    act(() => {
      started = hook.result.current.start()
    })
    act(() => hook.result.current.cancel())
    expect(hook.result.current.status).toBe('idle')

    await act(async () => {
      allow(stream)
      await started
    })

    expect(hook.result.current.status).toBe('idle')
    expect(FakeRecorder.instances).toHaveLength(0)
    expect(stream.tracks[0]!.stop).toHaveBeenCalled()
  })

  it('does not show an error for a refusal that arrives after the cancel', async () => {
    let refuse: (reason: unknown) => void = () => {}
    getUserMedia.mockImplementation(() => new Promise((_resolve, reject) => (refuse = reject)))
    const hook = setup()
    let started: Promise<void> = Promise.resolve()
    act(() => {
      started = hook.result.current.start()
    })
    act(() => hook.result.current.cancel())
    await act(async () => {
      refuse(Object.assign(new Error('x'), { name: 'NotAllowedError' }))
      await started
    })
    expect(hook.result.current.error).toBeNull()
  })

  it('lets a new recording start after a cancel while waiting', async () => {
    let allow: (value: FakeStream) => void = () => {}
    getUserMedia.mockImplementationOnce(() => new Promise((resolve) => (allow = resolve)))
    const hook = setup()
    let first: Promise<void> = Promise.resolve()
    act(() => {
      first = hook.result.current.start()
    })
    act(() => hook.result.current.cancel())

    const second = new FakeStream()
    getUserMedia.mockImplementationOnce(async () => second)
    await startRecording(hook)
    await act(async () => {
      allow(stream) // the first, old, answer
      await first
    })

    expect(hook.result.current.status).toBe('recording')
    expect(stream.tracks[0]!.stop).toHaveBeenCalled() // the old one was released
    expect(second.tracks[0]!.stop).not.toHaveBeenCalled() // the new one is in use
  })

  it('does nothing when idle', () => {
    const hook = setup()
    act(() => hook.result.current.cancel())
    expect(hook.result.current.status).toBe('idle')
  })

  it('is not disturbed by a late report from a recording that was cancelled', async () => {
    const hook = setup()
    await startRecording(hook)
    await wait(1000)
    const old = recorder()
    act(() => hook.result.current.cancel())
    stream = new FakeStream()
    await startRecording(hook)
    expect(recorder()).not.toBe(old)

    act(() => {
      old.onerror?.()
      old.onstop?.()
    })

    expect(hook.result.current.status).toBe('recording')
    expect(hook.result.current.error).toBeNull()
    expect(stream.tracks[0]!.stop).not.toHaveBeenCalled()
    expect(hook.onRecording).not.toHaveBeenCalled()
  })

  it('clears an earlier error', async () => {
    const hook = setup()
    await startRecording(hook)
    await wait(100)
    await stopRecording(hook)
    expect(hook.result.current.error).toBe('too_short')

    act(() => hook.result.current.cancel())
    expect(hook.result.current.error).toBeNull()
  })
})

describe('leaving the screen', () => {
  it('releases the microphone and delivers nothing', async () => {
    const hook = setup()
    await startRecording(hook)
    await wait(3000)

    hook.unmount()
    await wait(0)

    expect(stream.tracks[0]!.stop).toHaveBeenCalled()
    expect(recorder().state).toBe('inactive')
    expect(hook.onRecording).not.toHaveBeenCalled()
  })

  it('releases a microphone that is allowed after the screen has gone', async () => {
    let allow: (value: FakeStream) => void = () => {}
    getUserMedia.mockImplementation(() => new Promise((resolve) => (allow = resolve)))
    const hook = setup()
    let started: Promise<void> = Promise.resolve()
    act(() => {
      started = hook.result.current.start()
    })
    hook.unmount()

    await act(async () => {
      allow(stream)
      await started
    })

    expect(stream.tracks[0]!.stop).toHaveBeenCalled()
    expect(FakeRecorder.instances).toHaveLength(0)
  })

  it('leaves no timer behind', async () => {
    const hook = setup()
    await startRecording(hook)
    hook.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does nothing on unmount when idle', () => {
    const hook = setup()
    expect(() => hook.unmount()).not.toThrow()
  })

  it('works in React strict mode (effects run twice in development)', async () => {
    const wrapper = ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>
    const onRecording = vi.fn()
    const { result } = renderHook(() => useVoiceRecorder({ onRecording }), { wrapper })

    await act(async () => {
      await result.current.start()
    })
    expect(result.current.status).toBe('recording')
    await wait(2000)
    await act(async () => {
      result.current.stop()
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(onRecording).toHaveBeenCalledTimes(1)
    expect(stream.tracks[0]!.stop).toHaveBeenCalled()
  })
})

// ── Privacy ──────────────────────────────────────────────────────────────────────

describe('privacy', () => {
  it('writes nothing to the console or to browser storage', async () => {
    const spies = (['log', 'info', 'warn', 'error', 'debug'] as const).map((name) =>
      vi.spyOn(console, name).mockImplementation(() => {}),
    )
    const hook = setup()
    await startRecording(hook)
    await wait(3000)
    await stopRecording(hook)

    for (const spy of spies) expect(spy).not.toHaveBeenCalled()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it('asks for audio only, never for video', async () => {
    const hook = setup()
    await startRecording(hook)
    expect(getUserMedia.mock.calls[0]![0]).toEqual({ audio: true })
  })
})
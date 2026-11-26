# Voice interview architecture

**Status:** decided 2026-11-25 (Phase 8, Task 1), numbers measured the same day. Implementation follows in Tasks 2–17.
**Owner:** Dev Lead. **Related:** Decision D6 (speech stack), risks R3, R4, R11.

This page records how the interview gets a voice, why, and which numbers the choice rests on. Everything runs on the **Groq free tier**; no paid service is used.

## Decisions at a glance

| # | Topic | Decision |
|---|---|---|
| 1 | Interaction | **Click to start, click to stop.** A timer and a maximum length (planned: 120 s) are shown while recording. No hold-to-talk, no hands-free in the first release (hands-free is Task 9). |
| 2 | Listening (speech to text) | **Groq Whisper, `whisper-large-v3-turbo`**, called by the backend. The model name is a setting, so a retirement is a one-line change. |
| 3 | Speaking (text to speech) | **Browser `speechSynthesis` by default.** Groq Orpheus is an optional switch, capped by a daily budget, cached, and checked for empty audio (see Measured numbers). |
| 4 | Fallbacks | Typing is always available while listening. If Whisper returns 429 or fails, the candidate sees a short message and keeps typing. If Orpheus returns 429, the browser voice speaks instead. |
| 5 | Flow | Audio goes to the backend, comes back as text, the candidate **can edit it**, then it is sent through the existing `/respond` endpoint. The interview logic does not change. |
| 6 | Storage | **Raw audio is never stored**: not on disk, not in the database, not in logs. It is read from the request, sent to Groq and dropped. |
| 7 | Limits | Per-user **and** organisation-wide daily counters in Redis (the Groq limits belong to the organisation, not to one user). |
| 8 | Language | **English only** for the first release. |
| 9 | Streaming | Out of scope (Task 17, WebSocket). Each answer is one upload and one reply. |

### Why speaking is browser-first

The free plan allows far more listening than speaking. Orpheus is the bottleneck:

| Model | Requests/min | Requests/day | Other limit |
|---|---|---|---|
| `whisper-large-v3-turbo` | 20 | 2,000 | 7,200 audio s/hour, 28,800 audio s/day |
| `canopylabs/orpheus-v1-english` | 10 | 100 | 1,200 tokens/min, 3,600 tokens/day |
| `openai/gpt-oss-120b` (for comparison) | 30 | 1,000 | 8,000 tokens/min, 200,000 tokens/day |

Orpheus also accepts at most **200 characters per request**, so one interview question usually needs two or three requests. Measured on 2026-11-25, **one token is one character**, so the 3,600 tokens a day are about 3,600 characters: roughly 25 questions of the length we tested, for the whole organisation, shared with every test run.

Orpheus also returned **nearly silent audio on 2 of 4 clips in the last run** (see Measured numbers), and a failed clip still uses its tokens and one of the 100 daily requests. Browser speech has no such limit and no such failures, so it is the default. Orpheus is an optional upgrade for demos, and any use of it must check that the audio has sound and fall back to the browser voice when it does not.

Limits were read from the Groq documentation on 2026-11-25 and apply to the organisation. The page heading is ambiguous about "free" versus "developer", so the real values are confirmed from the Groq console, not from this table (see open items).

## Flow

```
Candidate                Frontend                     Backend                       Groq
   | click "answer"         |                            |                            |
   |----------------------->| MediaRecorder starts       |                            |
   | click "stop"           |                            |                            |
   |----------------------->| POST audio (multipart) --->| check consent + quota      |
   |                        |    .../transcribe          | (Redis, per user + org)    |
   |                        |                            |--- POST audio/transcriptions -->|
   |                        |                            |<-- text -------------------|
   |                        |<-- { text } ---------------| audio dropped here         |
   | edits text (optional)  |                            |                            |
   |----------------------->| POST .../respond (as today)|                            |
   |                        |<-- next question ----------|                            |
   |                        | speak it: browser voice    |                            |
   |                        |   or POST .../speak (Orpheus, cached by text hash)     |
```

## Settings

Quota settings (added in Task 2, all optional, read from the environment; see `backend/.env.example`). Each default is at most 50% of the Groq free-tier limit it protects. A cap of 0 switches that kind of call off.

| Setting | Default | Groq free limit |
|---|---|---|
| `VOICE_ENABLED` | `true` (`false` switches all voice calls off) | — |
| `VOICE_STT_MIN_BILLED_SECONDS` | 10 | Groq bills at least 10 s per request |
| `VOICE_STT_USER_DAILY_REQUESTS` | 30 | — |
| `VOICE_STT_USER_DAILY_SECONDS` | 900 | — |
| `VOICE_STT_ORG_DAILY_REQUESTS` | 600 | 2,000 a day |
| `VOICE_STT_ORG_DAILY_SECONDS` | 10,000 | 28,800 a day |
| `VOICE_STT_ORG_HOURLY_SECONDS` | 3,000 | 7,200 an hour |
| `VOICE_STT_ORG_MINUTE_REQUESTS` | 10 | 20 a minute |
| `VOICE_TTS_USER_DAILY_CHARS` | 600 | — |
| `VOICE_TTS_ORG_DAILY_CHARS` | 1,800 | 3,600 tokens a day (1 token = 1 character) |
| `VOICE_TTS_ORG_DAILY_REQUESTS` | 50 | 100 a day |
| `VOICE_TTS_ORG_MINUTE_CHARS` | 600 | 1,200 tokens a minute |
| `VOICE_TTS_ORG_MINUTE_REQUESTS` | 5 | 10 a minute |

A bad value (not a whole number, or negative) is ignored with a warning and the default stays.

Speech-to-text client settings (added in Task 3, optional):

| Setting | Purpose | Default |
|---|---|---|
| `VOICE_STT_MODEL` | Whisper model name (a retirement is a one-line change) | `whisper-large-v3-turbo` |
| `VOICE_MAX_AUDIO_BYTES` | Largest recording accepted before any call is made | 10,485,760 (Groq's free-plan limit is 25 MB) |

`GROQ_API_KEY` is the existing key; the client reads it when a call is made.

Planned settings, added with the tasks that use them:

| Setting | Purpose | Default | Task |
|---|---|---|---|
| `VOICE_MAX_RECORDING_SECONDS` | Longest recording accepted | 120 | 4 |
| `VOICE_TTS_PROVIDER` | `browser` or `orpheus` | `browser` | 5 |
| `VOICE_TTS_MODEL`, `VOICE_TTS_VOICE` | Orpheus model and voice | `canopylabs/orpheus-v1-english`, `daniel` | 5 |

Names may change when the tasks are built.

## Quota and cost guard (Task 2)

`app/services/voice_quota_service.py` (`VoiceQuotaService`) gives the voice endpoints one place to ask "may I call Groq?". Groq's limits belong to the whole organisation, so one user, or one afternoon of testing, could otherwise use up the day for everybody. Orpheus is the tight one: about 3,600 characters a day.

**How an endpoint uses it** (tasks 4 and 5):

```python
decision = await quota.reserve(STT, user_id, audio_seconds)   # or TTS with the text length
if not decision.allowed:
    raise HTTPException(decision.http_status, decision.message)   # "type instead"
try:
    transcript = await speech.transcribe(audio, content_type, declared_seconds=declared)
except SpeechRateLimited as exc:
    await quota.release(decision.reservation)
    await quota.record_rate_limited(STT, exc.retry_after)
    raise
except NoSpeechDetected as exc:
    # Groq processed the audio, so quota was used: adjust, do not release.
    await quota.adjust(decision.reservation, exc.audio_seconds or declared)
    raise
except SpeechError:
    await quota.release(decision.reservation)     # the call did not use Groq's quota
    raise
await quota.adjust(decision.reservation, transcript.audio_seconds)  # the real length
```

**What is counted.** A reservation adds to every counter at once; if any counter would go over its cap, all of them are rolled back, so a denied call never uses anything.

| Kind | Counters |
|---|---|
| Speech to text | per user per day: requests, audio seconds. Organisation per day: requests, audio seconds. Organisation per hour: audio seconds. Organisation per minute: requests |
| Text to speech | per user per day: characters. Organisation per day: characters, requests. Organisation per minute: characters, requests |

Audio is counted at a 10 s minimum per request. The endpoint reserves the longest recording first and then calls `adjust` with the real length, so a short answer does not use the whole reservation.

**Why a call can be denied.**

| Reason | Meaning | HTTP |
|---|---|---|
| `user_daily` | This user has used today's allowance | 429 |
| `org_daily`, `org_hourly`, `org_minute` | The organisation's allowance is used for that window | 429 |
| `cooldown` | Groq answered 429 and said when to come back | 429 |
| `disabled` | `VOICE_ENABLED=false` | 503 |
| `unavailable` | Redis could not be reached | 503 |

Every denial carries a friendly message that points the candidate to typing, and (except `disabled` and `unavailable`) the seconds until the call could be allowed again.

**Design choices.**

- **Fixed UTC windows, plus a cool-down.** Groq's own windows are rolling (the response headers showed the daily request limit refilling one request every 14.4 minutes), so a fixed window can let through up to twice a cap across a boundary. The caps are at most 50% of Groq's limits for that reason. When Groq still returns 429, `record_rate_limited` blocks that kind of call for the whole organisation for Groq's `retry-after` time (60 s if missing, 1 hour at most).
- **Fail closed.** If Redis is down the call is denied as `unavailable`. Voice is optional and typing always works, so a failure must not spend quota unchecked. The guard never raises for Redis problems.
- **Keys hold no content.** A key is `voice:<kind>:<user or org>:<window>:<time bucket>:<requests or units>`, for example `voice:tts:org:day:20261126:units`. Keys expire a minute after their window ends. Text and audio are never stored.
- **`remaining(kind, user_id)`** returns how much this user can still use, the lowest of their own and the organisation's room, for the interview screen to show later.
- **Browser speech needs no quota** because it runs on the device.

## Speech-to-text client (Task 3)

`app/services/speech_service.py` (`SpeechService`) turns one recording into text with Groq Whisper. It has no endpoint of its own: Task 4 adds `POST …/transcribe`, asks the quota guard first, and calls this.

```python
transcript = await SpeechService().transcribe(audio_bytes, "audio/webm;codecs=opus", declared_seconds=31.2)
transcript.text, transcript.audio_seconds, transcript.latency_s
```

**One request, no retries.** The client uses plain `httpx` and not the Groq SDK, which (as far as we know) retries 429s by itself. A retry would spend quota behind the guard's back. A 429 is raised as `SpeechRateLimited` with Groq's `retry-after`, which the endpoint passes to `VoiceQuotaService.record_rate_limited`.

**Checks before any call.** Empty audio, audio over `VOICE_MAX_AUDIO_BYTES` and unsupported content types are refused without calling Groq. The file is sent with an extension that matches the browser's content type, because Groq's documentation does not say whether it detects the format from the name or the content.

| Browser recording | Sent as |
|---|---|
| `audio/webm` (Chrome, Edge, Firefox) | `answer.webm` |
| `audio/ogg` (Firefox) | `answer.ogg` |
| `audio/mp4` (Safari) | `answer.m4a` |
| `audio/wav`, `audio/mpeg`, `audio/flac` | `.wav`, `.mp3`, `.flac` |

A codec suffix such as `;codecs=opus` is removed from the content type. Settings sent to Groq: the model, `language=en`, `temperature=0` and `response_format=verbose_json`.

**Silence and made-up answers.** The first real runs showed Whisper answering "you", "Thank you." or "." for nearly silent audio. `verbose_json` returns per-segment `no_speech_prob` and `avg_logprob`, and the client raises `NoSpeechDetected` (never an answer) when:

- the text has no letters or digits, or
- every segment is clearly silent: `no_speech_prob` above 0.6 **and** `avg_logprob` below -1.0 (Whisper's own rule), or
- the whole answer is a known filler ("you", "thank you", "thanks for watching", "thank you for watching", "bye") and the segment is not confident: `no_speech_prob` of 0.3 or more, or `avg_logprob` of -0.8 or less, or there is no segment information.

A confident "thank you" and filler words inside a real answer are kept.

**Measured on Groq (2026-11-26, `whisper-large-v3-turbo`, the four clips saved in Task 1):**

| Clip | Whisper heard | `no_speech_prob` | `avg_logprob` | Result |
|---|---|---|---|---|
| 1, speech | the full question | 0 | -0.084 | kept |
| 2, speech | the full question | 0 | -0.044 | kept |
| 3, nearly silent | "." | 0 | -1.037 | refused: no words |
| 4, nearly silent | "you" | 0 | -1.478 | refused: filler answer |

- **`no_speech_prob` was 0 for every clip, silent ones included**, so on this model it carries no information, and the "every segment is silent" rule above will not fire. It stays in case another model (for example `whisper-large-v3`) reports it. The usable signal is `avg_logprob`: good speech sits near 0, and the two silent clips at -1.0 and -1.5. The -0.8 threshold of the filler rule falls between them with room on both sides.
- **Four clips are a small sample.** Real microphone answers (Task 6 onwards) may include quiet but valid speech. Watch for good answers being refused (a very short answer such as "yes" or "thank you" at low confidence is the likeliest case), and adjust the constants in `speech_service.py` if so.

**Errors.** Each cause has its own type, a short message that is safe to show (`str(error)`), a `detail` for logs, and the HTTP status the endpoint should use:

| Error | When | Status |
|---|---|---|
| `SpeechAudioRejected` | empty, too large, unsupported or unreadable audio | 400, 413 or 415 |
| `SpeechRateLimited` | Groq 429 (carries `retry_after`) | 429 |
| `NoSpeechDetected` | silence, noise or a made-up filler answer (carries `audio_seconds`) | 422 |
| `SpeechNotConfigured` | no `GROQ_API_KEY`, or Groq rejected it (401, 403) | 503 |
| `SpeechUnavailable` | timeout, network failure, a server error or an unreadable reply | 503 |

Messages and details never contain the API key or the audio, and Groq's own error text appears only in `detail`, with the key removed.

**Audio length for the quota guard.** `Transcript.audio_seconds` is the longer of the end of Groq's last segment and the length the browser declared (ignored when it is not a sensible number; the endpoint caps it at the longest allowed recording). Both are needed: the segments end where the speech ends, so clip 4, a 7.6 s recording, had one segment ending at 0.5 s, while Groq bills the whole recording. On clip 1 and clip 2 the last segment's end matched the real length exactly (7.2 s and 8.08 s). Task 4 reserves the longest recording first and then calls `VoiceQuotaService.adjust` with this value.

**A refused recording still used quota.** When Groq returns text that is then refused as `NoSpeechDetected`, Groq did process the audio. The error carries `audio_seconds`, and the endpoint must **adjust** the reservation with it, not release it (see the example in the quota section).

**Privacy.** Audio stays in memory and is dropped after the call. Only sizes, status codes and timing are logged, never audio, transcripts or the key. The call is not an LLM call and is not traced in LangSmith. One shared connection pool serves all requests and is closed on shutdown with `close_speech_client()`.

## Privacy

- Consent is asked before the first recording (Task 12). Without it the microphone button does not appear.
- Audio leaves the candidate's browser for our backend, and from there for Groq. Both steps are named in the consent text.
- Browser speech synthesis runs on the device. Browser speech **recognition** is not used by default: support is uneven and some browsers send audio to the vendor's servers. Treat that as unverified until checked (open items).
- The measurement script uses synthetic sentences only, saves no audio, and never writes the API key.
- Monitoring (risk R11) must not log transcripts or audio; only sizes, durations, status codes and latency.

## Map of the remaining tasks

| Task | What | Depends on this page for |
|---|---|---|
| 2 | Quota and cost guard (Redis) — **done** | Per-user and organisation-wide caps |
| 3 | `speech_service.py`, Whisper client — **done** | Model, formats, 25 MB free-plan file limit, `language` hint |
| 4 | `POST /api/candidate/interview/{id}/transcribe` | Flow above, audio never stored |
| 5 | `POST …/speak` with cache | Orpheus limits, 200-character chunks, WAV only |
| 6 | `useVoiceRecorder` hook | Click to start/stop, timer, maximum length |
| 7 | `useSpeech` hook | Browser-first speaking, Orpheus fallback |
| 8 | Voice controls in `InterviewChat` | Editable transcript before sending |
| 9 | Hands-free / voice activity detection | After the basic flow works |
| 10–11 | Delivery metrics and feedback card | Transcript only; audio is not kept |
| 12 | Privacy and consent | Privacy section above |
| 13 | Accessibility and language | English only first |
| 14–15 | Unit tests, cross-browser QA | — |
| 16 | Voice evaluation set (word error rate, latency, LangSmith) | Uses the same measuring approach as the script |
| 17 | Streaming over WebSocket | Out of scope |

## Measured numbers

Measured on 2026-11-25 with `backend/scripts/measure_voice.py`: four synthetic interview-style sentences of 130–143 characters, voice `daniel`, Whisper `whisper-large-v3-turbo`, two complete runs (8 clips). The last run also recorded loudness for each clip, so the figures below come from it unless stated. Raw results stay in `data/eval/results/` (git-ignored).

| Measure | Value |
|---|---|
| Orpheus latency per request (median / max) | 1.21 s / 1.35 s for 7–8 s of audio |
| Orpheus tokens per request | **1 token per character** (136, 143, 130 and 139 for sentences of the same lengths) |
| Whisper latency per request (median / max) | 0.24 s / 0.24 s |
| Whisper time divided by audio length (median) | 0.03, so about 30 times faster than real time |
| Audio length per sentence (median) | 7.4 s |
| Orpheus clips that were nearly silent | **2 of 4** (last run); the earlier complete run also had 2 of 4 transcripts wrong |
| Word error rate on clips that had speech | 0.0 on every one (2 of 2 in the last run, 2 of 2 in the earlier run) |
| Word error rate overall (last run, mean / max) | 0.49 / 1.0, all of it from the two nearly silent clips |
| Word timestamps | The field comes back, but the only call that asked for it was on a nearly silent clip (1 word), so this is **not yet tested on real speech** |

What the numbers mean:

- **Whisper is fast and accurate on speech.** Both clips with sound in the last run, and both in the earlier run, came back word for word. Real microphone audio is harder (noise, accents, webm/opus), and the voice evaluation set (Task 16) measures that. Whisper's job in the product is the microphone path, not Orpheus audio.
- **The failures are Orpheus's, not Whisper's.** The two wrong transcripts ("." and "you") came from clips with sound in only about 1% of their samples, against about 65% for the good clips. Whisper answered correctly for what it heard. The loudness figures are in the result file for each clip.
- **Orpheus is too unreliable to be the default.** A failed clip looks normal (right length, correct header, normal latency, full tokens charged), so only a loudness check catches it. Sentence 4 failed in both complete runs, so it may depend on the text, but two runs cannot show that.
- **Orpheus adds a WAV header that claims the file is about 25 hours long.** The length field is a placeholder (`0xFFFFFFFF`) and there is an extra `LIST` chunk before the audio. Our code must measure the bytes it receives and not trust the header; some players and decoders may also need it repaired.
- **Latency is fine for both.** About 1.2 s to start speaking and 0.24 s to transcribe an answer are not a problem for a turn-based interview.

How the tokens were counted: the response headers show a per-minute token figure that refills 20 tokens a second, which the script corrects for. The daily total is not in any header, so the daily cap is read on the usage page of the Groq console.

To repeat the measurement after a model change:

```
cd backend
python scripts/measure_voice.py --dry-run               # shows the plan, makes no calls
python scripts/measure_voice.py                         # 8 real calls on the free plan
python scripts/measure_voice.py --save-audio            # also keeps the clips, to listen to them
```

Each full run spends about 550 of the 3,600 daily Orpheus tokens, so do not run it many times in one day.

## Open items

1. **Orpheus reliability.** Two of four clips were nearly silent in each of the two complete runs, and sentence 4 failed both times. Not enough runs to say whether it depends on the text, the voice or the time of day. Task 5 must check every clip for sound before using it.
2. **Daily token total.** Confirm on the Groq console usage page that tokens match characters (about 550 per run).
3. **Free plan versus developer values.** The limits page heading is ambiguous; the 100 requests a day and 1,200 tokens a minute seen in the response headers match the free-plan numbers above.
4. **Word timestamps.** Test on real speech in Task 10 or Task 16. The Groq documentation contradicts itself on whether `timestamp_granularities` needs `verbose_json`; the script asks for both.
5. **Browser speech recognition.** Which browsers support it, and where the audio is processed. Verify before offering it as a fallback.
6. **Browser voices.** Quality and availability differ by operating system; check in Task 15.
7. **Rolling windows.** Groq's limits refill continuously, but the guard counts in fixed UTC windows with caps at 50% of the limits. Check the Groq console after a heavy day that the margin was enough, and lower the caps if Groq ever answers 429 before the guard does.
8. **Model retirement (R3).** Groq retires models every few months; model names are settings, and the measuring script is the quick check after a change.
9. **Silence thresholds on real answers.** The filler rule's -0.8 threshold sits between good speech (-0.04, -0.08) and the two silent clips (-1.04, -1.48), but only four clips back it, all synthetic. `no_speech_prob` is always 0 on `whisper-large-v3-turbo`. Check with real microphone recordings (Task 6 onwards and the Task 16 voice evaluation set), and look especially for short, quiet but valid answers being refused.

## Sources (read 2026-11-25)

- Groq speech to text: <https://console.groq.com/docs/speech-to-text>
- Groq text to speech: <https://console.groq.com/docs/text-to-speech>
- Groq rate limits: <https://console.groq.com/docs/rate-limits>
- Groq pricing: <https://groq.com/pricing>

## Changelog

- 2026-11-25: First version. Free-tier voice stack chosen.
- 2026-11-25: Measured numbers added. Orpheus costs one token per character, and half its clips were nearly silent; the Orpheus terms must be accepted in the Groq console before first use (done for this organisation).
- 2026-11-26: Task 2 added the quota and cost guard (`voice_quota_service.py`) and the `VOICE_*` quota settings.
- 2026-11-26: Task 3 added the speech-to-text client (`speech_service.py`) with the silence and filler filter. Checked against real Groq output for the four saved clips: audio length is the longer of the last segment's end and the declared length; a refused recording carries its length so quota is adjusted, not released.
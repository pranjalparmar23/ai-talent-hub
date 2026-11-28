# Voice interview architecture

**Status:** decided 2026-11-25 (Phase 8, Task 1), numbers measured the same day. Implementation follows in Tasks 2–17 (Tasks 2–6 are done).
**Owner:** Dev Lead. **Related:** Decision D6 (speech stack), risks R3, R4, R11.

This page records how the interview gets a voice, why, and which numbers the choice rests on. Everything runs on the **Groq free tier**; no paid service is used.

## Decisions at a glance

| # | Topic | Decision |
|---|---|---|
| 1 | Interaction | **Click to start, click to stop.** A timer and a maximum length (120 s, `VOICE_MAX_RECORDING_SECONDS`) are shown while recording. No hold-to-talk, no hands-free in the first release (hands-free is Task 9). |
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
   |----------------------->| POST raw audio (body)  --->| check consent + quota      |
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

Speech-to-text settings (added in Tasks 3 and 4, optional):

| Setting | Purpose | Default |
|---|---|---|
| `VOICE_STT_MODEL` | Whisper model name (a retirement is a one-line change) | `whisper-large-v3-turbo` |
| `VOICE_MAX_AUDIO_BYTES` | Largest recording accepted before any call is made | 10,485,760 (Groq's free-plan limit is 25 MB) |
| `VOICE_MAX_RECORDING_SECONDS` | Longest answer accepted (Task 4); 0 switches speech to text off | 120 |

`GROQ_API_KEY` is the existing key; the client reads it when a call is made.

Text-to-speech settings (added in Task 5, optional):

| Setting | Purpose | Default |
|---|---|---|
| `VOICE_TTS_PROVIDER` | Who reads questions aloud: `browser` (the browser's own voice) or `orpheus` (the `/speak` endpoint). Anything else is ignored with a warning and means `browser` | `browser` |
| `VOICE_TTS_MODEL` | Orpheus model name | `canopylabs/orpheus-v1-english` |
| `VOICE_TTS_VOICE` | Orpheus voice | `daniel` |
| `VOICE_TTS_CACHE_SECONDS` | How long a made clip is kept for the next request | 259,200 (3 days) |

With `browser`, `/speak` answers `503 tts_off` and the app never calls it. Switching to
`orpheus` also needs the Orpheus terms accepted once in the Groq console (done for this
organisation, 2026-11-25).

## Quota and cost guard (Task 2)

`app/services/voice_quota_service.py` (`VoiceQuotaService`) gives the voice endpoints one place to ask "may I call Groq?". Groq's limits belong to the whole organisation, so one user, or one afternoon of testing, could otherwise use up the day for everybody. Orpheus is the tight one: about 3,600 characters a day.

**How an endpoint uses it** (Task 4 does exactly this in `voice_service.py`; Task 5 does the same for speaking, with the difference described under "Several Groq requests in one reservation" below):

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
- **Several Groq requests in one reservation.** Orpheus takes at most 200 characters per request, so one question can be two or three requests. `reserve(kind, user_id, units, requests=n)` counts all `n` against the request counters at once, and `adjust(reservation, units, actual_requests=m)` corrects both numbers when the call ends (for example when the second of three requests failed, `m` is 2). Speech to text always uses the default of one.
- **`remaining(kind, user_id)`** returns how much this user can still use, the lowest of their own and the organisation's room, for the interview screen to show later.
- **Browser speech needs no quota** because it runs on the device.

## Speech-to-text client (Task 3)

`app/services/speech_service.py` (`SpeechService`) turns one recording into text with Groq Whisper. It has no endpoint of its own: `VoiceService` (Task 4) asks the quota guard first and then calls this.

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

## Transcribe endpoint (Task 4)

`POST /api/candidate/interview/{session_id}/transcribe` turns one spoken answer into text. It is the only voice endpoint so far; the full request and response are in `docs/api/api-reference.md`. The route is in `app/api/candidate_routes.py`, the voice logic in `app/services/voice_service.py` (`VoiceService`), which sits between the route, the quota guard and the speech client.

**The request is the audio itself.** The browser sends the recording as the raw request body, with its type (for example `audio/webm;codecs=opus`) as `Content-Type`, and optionally its length in seconds as `X-Audio-Duration`. It is not a multipart upload: as far as we know FastAPI's `UploadFile` writes files over 1 MB to a temporary file on disk, and the decision above is that audio is never written anywhere. The body is read in chunks and refused as soon as it passes `VOICE_MAX_AUDIO_BYTES`; a `Content-Length` over the limit is refused before any of the body is read.

**Order of checks.** Each step happens only if the one before it passed, so a refused call costs nothing:

1. Signed in (401).
2. The interview is the caller's (404, which also covers an unknown or invalid id) and still open (409).
3. The body is not empty (400) and not over the size limit (413).
4. The declared length is not over the longest answer plus 2 s of grace (413 `too_long`).
5. Quota is reserved for the declared length, or for the longest answer when none was declared (429 or 503).
6. Groq Whisper is called, and the reservation is settled:

| Outcome | Quota | Answer |
|---|---|---|
| Text returned | adjusted to the real audio length | 200 |
| No speech detected | adjusted (Groq processed the audio) | 422 `no_speech` |
| Groq 429 | released, and the organisation cool-down starts | 429 `rate_limited` with Groq's wait |
| Any other speech error | released | the error's status |
| Anything unexpected | released | 500 |

**One error shape.** Every failure of this endpoint, including 404 and 409, has `detail = {"code", "message", "retry_after"?}` and, when the wait is known, a `Retry-After` header. The interview screen needs one handler and can show `message` as it is. The messages are short, safe to show, and point to typing. The only exceptions are the standard 401 (a plain-string `detail`) and a server error (500).

**The text is only returned.** It is not saved and not sent to the interviewer. The candidate edits it and sends it through the existing `/respond`, so the interview logic and its history did not change.

**Privacy.** The audio lives in memory for the length of the request. Nothing is written to disk, the database or Redis (the counters hold only numbers), and the logs hold only outcome codes. A test checks that neither the transcript nor the user id appears in the logs.

**Tests.** `tests/unit/services/test_voice_service.py` covers the service on a fake Redis and a fake speech client. `tests/unit/api/test_transcribe_routes.py` goes through the real app (sign-in, ownership, body limits, every error code, the API page, shutdown). Neither uses the network. The first run against real Groq is the manual check below.

**Manual check** (after logging in, with an interview started; replace `$TOKEN` and `$SID`):

```
curl -s -X POST "http://localhost:8000/api/candidate/interview/$SID/transcribe" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: audio/wav" -H "X-Audio-Duration: 8" \
  --data-binary @voice_audio_20261125T123407Z_2.wav
```

A clip with speech returns the text and `remaining`; a nearly silent clip returns 422 `no_speech`. Counters can be read in Redis with `docker exec ath_redis redis-cli --scan --pattern 'voice:stt:*'`.

## Speak endpoint (Task 5)

`POST /api/candidate/interview/{session_id}/speak` reads one interviewer question aloud with Groq Orpheus. It exists for the optional Orpheus mode: the default is the browser's own voice, which needs no endpoint and no quota. The request and response are in `docs/api/api-reference.md`. The pieces:

| File | Job |
|---|---|
| `app/services/wav_audio.py` | Reads Groq's WAV (placeholder length, extra `LIST` chunk), checks the clip for sound, joins clips, writes a correct WAV |
| `app/services/tts_service.py` | Cleans and splits the text, calls Groq once per chunk, checks every clip |
| `app/services/voice_cache.py` | Keeps made clips in Redis |
| `app/services/voice_service.py` (`speak`) | Cache, quota guard, settlement, sharing of one call between identical requests |
| `app/api/candidate_routes.py` | Sign-in, ownership, picks the question from the interview, returns the audio |

**The text never comes from the request.** The body holds only an optional `message_index`. The route reads the interview the caller owns and takes the text of an interviewer message from it, so the endpoint cannot be used to make Orpheus say anything else, and a user cannot fetch or probe another user's questions.

**Order of checks.** A refused call costs nothing:

1. Signed in (401). The interview is the caller's (404) and still open (409).
2. The message is an interviewer question (400 `not_a_question`, 404 `question_not_found`).
3. `VOICE_TTS_PROVIDER` is `orpheus` (503 `tts_off`) and voice is on (503 `disabled`).
4. The text is cleaned and split (400 or 413 `text_rejected`).
5. **Cache.** A hit returns at once: no Groq call, no quota. It is served even when the user's allowance is used up or during a cool-down.
6. **Quota** is reserved for the characters and for one request per chunk (429 or 503, with messages that point to reading the question on the screen).
7. Orpheus is called, one request per chunk, and the reservation is settled:

| Outcome | Quota | Cache | Answer |
|---|---|---|---|
| Clip made | adjusted to what Groq charged | stored | 200 |
| Groq 429 before any chunk was answered | released, and the cool-down starts | — | 429 `rate_limited` |
| Groq 429 after some chunks were answered | kept for those chunks and the failed request, and the cool-down starts | — | 429 `rate_limited` |
| A chunk came back silent | kept: Groq charged it | not stored | 502 `silent_audio` |
| Any other error before a chunk was answered | released | — | the error's status |
| Any other error after some chunks were answered | kept for those chunks and the failed request | — | the error's status |
| Anything unexpected | released | — | 500 |

**Text preparation.** Links become "the link", markup (`*`, backticks, `_`, headings, bullets, tags) is removed, and square brackets are removed because Orpheus may read `[cheerful]` as a direction. A text with no letter or digit is refused. A cleaned question over 600 characters (the organisation's per-minute cap) is refused with 413. The text is split at sentence ends, then clause marks, then spaces, into chunks of at most 200 characters; nothing is dropped. The clips are joined with 0.15 s of silence between them.

**Every clip is checked for sound.** Half of the measured Orpheus clips were nearly silent although their length, header and cost looked normal. A clip passes when at least 20% of its samples are above 1% of full scale. The four clips measured on 2026-11-25 sit at 0.65–0.67 (good) and 0.013–0.014 (silent), so the line has room on both sides, but four clips are a small sample (open items). A silent clip is never returned or cached.

**No retries.** Plain `httpx`, one request per chunk, no retry: a retry would spend the 3,600 daily tokens behind the quota guard's back. A silent clip is also not retried automatically; the candidate sees the question on screen and the browser voice can read it.

**Cache.** Key: `voice:tts:clip:` plus the SHA-256 of the model, the voice and the cleaned text, so no text and no user id is ever in a key, a log line or a counter. A different model or voice is a different clip, and question text that differs only in markup is the same clip. The value is the WAV as base64 text (the app's Redis client decodes every reply as text; about 470 KB for 7 s of speech), kept for `VOICE_TTS_CACHE_SECONDS`. Clips over 3 MiB are not stored. The cache **fails open**: if Redis is unavailable `get` is a miss and `set` does nothing, so speaking still works. The quota guard is the opposite and fails closed. The clip is the same for every user, so one candidate's clip serves everyone who gets the same question.

**One call for identical requests.** If the same question is requested twice at once (a page that asks twice, or two candidates with the same question), the second request waits for the first. The Groq call runs as its own task: a candidate who leaves does not cancel it, so the quota is always settled and a clip that was paid for is cached. The request that waited reports `X-Voice-Cache: hit`.

**One error shape**, as for `/transcribe`: `detail = {"code", "message", "retry_after"?}` and a `Retry-After` header when the wait is known. The browser can read `X-Voice-Cache` and `Retry-After` because `main.py` exposes them through CORS. The shared Groq client for speaking is closed on shutdown with `close_tts_client()`.

**Privacy.** The question text goes to Groq (named in the consent text, Task 12). The audio is not about the candidate: it is the interviewer's question. It is kept in Redis for 3 days without any user id and nothing is written to disk or the database. The logs hold only outcome codes, counts and timing, never the question, the audio or the user id; tests check this.

**Tests.** `test_wav_audio.py`, `test_tts_service.py`, `test_voice_cache.py`, `test_voice_quota_service.py` and `test_voice_service.py` cover each piece with fakes; `tests/unit/api/test_speak_routes.py` goes through the real app (sign-in, ownership, the optional body, headers, every error, CORS, the API page, shutdown). None uses the network or a key.

**Manual check with real Groq** (set `VOICE_TTS_PROVIDER=orpheus` and restart the backend; an interview must be started; replace `$TOKEN` and `$SID`). One question of about 140 characters costs about 140 of the 3,600 daily Orpheus tokens, so run it only once or twice a day:

```
curl -s -D - -X POST "http://localhost:8000/api/candidate/interview/$SID/speak" \
  -H "Authorization: Bearer $TOKEN" -o question.wav     # X-Voice-Cache: miss
curl -s -D - -X POST "http://localhost:8000/api/candidate/interview/$SID/speak" \
  -H "Authorization: Bearer $TOKEN" -o again.wav        # X-Voice-Cache: hit, no tokens used
```

Play `question.wav`; it should be audible and about 7 s long. Counters: `docker exec ath_redis redis-cli --scan --pattern 'voice:tts:*'`.

## Voice recorder (Task 6)

`frontend/src/voice/useVoiceRecorder.ts` records one spoken answer in the browser. It only records: it hands the audio to the caller, who sends it to `/transcribe` (the controls are Task 8). `frontend/src/voice/recorderSupport.ts` holds the parts that need no microphone, so they can be tested without one.

```ts
const rec = useVoiceRecorder({ maxSeconds: 120, minSeconds: 0.5, onRecording })
rec.start()   // asks for the microphone, then records (status: requesting → recording)
rec.stop()    // ends the recording; onRecording is called when the recorder has flushed
rec.cancel()  // throws the recording away; nothing is delivered
// onRecording({ blob, contentType, durationSeconds })
// also: rec.supported, rec.status, rec.elapsedSeconds, rec.maxSeconds, rec.error, rec.clearError()
```

**What the caller gets.** `onRecording` is called once for every kept recording, whether the candidate stopped it or it reached the maximum. `contentType` is the type the recorder really used (for example `audio/webm;codecs=opus`) and goes in the `Content-Type` header of the upload. `durationSeconds` is measured with the clock **at the click**, not when the recorder finishes, is rounded to two decimals, and is never more than the maximum. It goes in the `X-Audio-Duration` header, which the server uses to reserve the right amount of quota.

**Format.** The hook asks for the first of `audio/webm;codecs=opus`, `audio/webm`, `audio/ogg;codecs=opus`, `audio/mp4` that `MediaRecorder.isTypeSupported` accepts: webm in Chrome, Edge and Firefox, mp4 in Safari (as far as we know; to be confirmed in Task 15). If the browser supports none of them it chooses its own format, and the hook refuses it (`unsupported`) unless the server accepts that type (`webm`, `ogg`, `mp4`, `mpeg`, `wav`, `flac`). Some browsers only report the type once the recorder is running, so it is read again after start.

**Length.**

| Rule | Value | Why |
|---|---|---|
| Maximum | 120 s (`maxSeconds`) | The server's default `VOICE_MAX_RECORDING_SECONDS`. The client does not read the server's value yet (open items) |
| Automatic stop | 0.5 s before the maximum | The length is measured by the clock, so a recording stopped exactly at the maximum could be a few milliseconds over what the server allows (the maximum plus 2 s of grace) |
| Minimum | 0.5 s (`minSeconds`) | A shorter recording is a stray click, and a recording uses at least 10 s of the voice allowance. It is not delivered; `error` is `too_short` |
| No audio | `error` is `recorder_failed` | The recorder gave no data |

**Errors.** The hook returns a code and the screen (Task 8) writes the message. Typing always stays available, so none of these blocks the interview.

| `error` | Cause |
|---|---|
| `unsupported` | No `MediaRecorder` or microphone API, a page that is not https or localhost (browsers hide the microphone from other pages), or a recording format the server cannot read |
| `permission_denied` | The candidate or the browser's policy blocked the microphone |
| `no_microphone` | No input device |
| `mic_in_use` | Another app has the microphone, or it could not be started |
| `recorder_failed` | The recorder failed or gave no audio, the device was unplugged while recording, or the recorder did not finish within 3 s of stop |
| `too_short` | The recording was shorter than `minSeconds` |

`error` is cleared by the next `start()`, by `clearError()` and by `cancel()`.

**Microphone safety.** The microphone is released (every track stopped, so the browser's recording light goes off) when the recording ends, is cancelled, fails, and when the screen is left. A permission answer that arrives after `cancel()` or after the screen has gone is released at once and starts nothing. A late report from an old recording cannot disturb a new one. React strict mode, which mounts effects twice in development, is covered by a test.

**Privacy.** The audio is a `Blob` in memory, handed to `onRecording`; the hook keeps no copy. Nothing is written to `localStorage`, `sessionStorage`, IndexedDB or the console (a test checks the console and storage). The hook asks for audio only. It does not ask for consent: Task 12 decides whether the microphone button is shown at all.

**Not in this task.** Voice-activity detection and a volume meter (Task 9), browser speech recognition (not used, see Privacy), sending the recording and showing the controls (Task 8).

**Tests.** `recorderSupport.test.ts` (111 tests, plain Node) and `useVoiceRecorder.test.tsx` (72 tests) use a fake `MediaRecorder`, a fake microphone and fake timers, so no real microphone is involved. Hook tests run in jsdom, chosen per file with a `// @vitest-environment jsdom` comment, so the other tests stay in Node. A file with JSX must be `.tsx`. Run `npm test` in `frontend/`. CI runs only `tsc --noEmit` and ESLint on the frontend, not the tests (open items).

**Manual check** (after Task 8 adds the button): allow the microphone, record 5 s, stop, and confirm that the recording light goes off at once. Block the microphone in the browser's site settings and confirm the `permission_denied` message. To see which formats a browser records, run this in its console:

```js
['audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus','audio/mp4'].map(t => [t, MediaRecorder.isTypeSupported(t)])
```

## Privacy

- Consent is asked before the first recording (Task 12). Without it the microphone button does not appear.
- Audio leaves the candidate's browser for our backend, and from there for Groq. Both steps are named in the consent text.
- A spoken question made with Orpheus is the interviewer's question, not the candidate's voice. The question text is sent to Groq; the clip is cached in Redis for 3 days with no user id in the key (see the Speak endpoint section).
- The recorder keeps the audio only in memory until it is handed to the upload, and releases the microphone when the recording ends, is cancelled or the screen is left (see the Voice recorder section).
- Browser speech synthesis runs on the device. Browser speech **recognition** is not used by default: support is uneven and some browsers send audio to the vendor's servers. Treat that as unverified until checked (open items).
- The measurement script uses synthetic sentences only, saves no audio, and never writes the API key.
- Monitoring (risk R11) must not log transcripts or audio; only sizes, durations, status codes and latency.

## Map of the remaining tasks

| Task | What | Depends on this page for |
|---|---|---|
| 2 | Quota and cost guard (Redis) — **done** | Per-user and organisation-wide caps |
| 3 | `speech_service.py`, Whisper client — **done** | Model, formats, 25 MB free-plan file limit, `language` hint |
| 4 | `POST /api/candidate/interview/{id}/transcribe` — **done** | Flow above, audio never stored |
| 5 | `POST …/speak` with cache — **done** | Orpheus limits, 200-character chunks, WAV only |
| 6 | `useVoiceRecorder` hook — **done** | Click to start/stop, timer, maximum length |
| 7 | `useSpeech` hook | Browser-first speaking; calls `/speak` only when the server uses Orpheus, and falls back to the browser voice on any error |
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

1. **Orpheus reliability.** Two of four clips were nearly silent in each of the two complete runs, and sentence 4 failed both times. Not enough runs to say whether it depends on the text, the voice or the time of day. Task 5 checks every clip for sound, but the first real run of `/speak` is still to be done: see how often `silent_audio` comes back, and whether the 20% line is right for real questions (it was set from four clips). With only ~25 questions a day, a silent clip wastes a lot, so the browser voice stays the default.
2. **Daily token total.** Confirm on the Groq console usage page that tokens match characters (about 550 per run).
3. **Free plan versus developer values.** The limits page heading is ambiguous; the 100 requests a day and 1,200 tokens a minute seen in the response headers match the free-plan numbers above.
4. **Word timestamps.** Test on real speech in Task 10 or Task 16. The Groq documentation contradicts itself on whether `timestamp_granularities` needs `verbose_json`; the script asks for both.
5. **Browser speech recognition.** Which browsers support it, and where the audio is processed. Verify before offering it as a fallback.
6. **Browser voices.** Quality and availability differ by operating system; check in Task 15.
7. **Rolling windows.** Groq's limits refill continuously, but the guard counts in fixed UTC windows with caps at 50% of the limits. Check the Groq console after a heavy day that the margin was enough, and lower the caps if Groq ever answers 429 before the guard does.
8. **Model retirement (R3).** Groq retires models every few months; model names are settings, and the measuring script is the quick check after a change.
9. **Silence thresholds on real answers.** The filler rule's -0.8 threshold sits between good speech (-0.04, -0.08) and the two silent clips (-1.04, -1.48), but only four clips back it, all synthetic. `no_speech_prob` is always 0 on `whisper-large-v3-turbo`. Check with real microphone recordings (Task 6 onwards and the Task 16 voice evaluation set), and look especially for short, quiet but valid answers being refused.
10. **Redis memory for cached clips.** A clip is about 470 KB as base64, so a day of Orpheus (about 25 questions) is roughly 12 MB, and 3 days of expiry keeps up to about 35 MB. Check the Redis `maxmemory` and eviction policy of the real deployment; the cache fails open, but the interview history in Redis must not be evicted for it.
11. **Header exposure.** `X-Voice-Cache` and `Retry-After` are exposed through CORS for the app on :5173. If the frontend is later served through a proxy on the same origin this is harmless, and if a proxy strips unknown headers check it.
12. **Real microphones.** The hook is tested with fakes only. Check it with a real microphone as soon as Task 8 adds the button, then in Chrome, Firefox, Safari and Edge in Task 15: the format that each browser really records, the size of a 120 s recording (expected to be well under the 10 MiB limit, not measured), that the recording light goes off, and what each browser does when the microphone is blocked.
13. **Maximum length on the client.** `useVoiceRecorder` uses 120 s unless told otherwise, the server's default, but the server's `VOICE_MAX_RECORDING_SECONDS` is not sent to the client. If it is changed, the client must be told (a small settings endpoint, Task 8 or 12), or the server answers `too_long`.
14. **Secure context.** The microphone works only on https or localhost. Opening the dev server by its network address (for example from a phone on the same Wi-Fi) shows `unsupported`. Use localhost, or serve https.
15. **Frontend tests in CI.** The frontend job runs `tsc --noEmit` and ESLint but not `npm test`. Add it once the voice hooks have more tests.

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
- 2026-11-28: Task 6 added the voice recorder: `useVoiceRecorder` and `recorderSupport` in `frontend/src/voice/`. Click to start and stop, a timer, an automatic stop at the maximum (120 s, stopping 0.5 s early), recordings under 0.5 s dropped, the length measured at the click for `X-Audio-Duration`, the microphone always released, and error codes for the screen to turn into messages. Added `jsdom` as a frontend dev dependency for hook tests.
- 2026-11-27: Task 5 added `POST /api/candidate/interview/{id}/speak` (Orpheus text to speech): `wav_audio.py` (Groq's placeholder WAV length, sound check, joining), `tts_service.py` (clean, split at 200 characters, one request per chunk, no retries), `voice_cache.py` (Redis, fail-open), `VoiceService.speak`, the route, the `VOICE_TTS_*` settings, quota reservations of several requests, and `close_tts_client()` on shutdown. The question text comes from the caller's own interview, never from the request. `X-Voice-Cache` and `Retry-After` are exposed through CORS.
- 2026-11-27: Task 4 added `POST /api/candidate/interview/{id}/transcribe` (`voice_service.py`, the route, the `VOICE_MAX_RECORDING_SECONDS` setting and the voice response schemas). The audio is the raw request body, not a multipart upload, so nothing is spooled to disk; every error of the endpoint has one `{code, message, retry_after?}` body; the shared speech client is closed on shutdown.
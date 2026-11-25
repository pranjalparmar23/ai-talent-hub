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

## Planned settings (added in Tasks 2–5)

| Setting | Purpose | Default |
|---|---|---|
| `VOICE_STT_MODEL` | Whisper model name | `whisper-large-v3-turbo` |
| `VOICE_TTS_PROVIDER` | `browser` or `orpheus` | `browser` |
| `VOICE_TTS_MODEL`, `VOICE_TTS_VOICE` | Orpheus model and voice | `canopylabs/orpheus-v1-english`, `daniel` |
| `VOICE_MAX_RECORDING_SECONDS` | Longest recording accepted | 120 |
| `VOICE_USER_DAILY_*`, `VOICE_ORG_DAILY_*` | Daily caps, set well below the Groq limits | set in Task 2 |

Names may change when the tasks are built; this table is the starting point.

## Privacy

- Consent is asked before the first recording (Task 12). Without it the microphone button does not appear.
- Audio leaves the candidate's browser for our backend, and from there for Groq. Both steps are named in the consent text.
- Browser speech synthesis runs on the device. Browser speech **recognition** is not used by default: support is uneven and some browsers send audio to the vendor's servers. Treat that as unverified until checked (open items).
- The measurement script uses synthetic sentences only, saves no audio, and never writes the API key.
- Monitoring (risk R11) must not log transcripts or audio; only sizes, durations, status codes and latency.

## Map of the remaining tasks

| Task | What | Depends on this page for |
|---|---|---|
| 2 | Quota and cost guard (Redis) | Per-user and organisation-wide caps |
| 3 | `speech_service.py`, Whisper client | Model, formats, 25 MB free-plan file limit, `language` hint |
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
7. **Model retirement (R3).** Groq retires models every few months; model names are settings, and the measuring script is the quick check after a change.

## Sources (read 2026-11-25)

- Groq speech to text: <https://console.groq.com/docs/speech-to-text>
- Groq text to speech: <https://console.groq.com/docs/text-to-speech>
- Groq rate limits: <https://console.groq.com/docs/rate-limits>
- Groq pricing: <https://groq.com/pricing>

## Changelog

- 2026-11-25: First version. Free-tier voice stack chosen.
- 2026-11-25: Measured numbers added. Orpheus costs one token per character, and half its clips were nearly silent; the Orpheus terms must be accepted in the Groq console before first use (done for this organisation).
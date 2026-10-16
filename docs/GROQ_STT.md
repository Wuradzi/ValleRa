# Groq STT — optional cloud candidate (Phase 3D.2)

## Mixed Ukrainian requests and contextual names (2026-10-09)

There is no brand-name exemption list. A complete Ukrainian-script word of
at least two letters permits a mixed Ukrainian/ASCII-Latin utterance regardless
of the Latin name's length. Existing mostly-Ukrainian acceptance is retained;
Russian-specific letters and unsupported scripts remain rejected by the guard.
This is a script heuristic, NOT a reliable language identifier: shared Cyrillic
vocabulary and code-switching remain ambiguous. It does not validate intent.

Latin-only names require a live open_app/window_control clarification and an
exact installed-app catalogue match, via the existing scoped-reply predicate.
The callback is bound to one pending-context identity and checks expiry; a
replacement context cannot inherit that exception. Standalone Latin names
without context are now rejected, including formerly whitelisted brands.
Unknown catalogue entries need a full Ukrainian request instead. Successful
results remain unscored/action-ineligible; existing proposal, target, direct-request
and confirmation/execution checks are unchanged. No LLM repair or extra API call.

## Sparse-audio guard (2026-10-09)

Before uploading captured speech, the Groq listener rejects spans >=3000 ms
with <=500 ms above-threshold audio and an activity ratio <=10%. All three
conditions must hold. This is a narrow energy-evidence heuristic, not a speech
classifier. Short replies (including yes/no) are not rejected by this rule.
CapturedAudio carries active_audio_ms and speech_span_ms from the existing
20 ms energy frames; missing legacy measurements do not mean zero activity.
Rejection returns empty/unreliable/unscored recognition, submits no backend
job and cannot approve an action. Logs contain stt.audio_guard reason=sparse_activity
and durations, not speech content. Endpoint timing itself is unchanged.

This filters long sparse recordings like the reported 360 ms activity over
10.92 s. It cannot distinguish a short noise burst from a short utterance:
the separate 340 ms / 640 ms case remains allowed. Quiet or unusually hesitant
speech may also be rejected; validate with the actual microphone. No keyword
blacklist, Whisper/Vosk fallback, confidence invention or transcript repair.

In Groq mode ALL speech recognition, including approval/cancellation, uses Groq.
No Vosk model preload or automatic Vosk fallback occurs in this mode. Approval
audio is uploaded just like conversational audio. Say **так** or **підтверджую**
to approve, **відміна** or **ні** to cancel. Groq confidence is unavailable:
only exact semantic replies from complete successful captures are accepted
for the currently correlated request. Unknown/failed/truncated/stale replies
cannot authorize execution. This is not speaker authentication. Local mode
keeps the constrained Vosk grammar and its confidence thresholds.

Local-mode 2026-10-08 corrective pass: startup preload also prepares the existing Vosk
confirmation model locally. With preload disabled (or an early reply before
startup completes), first confirmation can still incur model load time.
The script filter accepts mixed Latin names when at least two whole Ukrainian
words and at least 50% Ukrainian letters remain; foreign Cyrillic markers are
still rejected. This is not language identification or increased ASR confidence.
Rejected transcript diagnostics contain counts/flags, not speech text.
An explicit missing-program request retains its scoped clarification even if
the model labels the question CHAT. Approval and execution checks remain required.
These changes have offline tests; live cancellation/ASR quality require retesting.

No production default changes. After implementation, one explicitly consented
WAV was tested on 2026-10-07: HTTP200, 3.669s audio at44100Hz, API roundtrip
1.073s. The name was transcribed in Latin letters. This is not corpus-quality,
microphone/E2E or Pi validation; runtime trust restrictions below remain.
Windows/Linux/ARM64 share `STTBackend` and `RecognitionResult`. No local Whisper,
CUDA, sherpa runtime or Groq model files are required by this backend. Existing
microphone dependencies still apply; Vosk recognition is used only in local mode.

## Important trust limitation

Groq `json` returns transcript text, not calibrated recognition confidence.
Since the unscored-chat patch, successful text has confidence_available=False
through RecognitionResult and TurnEnvelope. Numeric confidence=0 remains only
a compatibility placeholder, not a measured probability. API failures still
have recognition_unreliable=True; missing confidence alone is not failure.

**Complete, successful unscored speech supports CHAT/CLARIFY and confirmed proposals.**
It retains action_eligible=False. With natural_actions_enabled, a separate
restricted path accepts direct requests for open_app, find_files, weather and
web_search only. Validated exact arguments are presented through the existing
correlated confirmation before execution; UI or the active STT backend supplies approval.
Exact requests to open an application found in the local catalogue skip Gemini
inference, but use the same validated proposal and confirmation. Unknown names
still use model interpretation. This does not accelerate general Gemini chat.
Negated/indirect requests, unreliable or incomplete speech and pending-selection
replies cannot gain authority. Other actions still require typed requests.
Truncation, incomplete capture and actual recognition failures still request
repetition. Global thresholds are unchanged. Logs show `unscored`, not 0.00.
The initial backend implementation rejected all unscored replies; that behavior
is historical, not the current chat policy. Live conversation after this patch
still needs validation; it does not improve ASR accuracy itself.

## Voice interaction (2026-10-08)

Use «відміна» to cancel a pending confirmation; «ні» remains supported.
The spoken prompt is only the action question (e.g. «Відкрити браузер?»).
Unknown replies still fail closed and receive a retry hint.

Groq transcripts with predominantly non-Ukrainian letters (under 70% Ukrainian
letters after excluding common product names), or ы/э/ё/ъ, are discarded before
dialogue. This is a script heuristic, not full language identification: shared
Cyrillic words may pass; unusual foreign names may be rejected. No LLM repair.

After a direct request to open an app and a CLARIFY asking for its name, a
complete Groq reply naming the app can fill that slot for 120 seconds. It still
requires a fresh correlated confirmation. «Так» and «відміна» are not app names.
Other contextual task types are unchanged. Prefixes Raven and «послухай мене»
are accepted without stripping negations or narrative subjects.

## Configuration

Lexical incompleteness alone now permits tool-free CHAT/CLARIFY for unscored
text. The incomplete flag and action restriction remain; it does not enable
proposals, actions or scoped task selection. Actual capture truncation and
recognition failure still require repetition. LLM failures retain their own
error message instead of being mislabeled as missing STT confidence.

UI: open «…» → «Розпізнавання голосу», select local or Groq, then
«Зберегти вибір STT». Groq requires the audio-upload consent checkbox.
Restart Raven to apply; active capture/confirmation is never hot-swapped.
The UI shows active and saved selection separately. Local means automatic local
profile selection (`backend: auto`), not necessarily the last manually forced
model. If a custom auto profile itself points to Groq, configure a local profile
first. Saving does not test the key or upload audio. Trust restrictions remain.

Merge these fields into the existing `stt` object in `config.json`:

```json
{
  "stt": {
    "backend": "groq",
    "groq_model": "whisper-large-v3-turbo",
    "groq_timeout_seconds": 15,
    "fallback_backend": "none"
  }
}
```

Optional model: `whisper-large-v3`. Leave other configuration intact. Then run
`python main.py`. Use the existing `GROQ_API_KEY` via the project's secret setup;
never put it in config.json, source code, screenshots or command-line arguments.
The backend captures the key before Core clears credential environment variables,
matching LLM providers. No additional key or package is needed: HTTP uses the
already-required `httpx`. `backend: auto` restores existing local selection.
Profiles can also explicitly set backend=groq/model=one of the two model IDs.

Selecting Groq means captured utterances are uploaded to Groq. See the provider's
privacy/retention terms before sending personal speech. Doctor only checks local
configuration: CONFIGURED / MISSING_KEY / NOT_SELECTED and LIVE_NOT_TESTED.
A locked vault can appear as missing until unlocked; presence is not API health.

## One explicitly selected WAV

Activate the project's Python environment, then:

```powershell
python tester.py --probe groq_stt_live --allow-live -- --audio "path/to/one.wav"
```

This sends only that file, requires a key and uses quota. Mono PCM16 WAV is
required; its real sample rate (including 44100 Hz) is preserved, without ffmpeg
or local resampling. The probe prints a transcript into its **private test log**.
Review/redact that log before sharing. No production configuration is modified.

## Explicit corpus benchmark

```powershell
python tester.py --probe stt_benchmark --allow-live --timeout 900 -- --corpus "path/to/corpus" --matrix testing/stt_groq_matrix.json --candidates groq-turbo --cloud
```

Both `--allow-live` and `--cloud` are required. `groq-quality` selects the second
candidate. Omit `--candidates` only to intentionally test both (double uploads).
The existing confirmed corpus format is unchanged. There is no hidden cloud
warmup, no LLM correction, no automatic retries. A failed cloud sample stops that
candidate; rows already measured are retained. Parent timeout remains bounded.

Reports preserve WER/CER, average/median/p95 latency, RTF, duration, backend/model,
failure counts/status and numeric resource sampling. `execution_location=cloud`
and `resource_scope=client_only` mean local RSS/CPU is client overhead, NOT remote
inference cost. Public benchmark reports redact transcripts as before.

## Errors, timing and lifecycle

Missing key, invalid audio, network/DNS, timeout, authorization, rate limit, server,
malformed JSON and empty text return empty unreliable RecognitionResult. Logs
contain fixed error classes/status, never raw HTTP bodies, headers or exceptions.
POST uses the fixed transcription endpoint, uk, temperature=0, JSON, no timestamps.
No translation or instruction execution is performed.

Metrics: request-start event, provider/model, input_sample_rate, audio_seconds,
api_roundtrip_ms (upload + server + download, not separable inference time),
transcription_ms, realtime_factor, success/error_class/http_status and
confidence_available=false. Existing listener PERF span remains in place.

HTTP timeout applies to individual network operations; existing
`stt.primary_timeout_ms` additionally bounds listener waiting. Cancellation does
not forcibly abort a running HTTP call: the existing single-job drain discards
late results, and backend close also discards its in-flight text. No background
dispatch is registered. No parallel retry is started by this backend.

Existing explicit `fallback_backend: vosk` remains available and conservative:
fallback text retains the primary failure's unreliable flags. Successful unscored
Groq text no longer triggers fallback solely because confidence is unavailable.
No automatic faster-whisper hybrid orchestrator was added.

Real Pi audio quality, upload latency, quota limits, cancellation under poor
network and client memory coexistence still require hardware validation.

API reference: https://console.groq.com/docs/speech-to-text

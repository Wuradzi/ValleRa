# Ukrainian STT backends — Phase 3A.2

## Phase 3D.1 — benchmark instrumentation (2026-10-06)

Infrastructure only: no model selection, installation, production default
changes or Linux TTS enablement. Real Pi benchmarks: NOT_RUN.
STT preserves load/first-inference times, row latency/confidence/WER/CER,
aggregate average/median/p95/RTF and lexical semantic accuracy metrics.
Candidates add platform, architecture, Python version, sample_rates,
corpus_duration_seconds, status and existing backend/model/device/compute
metadata. Requested settings are not proof of successful model loading.

Shared benchmark-only resource fields (RAM/RSS/swap are MiB despite the
backward-compatible `_mb` spelling):

| Fields | Meaning |
| --- | --- |
| available_before_load_mb, available_ram_mb, min_available_inference_mb | System available RAM before prepare, latest sample, inference minimum |
| rss_mb, peak_rss_mb, samples | Candidate process RSS, sampled peak, sample count |
| cpu_seconds, cpu_one_core_percent | Process user+system CPU, average utilization; 100% = one core |
| swap_used_mb, swap_used_delta_mb | System-wide swap usage and signed change |
| swap_in_delta_bytes, swap_out_delta_bytes | System-wide swap I/O deltas |
| temperature_c, peak_temperature_c | Readable thermal-zone-0 temperature and maximum |
| throttling_flags, throttling_flags_seen | Latest firmware flags and OR of observations |
| peak_vram_mb, vram_note | Existing optional NVML device-0 total usage, not process-exclusive |

Sampling targets 50ms; atomic checkpoints every ten samples and at load/finish
allow partial recovery on timeout/crash (resources_partial). A crash before the
first checkpoint may yield no data. Sampling overhead is included; between-sample
peaks may be missed. RSS excludes child processes and combined Core+STT+TTS
residency. System RAM/swap include other processes. Unavailable metrics are null,
not zero. Windows disabled performance counters are tolerated. Linux reads only
bounded numeric thermal/firmware sysfs files, without sudo or subprocess tools.
Kernel layouts may omit these files, particularly firmware throttling. No serial,
network inventory or environment collection. Pi sensors/overhead need validation.

Numeric metrics are retained; transcripts/references/arbitrary error text become
`<omitted>`, unknown model paths/candidate IDs/entity labels are hashed. This
intentionally limits old free-text comparison output for privacy; WER/CER use
original strings before redaction. Reports exclude home paths/usernames/arbitrary
model messages. Tester diagnostic logs remain local/internal: review before sharing.

### Backend-neutral TTS harness

`testing/probes/tts_benchmark.py` accepts an adapter with metadata (backend,
model, device, compute_type), prepare()->(ok,detail),
synthesize(text,output_path,cancellation_event), close(), and optional
supports_cancellation. Successful synthesis writes a valid WAV and returns its
path; cooperative cancellation returns None. No Windows probe reuse or playback.
The CLI currently registers ONLY a fake backend producing silent PCM fixtures.

```text
python tester.py --probe tts_benchmark --allow-live -- --fake-mode success
python tester.py --probe tts_benchmark --allow-live -- --fake-mode cancel --cancellation
```

--allow-live is the existing tester probe gate, NOT a live voice test here.
Other fixture modes: failure/malformed. --variant-timeout defaults to 30 seconds.
The isolated CLI worker bounds hangs; direct adapter calls are synchronous and
depend on the backend returning. Process termination is timeout, not successful
cooperative cancellation. Checkpoints preserve partial resource metrics.

TTS metrics: load_ms, last_synthesis_attempt_ms; per-WAV synthesis_ms,
audio_seconds, sample_rate, synthesis_rtf; total generated corpus_duration_seconds,
sample_rates; shared resources; cancellation/cancellation_ms, status/reason/
failure_type and platform metadata. first_playable_audio_ms stays null (unmeasured).
human_quality_evaluation=NOT_TESTED is not an automated quality score.
Fixture timings do not predict real synthesis speed or voice quality.

## Phase 3D.0 readiness audit — 2026-10-06

Audit/design only. No runtime changes, installs, downloads, model selection or
new hardware benchmarks. Phase 3C.1 edits remain in the working tree unchanged.
Labels: IMPLEMENTED = code exists; TESTED_WINDOWS = local tests;
MOCK_TESTED = simulated hardware/dependencies; LIVE_PI = user's prior hardware
report, not reproduced here; NOT_TESTED / NOT_IMPLEMENTED retain literal meaning.

### Evidence and primary constraints

LIVE_PI (user report, event date unspecified): Python3.13.5, Linux/aarch64,
positive Pi detection, 905MB total/~550–560MB available RAM,4 cores,no CUDA;
USB mic44100Hz, live audio-test speech_detected and clipping detection work;
normal capture did not clip. Output enumerated, playback NOT_TESTED. Confirmation
Vosk model cached (not proof of recognition quality). Core/text/vault/Gemini/
shutdown validated. Natural STT/E2E NOT_VALIDATED; Linux TTS NOT_IMPLEMENTED.

### 1. Actual unrestricted / confirmation paths

`ValleRaApp._voice_loop → _listen_for_turn` (`core/app.py`) calls
`SpeechListener.listen_once(15.0, None)` (`core/stt_listener.py`) in a thread.
Listener selects device/rate via inherited `VoskListener._input_candidates`,
then `services/audio/pcm_capture.capture_pcm → AcousticEndpoint` captures PCM16
mono. No Vosk decoder is constructed for unrestricted primary capture.
`resolve_profile → create_backend` ran during listener construction;
`_prepare_stt → listener.prepare` optionally preloads in background.
`_recognize_capture → RefinementWait.run → primary.transcribe` returns
`RecognitionResult`; empty/low/nonfinite confidence, unreliable or failed job
marks failed; optional configured Vosk fallback stays unreliable. Capture
truncation and possible_fragment propagate to fragmented/incomplete. App
`_enqueue_command` creates `TurnEnvelope`, `_command_loop` claims DispatchGuard
then `CommandProcessor.process`; microphone epoch/cancellation remain guards.
RecognitionPolicy.action_eligible is the compatibility `not fragmented`, NOT
permission to execute; ActionPolicy/direct request/confirmation still apply.

Confirmation: app snapshots request_id, calls `_listen_for_turn(timeout,
ConfirmationService.GRAMMAR)` → `SpeechListener.listen_once` delegates to
`VoskListener.listen_once/_listen_with_device`; builds a fresh KaldiRecognizer
with `ConfirmationService.recognition_grammar(model)` (known unknown-token
variant included), native endpoint, 80ms energy gate. No Whisper refinement
with grammar. Actual text/confidence → `_decision`/`submit(...,request_id)`;
unknown/low confidence fail closed, stale replies rejected. Cached Vosk Model
is shared within listener, but grammar/decoder state is per capture. Separation
is IMPLEMENTED/MOCK_TESTED and must remain; natural Vosk baseline is offline only.

### 2. Sample-rate facts and coverage

`_input_candidates` reads each input's default_samplerate; only nonpositive
rate falls back to config stt.sample_rate=16000. Thus reported Pi44100 implies
RawInputStream requests44100, NOT16000; capture carries that rate into backend.
No explicit application resampler. No claim that PortAudio converts44100→16000:
the code requests the reported rate, and actual host/device negotiation is not
instrumented. PortAudioError tries another candidate; no automatic16000 retry
for the same device. STREAM default rate metadata is not measured clock proof.

- Vosk: KaldiRecognizer(model, actual_rate), raw PCM. Resampling, if needed,
  belongs to native recognizer. Cached model does not validate44100 inference.
- Faster-whisper: `_wav_buffer` writes actual rate into WAV header; installed
  faster_whisper1.2.1 `transcribe.py/decode_audio` reads file-like audio and
  `audio.py` uses PyAV AudioResampler at feature-extractor rate (16000).
  This is inspected local dependency code, NOT Python3.13/Pi validation.
- Sherpa/Moonshine: `accept_waveform(actual_rate, float32_PCM)`; no app-level
  conversion. Native44100 handling must be verified against installed Pi runtime.
  Do not silently relabel44100 bytes as16000; no such relabelling found here.

Tests in test_audio_blocks cover16000/22050/48000 energy/timing; primary fixtures
mostly16000. No complete fake44100-device→backend/resampler→result regression
found. Add that focused check before live44100 inference in Phase3D. No demonstrated
rate correctness blocker from this audit; backend44100 acceptance remains a gate.

### 3. Acoustic endpoint (unchanged)

PCM callbacks capped at100ms (min(config audio_block_ms,100)); RMS analysis20ms.
Activity threshold=max(.0025,min(.08,noise_threshold*.75)); >=250ms cumulative
active audio establishes speech. Span<1s uses short700ms silence;1–3s uses
configured silence (0 becomes1200ms);>=3s uses max(normal,long1600ms).
These are code/default values, not a claim about private Pi config. App onset
budget15s; after speech detection deadline backdated to onset, max15s speech,
absolute bound30s including initial wait. Retain300ms pre-roll, trim leading
silence, no-speech returns empty. Bounded callback queue256; any callback status
or queue overflow marks capture_truncated. Endpoint waits until queued blocks
drained. Poll cancellation every<=50ms plus processing; flags suppress dispatch.
AcousticEndpoint is backend-independent. Runtime PCM capture has no dedicated
clipping metric; health `audio_probe` computes peak/RMS/clipping separately.
Reuse capture/endpoint unchanged; no threshold tuning in this audit.

### 4. Backend / residency inventory

| Backend | Status / loading | Rate, confidence, lifetime and limitations |
|---|---|---|
| Vosk | IMPLEMENTED_RUNTIME constrained and explicit low-resource; local Model path, no download | Fresh KaldiRecognizer per transcribe, SetWords mean word confidence; offline unrestricted has no grammar. Model cached with load lock, close drops reference; no native timeout/cancel mid-call. Result alone is not ACTION approval. |
| faster-whisper | IMPLEMENTED_RUNTIME; local files enforced through primary_settings/create_backend; Windows quality unchanged | WAV/PyAV rate path; forced uk/transcribe, word probability or exp(avg_logprob) combined with speech/language factors, not calibrated safety probability. Resident spawned worker, serialized pipe; model switches drop previous reference (no simultaneous intended model slots). Benchmark process=False loads inside isolated candidate. |
| sherpa-onnx | IMPLEMENTED_RUNTIME experimental, NOT_VERIFIED_ON_PI | Local `models/<profile.model>` or absolute directory; CPU threads=max(1,stt_whisper_cpu_threads), default2. Native call within locked daemon-drain thread in Core process. Confidence0 plus unreliable/incomplete/fragmented=True; not ready for trusted conversation/ACTION even if text exists. |
| Moonshine v2 ONNX | BENCHMARK_ONLY (`testing/probes/moonshine_candidate.py`) | Local encoder_model.ort, decoder_model_merged.ort, tokens.txt; sherpa OfflineRecognizer.from_moonshine_v2 CPU. No forced-language argument/independent Ukrainian model validation; label/path alone is not proof. Confidence0/unreliable; no production registration or timeout inside adapter. |

No new candidates proposed. Existing Windows Speech/RHVoice instructions are not
a Linux TTS adapter. No implemented Pi TTS candidate found. ONNX runtimes/libraries
and faster-whisper native dependency compatibility on ARM64/Python3.13 remain
NEEDS_LIVE_VERIFICATION, regardless of platform-neutral adapter Python code.

Sherpa details: requirements-edge.txt `sherpa-onnx>=1.12`; int8 expects exactly
one `*-encoder.int8.onnx`, `*-decoder.int8.onnx`, `*-tokens.txt`; float32 uses
`*-encoder.onnx`/`*-decoder.onnx` (ambiguous matches rejected). Empty files and
`.en-` names rejected. from_whisper(language='uk',task='transcribe',provider='cpu');
hotwords/initial_prompt rejected explicitly. Model stays resident; close sets
event and releases only if native job no longer owns lock. Preparation exceptions,
including Python MemoryError, return false/detail; inference exceptions go to
drain failure. Native crash/OS OOM can terminate Core; no isolation/RAM guard.
No package/wheel check has been made on real Python3.13.5/ARM64 in this audit.

### 5. Timeout is not native cancellation

RefinementWait allows one active drain job, soft=min(4000,primary_timeout_ms),
hard=primary_timeout_ms(default60000). Timeout bounds waiting, not CPU/RAM use;
late result remains private mailbox, no dispatch; next request returns busy.
Sherpa prepare preload is outside this wait and no hard kill exists. Close can
defer model destruction until decoding returns. Faster-whisper worker can be
terminated/reaped on shutdown/error; ordinary externally budgeted transcription
drains without the pipe's120s limit. Do not kill native thread or permit concurrent
jobs to make a UI timeout look faster. Optional Vosk fallback runs synchronously
after primary failure and has no extra hard budget; still ACTION-ineligible.

### 6. Edge selection and memory

`default_profiles()['edge']` hardcodes sherpa-onnx-whisper-small/int8/CPU.
resolve_profile auto ARM→edge; performance raspberry_pi existing alias sets edge.
target_ram_mb=2048 and target_rtf=1.0 are reporting targets, NOT hard runtime
guards or edge selection criteria. Quality auto has separate fixed RAM/VRAM
requirements, not these target fields.905MB Pi cannot be presumed suitable for
2048MB target. No OOM avoidance based on target_ram_mb; don't preload this candidate
just because profile resolves successfully. Initial run/model viability gate first.

Confirmation Vosk weights become resident lazily; one listener VoskBackend shares
model with its fallback, not recognizer grammar. Benchmark new backend/process
loads another Model; running alongside Core duplicates residency, unknown amount.
Keep Core stopped for isolated candidate tests. Core/NumPy/PCM (up to ~30s at
44100), queue/copies, float32 conversion, confirmation model, primary model and
future TTS may coexist. Default background prepare may load STT/TTS concurrently.
Whisper process close unloads; in-process Whisper close is effectively absent
(isolated benchmark exit frees it). Vosk drops reference; Sherpa deferred close.
Future simplest options: retain only candidate proven to fit, lazy TTS, sequential
STT/TTS load/unload if measured necessary. No scheduler designed/implemented.

### 7. Offline benchmark readiness

`tester.py --probe stt_benchmark` → `testing/probes/stt_benchmark.py`: sequential
spawned candidate processes, local files, no LLM correction/downloads. Each candidate
prepare, first inference excluded from warm stats, then corpus. WER/CER, mean,
median,p95,weighted RTF, per-row reference/transcript/confidence, load_ms,
first_inference_ms, metadata/device/compute, versions, completed/failed/timeout/
NOT_TESTED exist. completed means measurement finished, not usable recognition.
Warmup result validity is specifically checked for Whisper, not every candidate;
crashed worker reports worker_exit, not proven OOM. Overall exit0 requires only
ONE completed variant; inspect all variants, not exit code alone.

Resources: isolated process sampled RSS max every50ms including load; no RSS
series/baseline/system-available/CPU/swap/temp in this probe. Hardware total RAM
available. Older `testing/probes/whisper.ResourceSampler` has min system available
RAM, worker RSS/private every200ms, but lives in a synthetic Windows-TTS-oriented
comparison, not a general Pi baseline. Reuse measurement logic, not that workflow.
RSS is sampled, not guaranteed true peak; OS OOM may lose the child report.

Private corpus inspected headers only:24/24 confirmed, non-synthetic WAV,
44100Hz monoPCM16,107.46s total. Same files/corpus.json can be transferred privately
unchanged; reader verifies SHA256 and confirmed references,1..240 samples allowed.
No audio/transcripts added to Git. Reader loads entire PCM corpus in parent and
candidate; account for that overhead, not just model weights. No subset-count CLI;
derive separate local manifests for1 and3–5 files with unchanged references/hashes.
Never overwrite canonical24 manifest. Matrices: stt_matrix.json and
stt_latency_matrix.json; use explicit candidate filters, NOT the default multi-model list.

Minimal Phase3D measurement extension: same psutil sampler captures baseline/peak
RSS (candidate and orchestration context), available RAM before/load/min-inference,
CPU-time deltas with wall time (state one-core vs machine normalization), swap
sin/sout deltas, numeric temp/throttling if locally available with bounded read.
Unavailable sensors=null/NOT_TESTED. No serial/MAC/env/proc dumps. Store periodic
small parent-visible numeric samples so a killed child does not lose all evidence.
Swapping/thermal throttling confound latency; flag runs, don't rank contaminated
results as clean wins. No monitoring framework or implementation in this audit.

### 8. TTS architecture / minimal future contract

LLM/processor response → SpeechBuffer → Speaker.say(console/UI) → bounded queue8
SpeechRequest(timing,generation) → _worker/_speak_sync/_speak_with_output → platform.
Windows default output uses resident PowerShell/System.Speech direct synthesis/
playback (NO per-phrase WAV). Selected output uses cached WAV → RawOutputStream;
platform winsound fallback. windows_tts owns transport; Speaker owns generation,
queue, cancellation, timing/UI. stop increments generation, drains queue,
terminates/reaps child, clears playback; stale requests cannot play.
TTSCache disk key voice/rate/text, no engine identifier or size eviction.
Linux Speaker.say currently returns after console/UI (no audio queue);
probe reports NOT_AVAILABLE with TTS NOT_IMPLEMENTED detail. Simply enabling
supports('tts') would still route through Windows-named synthesis branches:
must add a narrow Linux adapter selection, not just flip capability flag.

Small proposed contract, not code: prepare/status; synthesize(text, voice/rate,
destination, cancellation) produces validated finite PCM WAV with explicit rate/
channels/sample width; close/stop with clear timeout and resource ownership.
Start WAV-first unless evidence justifies streaming. Optional unsupported voice
is an explicit error/fallback report. Shared Speaker owns generation/queue;
discard late audio, never dispatch from backend callback. Linux playback must
support default sounddevice device=None as well as selected device without
falling into Windows Speech/winsound. Cache namespace must separate engine/model/
voice so old audio cannot masquerade as new engine. Keep Windows path untouched.

Existing tester probe `tts` is Windows-only GetProcessTimes + resident SAPI
PCM/pulse/stop/restart, audible; NOT a Linux candidate benchmark. Health
tts_self_test measures one Windows WAV/voice/duration, not multi-engine latency.
Do not reuse either as proof of Pi playback. Proposed separate backend-neutral
probe via tester.py: isolated candidate, fixed neutral Ukrainian sentence IDs,
cold init, first/warm synthesis, WAV validity/audio seconds, RTF(synthesis/audio),
first playable chunk only when available (else null), CPU/RSS/available RAM,
errors/repeated stability and cancellation. Human intelligibility/pronunciation/
naturalness comments stored separately with evaluator/date; no fake quality PASS.
No candidate or harness implemented during audit.

### 9. Timing coverage

CapturedAudio carries capture/speech-start/end-estimate/endpoint/return timestamps;
stt.capture logs them, speech onset not carried in TurnTiming. Primary span,
endpoint_to_stt_start and recognition_ready exist; whisper.inference worker detail
exists, equivalent native sherpa stage detail absent. App marks dispatch;
LLM request_including_callbacks, llm.first_text/provider first_text exist;
request start logged as span, not always a separate turn-relative event.
Speaker marks first_phrase,tts_start,tts_submit; queue/synthesis/playback durations
exist. tts_submit/speak_call/_set_playback are SOFTWARE submission, not acoustic
first sound. TurnTiming.finish total ends at processor completion, not final audio;
no universal end-of-response acoustic metric. Actual first sound requires local
loopback/physical measurement; future backend first PCM write is only a proxy.
Keep optional/perf-disabled behavior; don't mislabel software timestamps as sound.

### 10. Python3.13/ARM64 dependency inventory (no installations)

| Dependency | Classification / evidence |
|---|---|
| vosk>=0.3.45 | Python wrapper + NATIVE_EXTENSION/shared lib (CFFI); cached weights and successful Core import imply package availability from user run, not unrestricted inference44100 validation. |
| sounddevice>=0.5 + CFFI/PortAudio | Python wrapper + native runtime; CURRENTLY_INSTALLED_ON_PI inferred from reported live audio-test; playback still NOT_TESTED. |
| numpy>=2.0 | NATIVE_EXTENSION; exercised by Pi audio-test, exact installed version UNKNOWN. |
| psutil>=6.0 | NATIVE_EXTENSION; Pi Doctor numeric resources exercised. |
| cryptography>=43.0 | NATIVE_EXTENSION/transitive build needs; successful Pi vault supports current installed build, exact version UNKNOWN. |
| faster-whisper>=1.2.1 | Python orchestration with native CTranslate2, PyAV, tokenizers, ONNX Runtime VAD; ARM64/Python3.13 wheels/runtime NEEDS_LIVE_VERIFICATION. Windows installed build is not evidence. |
| sherpa-onnx>=1.12 | NATIVE_EXTENSION/ONNX runtime build; NOT_VERIFIED_ON_PI; no wheel availability claim. Moonshine needs specific API, minimum version alone insufficient proof. |
| google-genai/httpx/dotenv | Python-level orchestration; transitive native dependencies possible (e.g. pydantic-core). Live Pi Gemini validates current combination only. |
| rapidfuzz | Native-accelerated package; current Core import is not benchmark evidence; exact build UNKNOWN. |
| pywin32/pyttsx3/PyGetWindow/pyautogui | Windows-marked requirements or Windows usage; not Pi voice requirements. |

Inspect actual Pi versions/module imports and wheel/platform tags before selecting
a candidate. No speculative pins/downgrade of Python; no blanket3.13 support claim.

### 11. Minimal Phase3D implementation map / protected boundary

| Module | Current role → smallest conditional change | Pi validation |
|---|---|---|
| testing/probes/stt_benchmark.py; reusable ResourceSampler logic | Candidate measurement → system RAM/CPU/swap/temp, parent-visible crash evidence, uniform warmup validation | Required |
| tests/test_audio_backends.py / audio blocks | Mostly16k fixtures →44100 device→backend contract tests | Mocks then real |
| testing matrices | Candidate lists → one explicitly provisioned candidate per experiment; no production switch | Required |
| testing/probes/new TTS probe + tester.py registration | Windows-only probe → neutral isolated WAV benchmark, fake backend tests | Required |
| services/platform/linux.py,resolver.py; small Linux TTS adapter | No TTS → chosen measured candidate prepare/synthesize/stop/close/playback | Required |
| core/speak.py; core/tts_cache.py | Windows-bound synthesis branch/cache → narrow platform branch and engine-aware cache; preserve shared queue/generation | Windows + Pi |
| services/audio/backends.py/sherpa_backend.py | Existing adapters → only proven compatibility/lifecycle correction if experiment requires; no fabricated confidence | Required |
| config.py / profiles.py | Current candidates/defaults → only after evidence; memory target not a promise | Required |

Avoid changing core/models.py TurnEnvelope/RecognitionResult, action_policy,
confirmation, natural-turn/direct-request checks, dialogue/processor routing,
dispatch guard, memory/security/SecretStore, LLM/fallback, diagnostics privacy,
Windows Speech/windows_tts, primary Windows quality, endpoint tuning/download
policy. Voice capture should change only for a demonstrated rate bug. Do not
set confidence=1 for ONNX, clear unreliable to enable actions, share grammar
decoder across turns, kill active native threads, introduce auto-download or
headless GUI dependency. Any future safety-policy need is a separate reviewed task.

### 12. Exact staged Pi experiment plan (future, not run)

A. Stop Core/other candidates. Record numeric resources, Python3.13.5, package
versions, config snapshot without secrets. `python install.py --profile raspberry_pi
--check`; `python main.py --doctor`. Provision ONE chosen candidate explicitly,
verify imports/API/local weights/rate support; no production defaults changed.
Dependency failure means NOT_TESTED, stop candidate, do not compile/install blindly.

B. One private confirmed WAV in separate1-sample corpus. First reuse already
cached Vosk as BASELINE ONLY; one cold load/first inference and warm pass. Check
nonempty output, actual44100 rate, numeric memory. Stop on OOM/crash or incompatible
runtime; don't retry indefinitely. TTS equivalent is one neutral sentence, WAV
validation then explicit playback (output enumeration alone insufficient).

C. Separate3–5-sample corpus, short/long/pause cases, one candidate. Check WER/CER,
latency/RTF, RAM and swapping/throttling before24 files. Stop repeated crashes,
sustained memory collapse/swap growth or clearly unusable latency; existing
target_rtf=1 is a reporting goal, NOT sufficient winner threshold. Do not invent
new numeric acceptance thresholds without user/hardware evidence.

D. Full unchanged24 WAV only for surviving candidates, sequential fresh processes.
Existing command (replace private directory):

```sh
python tester.py --probe stt_benchmark --allow-live --timeout 700 -- --corpus /private/corpus24 --matrix testing/stt_matrix.json --candidates vosk --device cpu --variant-timeout 600
```

Same command with corpus1/corpus-small for B/C;600s is existing candidate cap,
700s outer harness deadline avoids its default300s killing the600s worker first.
These are execution limits, not winner thresholds. Do not run entire matrix.
Wait for worker exit before next candidate; compare cold/warm separately. Reports
contain transcripts and stay private. Future TTS probe CLI is NOT_IMPLEMENTED;
specify it when the neutral harness is added, not a fictitious runnable command.

E. Surviving STT through actual microphone44100 + unchanged endpoint; evaluate
recognition/repeat behavior, confirmation yes/no/stale/cancel, software timings.
Keep unknown-confidence candidates offline or fail-closed; no tools used as ASR
accuracy tests. Explicit short playback test and human Ukrainian voice assessment.

F. Run Core + confirmation model + candidate STT + candidate TTS; measure total
available RAM, all process residency, swap and thermal condition over repeated
turns, cancellation/shutdown, late results/no duplicate response, headless/text-only.
If coexistence fails, test sequential lazy load/unload rather than weakening
safety or masking swapping. No combined-stack readiness until live evidence.

### Audit validation

Existing `tester.py --module stt voice tts performance --verbose`:197/197 PASS
on Windows (mock/offline unit tests, not acoustic or Pi validation).
`tester.py --all --filter stt_latency_benchmark --verbose`:4/4 PASS, fake candidate
validation only (no inference benchmark).
No new test or harness code required for this documentation-only audit. Header inventory was
read-only, not recognition. Full suite/release/Doctor not rerun for documentation;
previous Phase3C.1 results are historical, not this audit's results.


Natural speech uses `SpeechListener → capture_pcm → selected STTBackend`
without constructing a Vosk decoder or waiting for Vosk text. Backend output is
the existing `RecognitionResult`; `TurnEnvelope` and dialogue/action policy are
unchanged. `STTBackend` accepts PCM16 mono and sample rate on Windows and Linux.

## Configuration

Under `stt` in config.json:

```json
{
  "backend": "auto",
  "profile": "auto",
  "primary_device": "auto",
  "primary_local_files_only": true,
  "fallback_backend": "none",
  "primary_timeout_ms": 60000
}
```

Profiles are deployment records in `stt.profiles`, not model identities:

| Profile | Initial candidate (replaceable) | Device | Advisory RAM / RTF target |
|---|---|---|---|
| quality | faster-whisper large-v3 | auto, or explicit CPU | 8192 MiB / 1 |
| balanced | faster-whisper small | CPU int8 | 3072 MiB / 1 |
| edge | sherpa-onnx multilingual Whisper small int8 | CPU | 2048 MiB / 1 |

These are candidates and resource **targets**, not measured consumption, strict
memory limits, or winners. Turbo/medium are candidates in testing/stt_matrix.json.
Each record accepts backend/model/device/compute_type/target_ram_mb/target_rtf
and optional hotwords/initial_prompt; examples are in config.example.json.

Only services/audio/capabilities.py detects hardware: architecture aliases, OS,
CTranslate2 CUDA availability, optional psutil RAM and optional NVML VRAM.
No computer product names. Auto: ARM → edge; other CPU → balanced; quality only
with detected CUDA, known RAM >=16 GiB and VRAM >=8 GiB. These are conservative
selection gates, not proof a model fits. Unknown VRAM means balanced; explicit
quality still works. Balanced/edge are CPU-only. Installed runtime availability
is recorded, not used to silently substitute a weaker decoder.

Explicit stt.profile wins over old quality_profile. Legacy quality stays quality;
old balanced preserves turbo; old low_resource maps to edge preserving its
explicit legacy Whisper model. New profile=low_resource is an edge alias.
Explicit stt.backend=vosk is preserved, never an automatic natural-speech default.
The legacy --profile raspberry_pi switch maps STT to edge, not a separate OS.
No existing config file is rewritten. New examples use edge.

Legacy `stt.whisper.model/device/compute_type` configure old refinement/probes,
not the new primary profile. Other Whisper tuning, including CPU threads, beam,
confidence, `hotwords` and `initial_prompt`, remains under `stt.whisper` (the
initial prompt key is `prompt`). Hints are optional and may bias transcription.

Primary language is always `uk`, task `transcribe`. Quality device `auto` selects CUDA when
CTranslate2 detects it, otherwise CPU int8. CUDA load/inference failure retries
the **same** model on CPU and logs why; it does not substitute a smaller model.
No GPU was available for a live CUDA test in this iteration. See the
[upstream installation requirements](https://github.com/SYSTRAN/faster-whisper).

Models are never downloaded by the primary conversation/benchmark path, even if
the legacy primary_local_files_only field is false. Only the explicit preparation
command grants allow_download to the factory. Explicit installation:

```powershell
python tools/download_whisper_model.py
```

This command downloads/prepares the selected primary profile; large models need
substantial disk space and RAM. On CPU, large-v3 is not a latency guarantee.
Until a selected model is installed, natural voice recognition is unavailable
unless an explicit fallback is configured; text input and Vosk confirmation
remain separate. Never silently label an unavailable model as ready.

## Capture and safety

This acoustic endpoint is unchanged by the deployment-profile continuation.

The primary path uses acoustic RMS endpointing (20 ms analysis frames inside
configured audio blocks), not Vosk partial/final tokens. It requires 250 ms of
accumulated energy above the configured threshold. Quiet timeout uses
`endpoint_silence_ms` (1200 ms when zero); after 3 s of active speech it is at
least 1600 ms. Existing maximum capture duration is retained. This new acoustic
path needs a live microphone test; fan noise and long pauses remain limitations.
Legacy Vosk endpoint/Fragment Guard code has not been rewritten. Its pre-ASR
text-based continuation window is not used in the primary path; incomplete
text is marked conservatively after transcription, not guessed into a command.

Constrained grammar requests still use the existing Vosk listener, including
confirmation grammar, parser, request correlation and short negative replies.
Explicit `backend=vosk` is a low-resource option, not the natural-speech default.

Primary inference reuses the existing single-job drain: the bounded wait does
not kill inference or start duplicate jobs. Timeout/cancellation discard the
late result. Shutdown retains process ownership. The default primary wait is
60 s (configurable 1–300 s), warning at min(4 s, timeout); it includes cold load
if not preloaded. A still-running job causes the next attempt to report busy.

Fallback to Vosk requires `fallback_backend=vosk`. Any failed/timed-out primary
result or fallback is marked `recognition_unreliable` and clarification-required;
high Vosk confidence cannot restore ACTION eligibility. Truncation and
linguistically incomplete text remain separate provenance flags. Confirmation,
direct-request validation and execution verification are not changed.

## Offline comparison

```powershell
python tester.py --probe stt_benchmark --allow-live --timeout 1800 -- --corpus PATH_TO_CORPUS --models vosk base large-v3 large-v3-turbo --device cpu
```

`--allow-live` is the existing tester opt-in for all probes: this probe is
**offline**, never downloads and never invokes an LLM. Use `--device auto` or
`cuda` for a separately measured GPU run. `--variant-timeout` bounds each child
process (default 600 s). Each model reads the exact same manifest-confirmed,
SHA-256-validated WAV files. The manifest format is the existing recorded corpus
format (`samples`: id/file/reference/reference_confirmed/wav_sha256).

Private reports under `logs/stt-benchmark` contain references and transcripts,
micro-averaged WER/CER, mean/median/p95 decode latency (nearest-rank p95), RTF,
backend/model/device/compute, hardware/OS metadata, load time and sampled process
peak RSS (50 ms sampling, not exact allocator peak). CER includes normalized
spaces; normalization is NFKC/casefold with punctuation removed. Missing models
are `NOT_TESTED`, not failures or benchmark successes. VRAM is explicitly not measured.
Do not publish personal transcripts. Functional unit tests use fake decoders
and are not evidence of recognition quality.

## Experimental edge adapter — not production-ready

SherpaOnnxBackend uses CPU-only offline Whisper. Multilingual Whisper supports
Ukrainian, unlike `.en` models. Sources: [ARM64 support](https://k2-fsa.github.io/sherpa/intro.html),
[ONNX models/export](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/whisper/index.html),
[Ukrainian language token](https://github.com/openai/whisper/blob/main/whisper/tokenizer.py).
ARM64 runtime support does not prove Ukrainian accuracy, memory fit or RTF on Pi.
English-only tiny.en benchmarks are not evidence for this use case. No other
language-specific ONNX family was selected without Ukrainian model evidence.

Install requirements-edge.txt explicitly. Obtain/export a multilingual small
model from the upstream instructions, placing `small-encoder.int8.onnx`,
`small-decoder.int8.onnx`, `small-tokens.txt` inside
`models/sherpa-onnx-whisper-small/`. Or configure another directory (absolute,
or relative to models/). Exactly one matching file per component is required;
`.en` filenames are rejected. Float32 ONNX is supported via compute_type=float32.
No automatic ONNX download occurs; model preparation errors give profile/path/help.

This API does not provide calibrated confidence. Its transcripts are usable for
offline scoring but runtime marks them unreliable and clarification-required.
**Trusted natural voice turns and voice ACTION are not enabled by this experimental
adapter.** This is an integration limitation, not a claim edge deployment is solved.
Unsupported prompt/hotwords produce an explicit preparation error.

An ONNX request uses the existing single drain thread. Close sets a closed flag;
native state is released after the active decode completes. A timed-out job is
not killed, duplicated or dispatched later. Unlike the Whisper child process,
ONNX inference is not forcibly terminable; preparation/OOM have no memory sandbox.

## Semantic annotations and benchmark matrix

Add an optional `semantic` object to a corpus sample, preserving its WAV/hash:

```json
{
  "intent": "web_search",
  "intent_phrases": ["пошукай", "знайди"],
  "entities": {"query": ["рецепт шаурми", "рецепти шаурми"]}
}
```

The object belongs under `samples[i].semantic`, not at corpus root. Intent label
alone is descriptive; intent_phrases supplies manually annotated alternatives.
Normalized whole-token contiguous matching counts preserved intent evidence and
each critical entity. Report includes annotated denominators; absent annotations
are null, not 100%. This is a lexical proxy, not general semantic understanding:
negation/paraphrases require carefully annotated phrases. Nothing here changes
production intent/action policy. No LLM correction or full production LLM is used.

The matrix has Vosk/base baselines; large-v3 quality; small/medium/turbo CPU;
multilingual small ONNX edge. Matrix entries can replace backend/model/device/
compute independently. Same references and WAV hashes are used for every entry.
Unavailable runtime/model returns NOT_TESTED with a preparation reason. A probe
may finish successfully with partial coverage: inspect every candidate status.

### Later: GPU workstation (PowerShell, from repo root)

Set stt.profile=quality in config.json; GPU libraries per upstream instructions.

```powershell
.\.venv\Scripts\python.exe tools/download_whisper_model.py --profile quality
.\.venv\Scripts\python.exe tester.py --probe stt_benchmark --allow-live --timeout 1800 -- --corpus logs/voice-corpus/ID --matrix testing/stt_matrix.json --candidates large-quality --device cuda
```

### Later: CPU x86 laptop

Set stt.profile=balanced. To test turbo/medium, first set that profile's model,
then explicitly prepare it; downloading small does not install other candidates.

```powershell
.\.venv\Scripts\python.exe tools/download_whisper_model.py --profile balanced
.\.venv\Scripts\python.exe tester.py --probe stt_benchmark --allow-live --timeout 1800 -- --corpus logs/voice-corpus/ID --matrix testing/stt_matrix.json --candidates vosk base small-cpu medium-cpu turbo-cpu --device cpu
```

### Later: ARM64 Linux / Raspberry Pi (not verified on hardware)

Use a 64-bit OS/Python and system audio dependencies from install.py. Create a
venv, install base + edge requirements, set stt.profile=edge. Put the ONNX files
in the documented directory and transfer the same private WAV corpus securely.
Do not upload recordings to the repository. Commands below use the local venv:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-edge.txt
.venv/bin/python tools/download_vosk_model.py
.venv/bin/python tools/download_whisper_model.py --profile edge
.venv/bin/python tester.py --probe stt_benchmark --allow-live --timeout 1800 -- --corpus logs/voice-corpus/ID --matrix testing/stt_matrix.json --candidates edge-small-onnx --device cpu --variant-timeout 1200
```

Replace ID with an actual corpus directory. Preparation validates local ONNX;
it does not fetch the files. ARM wheels, Pi RAM and Ukrainian RTF are NOT TESTED
here. The experimental confidence limitation still prevents a production voice
claim even if the offline benchmark completes. This is not Phase 3B: Windows
application control/TTS portability is outside this change.

## Recorded result, 03.10.2026 (not a profile acceptance claim)

Same 24 Ukrainian WAV: faster-whisper small CPU/int8 on Windows x86_64,
RAM3791MiB, CUDA unavailable: WER24.47%, CER8.97%, mean7340ms,
median7278ms, p958153ms, RTF1.639, model load5853ms, sampled peak RSS579.7MiB.
RTF<=1 target is NOT met. Large-v3/turbo/ONNX were NOT_TESTED (missing models;
ONNX runtime also unavailable). Medium was not run. Cached tiny/base were not
rerun here; old baseline results remain separate. No semantic annotations exist
in this corpus: semantic accuracy is null, not a claimed success. No ARM64/GPU
hardware validation was performed. Functional suite on 04.10: 637/637; this does
not validate acoustic quality. Architecture is ready for profile-specific offline
benchmarking, but production edge confidence and interactive CPU quality remain open.

## Performance pass (04.10.2026): validation candidate, not a GPU speed claim

CUDA failures now retain exception type/message/traceback, model, device,
compute type and installed faster-whisper/CTranslate2 versions in the debug/session
log. Only explicit CUDA runtime/driver/cuBLAS/cuDNN failures permit one same-model
CPU retry. Generic RuntimeError, input/decode, corrupt weights, and API/config errors
do not. A successful same-model CPU result is not automatically unreliable;
weaker Vosk fallback still is. No dependency was reinstalled or pinned speculatively.
The original RTX exception message was unavailable on this CPU-only host: its
actual cause remains UNIDENTIFIED until the diagnostic is run on that machine.

Current upstream requirements: CUDA 12 cuBLAS and cuDNN 9 for current CTranslate2
([faster-whisper](https://github.com/SYSTRAN/faster-whisper#gpu),
[CTranslate2](https://opennmt.net/CTranslate2/installation.html)). Check the exact
installed version, driver and library search path before changing packages.
Model load success is not evidence of successful CUDA inference.

### Optional fast-first GPU candidate

In `stt.profiles.quality`, set:

```json
{"backend":"faster-whisper", "model":"large-v3-turbo", "device":"cuda",
 "compute_type":"float16", "target_ram_mb":8192, "target_rtf":1.0,
 "escalation_model":"large-v3", "escalation_confidence":0.8}
```

Select `stt.profile="quality"`; remove legacy `quality_profile` overrides.
Both models must already be cached. No automatic download. The new profile
fields are optional: defaults remain unchanged until comparative GPU data exists.
This is deliberately NOT a claim that turbo won. Balanced small/CPU and edge
contracts remain intact. Base/tiny are comparison baselines, not new defaults.

One worker/request: primary returns immediately when nonempty, finite confidence
>=0.8, reliable and without the existing lexical incomplete hint. Otherwise one
large-v3 pass is allowed; runtime errors do not trigger it. Still-uncertain quality
output stays unreliable/incomplete. No intent rewriting, LLM correction or new
ACTION permission is introduced. Existing confirmation checks remain authoritative.
This does not detect every semantic/entity mistake in a confident transcript.

Only one model slot is resident. Switching drops the previous model; the next
turn restores primary lazily, so escalations cost load latency. Dual residency is
NOT enabled without VRAM evidence. Primary stays warm across ordinary turns.
The existing bounded drain owns the entire cascade; timeout/cancellation discards
its late result and prevents parallel jobs. Native inference is not cancellable
mid-call; a timed-out worker may finish computation but cannot dispatch a turn.

### Capture/endpoint measurements

PCM only uses <=100ms blocks. Under one second of speech span, the configurable
`pcm_short_silence_ms=700` applies; medium speech retains the configured silence
or 1200ms; >=3 seconds retains `pcm_long_silence_ms=1600` (at least the base).
These are cautious acoustic heuristics, not sentence completion detection.
Vosk confirmation and Fragment Guard are untouched. Short internal pauses can
still be ambiguous; long utterances do NOT yet achieve the 300–700ms UX target.

Capture logs include capture start/return, estimated speech start/last/end,
endpoint timestamp/reason, effective threshold, overflow, speech span and active
audio duration. `stt.latency` separates capture_total_ms, speech_end_to_endpoint_ms,
endpoint_to_stt_start_ms, stt_inference_ms, endpoint_to_transcript_ms and
speech_end_to_transcript_ms, plus model/backend/device/compute/fallback/escalated/
cold_or_warm. Energy timestamps are estimates based on callback arrival, not
ground-truth word boundaries. Timeout/busy never reuses a previous inference time.

A 15s capture can contain actual speech, initial silence, continuing noise above
the energy threshold, or a recording deadline. Previous aggregate timing alone
cannot identify which. `core/app.py` passes a 15s capture deadline, including
initial silence. Thus silence-only polls and sustained noise can reach 15s;
it is NOT a 15s inference or mandatory post-speech wait. This deadline is unchanged. Use
endpoint_reason=capture_limit, speech_span_ms, active_audio_ms and overflow to
diagnose the next live session. Silence without >=250ms active signal skips ASR;
continuous environmental noise can still pass this energy gate.

### RTX 4060 verification (PowerShell, repo root)

```powershell
nvidia-smi
.venv/Scripts/python.exe -c "from services.audio.cuda_diagnostics import runtime_versions; print(runtime_versions())"
Get-Command nvidia-smi
where.exe cublas64_12.dll
where.exe cudnn64_9.dll
.venv/Scripts/python.exe tester.py --probe stt_benchmark --allow-live --timeout 2400 -- --corpus logs/voice-corpus/ID --matrix testing/stt_matrix.json --candidates turbo-cuda-fp16 turbo-cuda-int8 large-cuda-fp16 --variant-timeout 600
.venv/Scripts/python.exe tester.py --probe stt_benchmark --allow-live -- --corpus logs/voice-corpus/ID --endpoint-only
```

Replace ID with the existing corpus directory on that PC. `where.exe` checks PATH,
not every possible DLL search location. Do not install DLLs from random sources.
Missing models/CUDA are NOT_TESTED. CPU fallback in a CUDA variant is FAILED,
never reported as GPU latency. First inference is a separate warmup, followed by
measured warm rows; load time is separate. Optional installed pynvml samples total
device-0 VRAM (including other processes), not exact model allocation. Without it
VRAM remains null; run `nvidia-smi --query-gpu=memory.used --format=csv -l 1`
in another terminal. No new dependency is required for CPU tests.

Finally run the normal assistant with performance logging and speak complete,
paused and short Ukrainian phrases. Inspect the session metrics above; offline
WAV benchmarks alone cannot validate microphone endpoint latency. The corpus
has no intent/entity annotations, so those accuracies remain null. No GPU, Pi,
live microphone or dual-residency performance is certified by automated tests.

### Actual CPU measurements and completion status, 04.10.2026

Same 24 WAV, Windows x86_64 / 3791MiB RAM, CTranslate2 4.8.1,
faster-whisper 1.2.1; no CUDA. First inference excluded from warm statistics.
Some early measurements overlapped automated tests / short replay: exploratory
latencies, not an idle-machine model selection experiment.

| Candidate | WER / CER % | Warm mean / median / p95 ms | RTF | Load / first inference ms | Peak process RSS MiB |
|---|---|---|---|---|---|
| tiny CPU int8 | 66.49 / 22.43 | 1347 / 1395 / 1518 | 0.301 | 1769 / 1709 | 268.3 |
| Vosk baseline | 27.66 / 7.83 | 1973 / 1948 / 2812 | 0.441 | 5551 / 1962 | 770.2 |
| base CPU int8 | 64.89 / 19.98 | 2466 / 2306 / 3078 | 0.551 | 2530 / 2618 | 335.1 |
| small CPU int8 | 24.47 / 8.97 | 7659 / 7427 / 8833 | 1.710 | 5482 / 7989 | 727.4 |

Medium, turbo, large-v3 and ONNX: NOT_TESTED (local weights/runtime unavailable).
All three explicit GPU variants: NOT_TESTED (CUDA unavailable). VRAM not measured.
Intent/entity preservation is null: the existing corpus lacks annotations.
Small still misses interactive targets; base/tiny quality is unacceptable as a
default fix. No GPU winner selected, no default model change justified yet.

Acoustic replay with 100ms blocks: both short-threshold candidates (700/1200ms)
had 0/24 potential early cuts, mean estimated end-to-endpoint 1465ms. These corpus
phrases did not exercise the under-1s fast branch; synthetic boundary tests do.
This is not proof against clipping natural slow speech or environmental noise.

Targeted checks: 374/374; final full suite: 652/652; Ruff and diff check clean.
An initial full run was 651/652: an old logging-worker mock lacked last_metadata;
the fixture was updated and logging 18/18 plus the full suite rerun successfully.
ActionPolicy, TurnEnvelope, confirmation correlation, routing and TTS unchanged.
Code is ready for RTX/live diagnostics, NOT certified to meet latency targets.

## Capture budget and offline latency comparisons (2026-10-05)

Unrestricted PCM capture now gives speech its own duration budget: a 15-second
listen call waits at most 15 seconds for onset, then permits at most 15 seconds
from acoustic onset, bounded by 30 seconds total. Silence endpoint thresholds
are unchanged. Only initial silence is trimmed before ASR, keeping 300ms pre-roll;
timestamps remain relative to original capture. Overflow/truncation/cancellation
still invalidate the result as before. This does not modify Vosk confirmations.

Controlled offline comparison (local models only; no audio upload):

```powershell
python tester.py --probe stt_benchmark --allow-live --timeout 2400 -- --corpus <corpus-directory> --matrix testing/stt_latency_matrix.json --variant-timeout 600
```

Despite the existing `--allow-live` opt-in name, this probe is offline. The matrix
separates small baseline, temperature=0/best_of=1, and word_timestamps=false.
Production decoder defaults remain unchanged. Reports include individual
confidence values: word probabilities and segment log probabilities are not
interchangeable calibrated ACTION scores. Compare WER/CER and confidence before
promoting any decoding change. Entity metrics require confirmed corpus annotations.

`moonshine-uk` is benchmark-only, not a runtime profile/backend. It expects
`models/sherpa-onnx-moonshine-base-uk-quantized-2026-02-27/` with
`encoder_model.ort`, `decoder_model_merged.ort`, `tokens.txt`, and an optional
`sherpa-onnx` installation exposing `OfflineRecognizer.from_moonshine_v2`.
See the [official API](https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/python/sherpa_onnx/offline_recognizer.py)
and [model releases](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models).
Missing files/dependencies produce NOT_TESTED, not a fabricated result. There are
no automatic downloads; check model licensing before deployment. Moonshine
confidence is uncalibrated and marked unreliable; it cannot authorize actions.

New local 24-WAV run (2026-10-05, CPU/int8, beam1, two threads):

| Variant | Mean / p95 ms | WER / CER % | Mean confidence |
|---|---|---|---|
| small baseline | 8555 / 12572 | 24.47 / 8.97 | 0.7454 |
| small single-pass | 8427 / 13057 | 24.47 / 8.97 | 0.7454 |
| small no word timestamps | 6579 / 7345 | 24.47 / 8.97 | 0.6628 |

All 24 transcripts were identical across these variants. This is a single
sequential run, not a statistical guarantee or live microphone measurement.
No-timestamps is promising (~23% lower mean), but remains benchmark-only because
confidence changes. Moonshine NOT_TESTED: local files/runtime absent. Entity
annotations absent; no separate filename/intent correctness claim. First tester
attempt hit its outer 300s deadline after saving baseline; remaining variants
were rerun with 1800s. Functional suite: 690/690, no skips; Ruff passed.

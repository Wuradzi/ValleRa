# Ukrainian STT backends — Phase 3A.2

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

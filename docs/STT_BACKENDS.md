# Ukrainian STT backends — Phase 3A.2

Natural speech now uses `SpeechListener → capture_pcm → FasterWhisperBackend`
without constructing a Vosk decoder or waiting for Vosk text. Backend output is
the existing `RecognitionResult`; `TurnEnvelope` and dialogue/action policy are
unchanged. `STTBackend` accepts PCM16 mono and sample rate on Windows and Linux.

## Configuration

Under `stt` in config.json:

```json
{
  "backend": "faster-whisper",
  "quality_profile": "quality",
  "low_resource_model": "base",
  "primary_device": "auto",
  "primary_local_files_only": true,
  "fallback_backend": "none",
  "primary_timeout_ms": 60000
}
```

Profiles select `large-v3` (quality), `large-v3-turbo` (balanced), or
`low_resource_model` (low_resource). These differ from the existing overall
`performance.profile`. The explicit raspberry_pi performance profile selects
Vosk. Desktop defaults select faster-whisper, including with older config files.
Legacy `stt.whisper.model/device/compute_type` configure old refinement/probes,
not the new primary profile. Other Whisper tuning, including CPU threads, beam,
confidence, `hotwords` and `initial_prompt`, remains under `stt.whisper` (the
initial prompt key is `prompt`). Hints are optional and may bias transcription.

Primary language is always `uk`, task `transcribe`. `auto` selects CUDA when
CTranslate2 detects it, otherwise CPU int8. CUDA load/inference failure retries
the **same** model on CPU and logs why; it does not substitute a smaller model.
No GPU was available for a live CUDA test in this iteration. See the
[upstream installation requirements](https://github.com/SYSTRAN/faster-whisper).

Models are not downloaded by default during conversation. Explicit installation:

```powershell
python tools/download_whisper_model.py
```

This command downloads/prepares the selected primary profile; large models need
substantial disk space and RAM. On CPU, large-v3 is not a latency guarantee.
Until a selected model is installed, natural voice recognition is unavailable
unless an explicit fallback is configured; text input and Vosk confirmation
remain separate. Never silently label an unavailable model as ready.

## Capture and safety

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
micro-averaged WER/CER, mean decode latency, RTF, load time and sampled process
peak RSS (50 ms sampling, not exact allocator peak). CER includes normalized
spaces; normalization is NFKC/casefold with punctuation removed. Missing models
are `unavailable`, not benchmark successes. VRAM is explicitly not measured.
Do not publish personal transcripts. Functional unit tests use fake decoders
and are not evidence of recognition quality.

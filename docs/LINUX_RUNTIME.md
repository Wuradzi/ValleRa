# Phase 3C — Linux / ARM64 runtime

Implemented, automated-tested with mocks on Windows. Linux/WSL/Pi live runtime
NOT_TESTED. This is a hardware-validation candidate, not a validated Pi voice stack.
No model selection/latency/quality claims for Pi. Windows remains first-class.

## Validation record — 2026-10-05

- Targeted `tester.py --all --filter LinuxRuntimeTests --verbose`: 14/14 PASS.
- Full `tester.py --all --verbose`: 720/720 PASS (Windows project `.venv`).
- `tester.py --release-check`: 9/9 required stages PASS; optional live/GPU/benchmark NOT_RUN.
- Windows `main.py --doctor`: 12 PASS / 8 WARN / 0 FAIL, Volodymyr uk-UA,
  71682-byte WAV, 4665.7ms synthesis. Playback/microphone signal NOT_TESTED.
- Ruff and git diff --check PASS. Mocked Linux/ARM + fixture Pi identity only;
  fresh-process text-Core construction with unavailable sounddevice passed.
- WSL executable exists, but no WSL distro/runtime validation was performed.
- No commit/push. No live API, audio capture, model downloads or OS modifications.

LINUX / ARM64 RUNTIME: READY FOR HARDWARE VALIDATION

WINDOWS BACKEND: REFERENCE GREEN

RASPBERRY PI HARDWARE: NOT_TESTED

Changed components in this phase (not the entire preceding dirty worktree):
`services/platform/{linux,resolver,__init__}.py`,
`services/apps/{controller,indexer}.py`, `services/files/file_service.py`,
`services/health/{checks,probes,runtime}.py`, `services/diagnostics.py`,
`core/{speak,command_router}.py`, `config.py`, `install.py`,
`skills/apps/{skill.py,manifest.json}`, `skills/system/manifest.json`,
`requirements.txt`, `requirements-desktop.txt`,
`tests/{test_linux_runtime,test_platform_boundary}.py`, `.gitignore`, README,
ARCHITECTURE and this guide; local development log/roadmap/evidence updated.

## Initial blocker audit

| Class | Finding / decision |
|---|---|
| BLOCKS_LINUX_STARTUP | Windows desktop Python dependencies were unconditionally installed by desktop extras: add markers. Core base keeps Vosk/numpy; wheels must still be verified on actual ARM64/Python version. |
| BLOCKS_FEATURE | Platform stub rejected every Linux action. Implement configured launches and desktop openers. |
| BLOCKS_FEATURE | Windows index discovery/aliases: Linux reads only manual entries, never scans Start Menu/registry or reuses automatic Windows inventory. |
| BLOCKS_FEATURE | Speaker tried unsupported synthesis per response: preserve console/UI output, skip unsupported audio queue. |
| BLOCKS_FEATURE | Desktop session/opener absent: deterministic not_available/not_found, no crash. |
| WINDOWS_ONLY_BY_DESIGN | Window/process management, workplace, drive inventory, Windows Speech transport. Linux reports NOT_IMPLEMENTED. |
| WINDOWS_ONLY_BY_DESIGN | Shutdown/cancel uses Windows delayed 60-second semantics. Linux power/session actions remain unsupported; no guessed shutdown equivalent, sudo/password/polkit changes or reboot intent. Confirmation remains before execution. |
| ALREADY_PORTABLE | DialogueState, TurnEnvelope, ActionPolicy, confirmations, storage, LLM/web APIs, pathlib project paths, temporary files, psutil instance lock, sounddevice PCM boundary. |
| BLOCKS_FEATURE | Windows user-directory config on Linux: ignore drive/UNC paths for FileService authorization, never create fake directories; preserve config on disk. Fresh Linux defaults use only existing standard home subdirectories. |
| DEFER_TO_PHASE_3D | Final edge STT/TTS, ARM package/wheel validation, real microphones/permissions and audio quality/latency. |

## Capability contract

- Apps: explicit manual `command` parsed with POSIX shlex into argv, executable
  resolved through PATH, `shell=False`, no package-name guessing or installation.
  Quotes protect a path containing spaces. Shell operators are literal arguments.
  Only configure trusted programs; argv is not a sandbox for those programs.
  OS spawn acceptance is **submitted**, never verified visible-window success.
  Immediate nonzero exit is execution_failed; later app failure remains unverified.
- Browser/files: `xdg-open` with argv and 8-second limit, requires DISPLAY or
  WAYLAND_DISPLAY. Environment is a hint, not proof of a working desktop;
  opener errors remain failures. Timeout does not prove the target did not open.
- Fixed errors: unsupported, not_found, permission_denied, not_available,
  execution_failed. No raw stderr/arguments in user-facing platform errors.
- No Linux window control, process closing, workplace, power/session control,
  automatic drive inventory or TTS yet. No mandatory wmctrl/xdotool/RHVoice.
- Pi detection: bounded read of device-tree `model` from
  `/sys/firmware/devicetree/base/model` or `/proc/device-tree/model`; only bool/null
  exported. ARM64 alone never labels Pi. No serial, environment or /proc dump.
- Doctor: platform, capability/environment states, Pi detection, CPU/RAM/available
  RAM, CUDA, existing model/profile checks and audio enumeration. Audio failure
  warns on Linux (text Core still works); missing active STT remains FAIL.
  Model files present does not mean successful inference. Diagnostics retain
  allow-list/redaction and include fixed Linux metadata; no transcript/raw audio.

## Debian-family / Raspberry Pi OS 64-bit first run

Prerequisites: ARM64 Linux for `raspberry_pi` installer profile, Python 3.10+
(Windows reference uses 3.12), git, venv, available package wheels/build support.
Do not copy Windows `.venv`, private data, API keys, or machine-specific config.
Use the checkout containing Phase 3C (not an older remote commit).

Explicit administrator provisioning, only if packages are missing:

```sh
sudo apt update
sudo apt install python3-venv libportaudio2
```

These commands are documentation, never run by installer. `sounddevice` is the
Python dependency; PortAudio is a system runtime on Linux. See the
[sounddevice installation documentation](https://python-sounddevice.readthedocs.io/en/0.5.3/installation.html).
Audio enumeration needs actual devices and OS access; PortAudio alone is not
proof of microphone readiness. No PulseAudio/ALSA/system configuration edits here.

Inside the repository:

```sh
python3 -m venv .venv
. .venv/bin/activate
python install.py --profile raspberry_pi
python install.py --profile raspberry_pi --check
python main.py --doctor
python main.py --text-only
```

Installer installs shared Python dependencies explicitly; no models or desktop
packages. `--check` only checks Python/core-module presence (no installation);
Doctor checks config/paths/models/audio. No missing `.env`/API key is fabricated.
First normal launch creates standard project config/data/cache/log directories;
it does not create user search directories. Configure API secrets locally using
existing setup/vault workflow, never put keys in a shell command or public log.
Text input works without STT weights/TTS; LLM needs configured provider/network.
Without a provider, local actions still work and dialogue reports unavailable.

Optional local UI: `python main.py --text-only --web-ui --no-browser`.
Keep loopback binding; remote UI exposure/authentication is not added in this phase.
No desktop required for Core; `open browser` on headless returns unavailable.

Application registration uses existing confirmed manual-app workflow, e.g.
`додай програму Editor команда /usr/bin/my-editor` (use your installed executable).
Aliases map to an exact configured app name; Windows Chrome/Telegram defaults
do not install or guess Linux packages. Configure Linux search directories
explicitly; localized/XDG custom locations are not automatically discovered.

## Hardware acceptance checklist

1. Save Python/OS/architecture, numeric resources, Pi-detection and dependency
   readiness from `--check`/`--doctor`; inspect warnings instead of hiding them.
2. Run text-only, local command, memory and confirmation confirm/cancel/timeout;
   test unavailable LLM and a configured provider separately (network costs apply).
3. Test an explicit installed app; distinguish submitted from verified. Test a
   missing executable and permission denial. Do not test destructive system actions.
4. Headless: browser/file open unavailable, Core continues. Desktop separately:
   existing allowed file and browser open, error/timeout not reported as success.
5. `python main.py --diagnostics`: inspect privacy before sharing ZIP.
6. `python tester.py --all --verbose`, `python tester.py --release-check` on Pi;
   record platform-specific fixture failures honestly, don't relabel Windows mocks.
7. Optional microphone only when ready: `python main.py --audio-test` records a
   short local probe. No cloud upload. Voice startup requires actual model files.

Phase 3D: explicitly install an edge candidate (`--with-voice` opts into current
experimental sherpa runtime), provision compatible model files per STT_BACKENDS,
benchmark recorded Ukrainian corpus on real Pi, choose TTS and measure first sound,
memory/CPU/latency and thermal throttling. Existing auto ARM edge policy unchanged;
no downloads at startup and no fake low-quality fallback PASS.

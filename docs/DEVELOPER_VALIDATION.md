# Перевірка ValleRa перед checkpoint

## Команди

Запускайте з кореня проєкту, у встановленому virtualenv.

| Команда | Що перевіряє / обмеження |
|---|---|
| `python tester.py --release-check` | Config, imports, critical contracts, confirmation, contextual actions, STT, full suite, Ruff, `git diff --check`. Без мікрофона, API і CUDA. |
| `python main.py --doctor` | Конфігурація, Python/OS/RAM, профіль STT, кеш моделей, пакети/CUDA, список аудіопристроїв, Windows TTS WAV self-test, наявність LLM configuration. |
| `python main.py --doctor --probe-audio` | Додатково відкриває мікрофон і записує 3 секунди лише в пам'ять. |
| `python main.py --audio-test` | 3 секунди локального PCM: RMS, peak, приблизний noise floor, clipping, speech activity та endpoint estimate. Говоріть після повідомлення. |
| `python main.py --models` | Primary/escalation/confirmation моделі, кеш, профіль і очікуваний device. Не завантажує моделі. |
| `python main.py --smoke-live` | Інтерактивний мікрофон, natural STT, transcript команди, constrained «так»/«ні», TTS і локальний contextual proposal dry-run. |
| `python main.py --diagnostics` | Локальний знеособлений каталог і ZIP у `logs/diagnostics/`. Нічого не надсилає. |

Doctor не змінює config, не розблоковує vault і не викликає LLM. Перевірка
прав доступу до директорій — лише `os.access`, не доказ успішного запису.
Без `--probe-audio` він не перевіряє сигнал мікрофона. Наявність output device
не доводить чутність звуку. Кеш перевіряється за файлами, не повним inference.
CUDA libraries потребують окремої живої GPU-перевірки.

TTS self-test використовує коротку нейтральну фразу, обирає голос за поточним
правилом Windows Speech або явно повідомляє fallback, завершує synthesis,
перевіряє WAV header/frames і видаляє тимчасовий файл. Playback не виконується.
Поза Windows цей probe повертає `NOT_AVAILABLE`, а не удаваний PASS.
Audio-test не переписує noise threshold і не передає аудіо в мережу.

## Release-check і живі перевірки

Звіт і stage logs: `logs/release-check/<run>/report.json`.
`FAIL` дає ненульовий exit code; відсутність запитаних optional перевірок —
`NOT_RUN`, відсутнє обладнання/модель — `NOT_TESTED`, не PASS.

```powershell
python tester.py --release-check --with-benchmark --corpus recordings/corpus
python tester.py --release-check --with-gpu --corpus recordings/corpus
python tester.py --release-check --with-live
```

Benchmark використовує наявну offline STT matrix і локальний корпус; формат
описаний у [STT_BACKENDS.md](STT_BACKENDS.md). GPU та моделі мають бути
підготовлені окремо. За замовчуванням мережевих перевірок немає.

Live smoke очікує Enter перед записами. Нічого не виконує через tools.
Command transcript перевіряє наявність надійного тексту, не повне виконання
intent. Context followup перевіряє локальний proposal lease/acceptance,
не повний LLM contextual resolver. Capture+decode latency не прирівнюється
до speech-end latency: відповідний рядок `NOT_TESTED`. Звіти зберігаються
в `logs/smoke-live/`. Один smoke не доводить production readiness.

## Приватність діагностики

Allowlist: `summary.txt`, `system.json`, `config.sanitized.json`,
`dependencies.json`, `stt_status.json`, `tts_status.json`,
`performance_summary.json`, `recent_session_tail.log`, `manifest.json`.

Config редагується рекурсивно: секретні та невідомі ключі й значення маскуються;
значна частина несекретних tuning values також прихована. STT status залишає
лише відомі enum-значення. Session tail **реконструюється з числових PERF
даних**, а не копіюється з приватного діалогу. `.env`, vault, memory,
clipboard, аудіо та довільні файли не копіюються. Bundle не запускає TTS:
його статус там `NOT_TESTED`. Перед передаванням ZIP все одно перегляньте
його: версії пакетів та характеристики середовища є технічними ідентифікаторами.

## Runtime observability

Текстова `/status` показує профіль, last STT metadata, TTS configuration,
LLM candidate, pending confirmation/proposal (лише bool), fallback і last turn.
Не підтверджує дію і не показує приватну memory. Configured provider ≠ healthy.

За ввімкненого performance logging `[TURN]` показує endpoint,
recognition_ready, stt, llm_first_text, first_phrase, tts_submit, total,
model/device/escalation. Milestones — від endpoint; STT/LLM — окремі durations.
`total` закінчується на processor completion, **не** на завершенні озвучення.
`tts_submit` не означає перший фізичний звук; відсутнє значення — `n/a`.
На graceful shutdown summary містить voice turns, runtime та mean/p50/p95
доступних STT/endpoint samples (обмежене вікно останніх 512).

## Config і рекомендований цикл

`config_version: 2`. Старий файл без версії вважається v1; міграція відбувається
в пам'яті, не перезаписує файл і зберігає невідомі поля. Непідтримувана версія
відхиляється. STT defaults, confirmation і action safety не змінюються.

Development batch → release-check → doctor → optional live smoke → optional
STT benchmark → diagnostic bundle → checkpoint review.

Автоматичний PASS не означає здорову конкретну машину. Потрібні окремі
живі перевірки мікрофона, чутності TTS і GPU; повна Linux підтримка не заявляється.

## Windows reference checkpoint — Phase 3A.3.1

Doctor must run in the normal Windows user environment. On 2026-10-05 the
restricted execution sandbox caused `System.Speech.GetInstalledVoices()` to
throw `NullReferenceException`; the identical minimal call outside it listed
Zira and Volodymyr. This is not evidence of missing voices. Do not reinstall
voices/.NET based on that sandbox failure. Actual doctor outside sandbox:
11 PASS / 8 WARN / 0 FAIL; requested/resolved Volodymyr, uk-UA, valid 71682-byte
temporary WAV, exit code 0. Audible playback was NOT_TESTED.

Production WAV synthesis and doctor share `services/audio/windows_speech.py`
voice resolution and synchronous `Invoke-ValeraWave`. The doctor waits for
output reset, disposal and process completion before validation. Streaming
playback/pulse remains in Speaker; health does not invoke dialogue logic.
Failure data includes stage, requested/resolved voice, culture, bounded exception
message/type, exit code, file presence/size and elapsed time. Synthesis PASS is
independent of playback availability; successful preference fallback is WARN.

Diagnostics manifest version 1 lists the exact 9 ZIP members, exclusions and
`redaction_applied=true`, `raw_audio_included=false`,
`conversation_content_included=false`. No recursive directory copy. Config
strings/numbers and unknown keys are redacted; technical enums and bounded
numeric PERF samples use explicit allow-lists. Raw logs, .env, audio, clipboard,
notes, chat, memory, encrypted stores, models and caches are excluded.

Combined checkpoint, from project root with virtualenv activated:

```powershell
python tester.py --release-check
python main.py --doctor
python main.py --audio-test
python main.py --models
python main.py --smoke-live
python tester.py --probe stt_benchmark --allow-live --timeout 2400 -- --corpus logs/voice-corpus/20260915T164508Z-516cab33 --matrix testing/stt_matrix.json --candidates turbo-cuda-fp16 turbo-cuda-int8 large-cuda-fp16 --variant-timeout 600
python main.py --diagnostics
```

The RTX step requires an RTX machine, working CUDA and locally cached candidate
weights; absent prerequisites mean NOT_TESTED, not PASS. Replace the private
corpus path if that corpus is not present on the target machine. Smoke's
contextual proposal check is a local dry-run, not a live LLM interaction, and its
TTS check synthesizes silently. Audible playback and real conversational/LLM
behavior still require a separate interactive application session.

Windows is a first-class, evolving reference target, not a frozen branch.
Future CORE features should preserve shared dialogue, TurnEnvelope, ActionPolicy,
confirmation, memory and routing contracts. PLATFORM features may retain richer
Windows desktop/RTX implementations and distinct ARM64/Linux implementations.
Only the minimum required platform boundaries should be introduced next;
Raspberry Pi support is not implemented or certified by this checkpoint.

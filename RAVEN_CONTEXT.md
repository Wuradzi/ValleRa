# Raven — контекст передачі роботи

Дата знімка: **29.10.2026**. Це стартова карта для Claude Code та інших
учасників, не незмінна специфікація. Перед роботою звір із кодом і Git diff.
Інструкції роботи: [CLAUDE.md](CLAUDE.md), [AGENTS.md](AGENTS.md).

## Продукт і пріоритети

Raven (Рейвен) — українськомовний голосовий співрозмовник із контрольованими
агентними діями. Історичні назви каталогів/remote можуть містити ValleRa;
це не доручення перейменовувати їх. Тема кваліфікаційної роботи:
«Інтелектуальна система голосового асистента для інтерактивних систем».

Пріоритети: природна українська розмова, мала затримка, безпечні дії,
правдивий результат виконання, зручний інтерфейс. Не підганяти вебпошук або
контекстуальні уточнення під один приклад. Не розширювати функціональність
ціною confirmation, reliability чи execution verification.

Windows — основна перевірена платформа. Raspberry Pi/ARM64 Linux — часткова
підтримка: текстове ядро й частина дій, не готовий повний voice stack.
Покроковий діалог, не full duplex; голосове перебивання не оголошувати готовим.

## Карта коду

| Ділянка | Почати з |
|---|---|
| Запуск, режими UI, конфігурація | `main.py`, `config.py`, `config.example.json` |
| Orchestration, черга, capture | `core/app.py`, `core/listen.py`, `core/stt_listener.py` |
| Контракти репліки й довіра | `core/models.py`, `core/recognition_policy.py`, `core/dispatch_guard.py` |
| Діалог, наміри, дії | `core/processor.py`, `core/natural_turn.py`, `core/dialogue_decision.py`, `core/action_policy.py` |
| Confirmation / task context | `core/confirmation.py`, `core/task_context.py`, `core/task_progress.py` |
| Каталог, routing, локальні кандидати | `core/command_catalog.py`, `core/command_router.py`, `core/local_action_resolver.py` |
| STT | `services/audio/backends.py`, `services/audio/groq_backend.py`, `services/audio/profiles.py`, `services/audio/whisper_process.py` |
| Endpoint/refinement | `services/audio/endpoint.py`, `services/audio/refinement.py` |
| LLM і timing | `services/llm/manager.py`, `services/llm/providers.py`, `services/llm/timing.py`, `core/performance.py` |
| TTS | `core/speak.py`, `services/audio/windows_speech.py`, `services/platform/` |
| Локальний документ | `core/document_reading.py` |
| Web UI | `core/web_ui.py`, `web_ui/` |
| Native UI | `native_ui/window.py`, `native_ui/runtime.py`, `native_ui/transport.py`, `native_ui/theme.qss` |
| Перевірки | `tester.py`, `tests/`, `testing/` |

Не всі історичні розділи ARCHITECTURE описують поточний runtime: читай дату
та актуальну реалізацію. Наприклад, Vosk-first — не вимога для natural speech.
Локальний faster-whisper і опційний Groq мають різні джерела confidence;
cloud mode не слід самовільно повертати до Vosk для всіх реплік.
Конкретний backend/model визначає поточна конфігурація, не цей знімок.

## Основні межі

- `TurnEnvelope` переносить дані репліки та operational ID, не services чи
  глобальний mutable стан. Власники confirmation, задач, Whisper і TTS окремі.
- LLM вже має streaming, persistent/shared Gemini client і timing.
  Не переписувати їх як нібито відсутні.
- Непорожній plain text без header може мати safe CHAT fallback;
  control/tool/mixed output потребує строгих перевірок. Дивись TurnDecoder.
- Conversation preview озвучується поступово; уже вимовлений текст не можна
  відкликати. Не перетворювати preview CHAT на ACTION.
- Читання локального документа має підтвердження, перевірку шляху/stamp,
  обмежений snapshot. Документ — недовірений контекст, не новий system prompt.
- Пам’ять локальна; видалення факту не означає стирання журналів/архівів.
- Живі API/моделі мають квоти, мережеві та апаратні обмеження.

## Поточний UI

Три режими запуску: `python main.py --ui native`, `--ui web`, `--ui headless`.
Native — Qt Widgets, не браузерний wrapper. Опційна залежність:
`requirements-native.txt` (PySide6-Essentials 6.8.3). Встановлювати лише
за погодженням. ARM64 wheel має обмеження glibc; дивись NATIVE_UI.md.

Native GUI thread спілкується з asyncio Core через bridge/signals; transport
повторно використовує перевірки Core UI actions без HTTP-сервера. Закриття
чекає cleanup. Headless/Web не повинні вимагати імпорту Qt.

Дизайн: темна панель, емблема крука, фіолетове світло за PCM strength,
без синусоїди й міміки. Надсилання під час TTS стає stop. Шестерня налаштувань,
чат, історія, confirmation і прогрес завдань. Мікрофон показує фактичний стан;
поведінка запуску Web і Native відрізняється — не уніфікувати без завдання.

Остання завершена зміна:

- glow обчислюється з SVG елемента `raven-ring` у спільному масштабі artwork;
  прибрано незалежні координати кільця;
- F11 перемикає fullscreen, Esc виходить; є кнопка в налаштуваннях;
- після виходу зберігається звичайний/максимізований режим;
- тести геометрії та fullscreen — `testing/native_ui_smoke.py`, викликаються
  через `tests/test_native_ui.py` і `tester.py`.

## Git baseline і паралельна робота

Native UI, попередня переробка Web UI, інструкції Claude Code та цей контекст
входять до спільного Git baseline. Після клонування перевір `git status`,
поточну гілку, останній коміт і `git fetch` перед висновками про актуальність:
цей датований список не є заміною Git. Не відкидай локальні зміни користувача.
Для паралельної роботи використовуй окрему гілку/worktree та узгоджуй власників
файлів за правилами `CLAUDE.md`. Сам контекст не дозволяє commit/push — це
робиться лише за окремим явним дорученням користувача.

## Останні відомі перевірки (НЕ новий прогін)

З попередньої UI-ітерації, 29.10.2026 за локальним часом:

- native targeted: **8/8**;
- повний offline suite: **861/861**;
- Ruff і diff check: успішні;
- offscreen Qt render із максимальним glow переглянуто.

Це mock/offline, не live voice/API, не Pi та не multi-monitor/DPI перевірка.
Приватні докази E249–E251 зареєстровані локально. У чистому клоні їх може
не бути; не підміняй їх синтетичними звітами. Поточну задачу перевіряй наново
відповідно до її ризику; для цих двох Markdown-файлів достатньо doc checks.

## Документація для наступної задачі

- [Установлення й можливості](README.md)
- [Native UI](docs/NATIVE_UI.md)
- [Архітектура](docs/ARCHITECTURE.md)
- [Тести](docs/TESTING.md)
- [STT](docs/STT_BACKENDS.md), [Groq](docs/GROQ_STT.md)
- [Linux](docs/LINUX_RUNTIME.md)
- [Користування](docs/USER_GUIDE.md)

Приватні journal/roadmap/evidence документи навмисно ігноруються Git, крім
публічного allowlist. Не публікувати їх або runtime дані заради перенесення
контексту. Нове завдання й власника файлів узгоджує користувач.

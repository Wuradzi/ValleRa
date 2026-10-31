# Raven — контекст передачі роботи

Дата знімка: **30.10.2026**. Це стартова карта для Claude Code, Codex та інших
учасників, не незмінна специфікація. Перед роботою звір із кодом і Git diff.
Інструкції роботи: [CLAUDE.md](CLAUDE.md), [AGENTS.md](AGENTS.md).

## Передача роботи — стан на 30.10.2026 (після сесії Claude, для Codex та інших агентів)

Обов'язково перед роботою: [CLAUDE.md](CLAUDE.md) — розділи «Safety contracts», «Приватність
і дозволи», «Перевірки» діють для **будь-якого** агента, не лише Claude; [AGENTS.md](AGENTS.md) —
правила документації. Потім `git status --short`, `git log --oneline -5`, релевантний diff.

### Стан репозиторію

Оновлення Codex, J132 (30.10.2026), **без commit/push**: виконано пункти 1–2
нижче. `dialogue_context` вже передавав status/coverage/failure_reason; втрачалися
назви джерел та caveat без claims. Тепер вони збережені, а фіксовані правила
чесного пояснення вебстатусу додано до обох chat/converse шляхів. Дані джерел
лишаються user-priority. Це prompt/context fix, не гарантія відповіді живої LLM.
Повтори одного слова confirmation нормалізуються; mixed/unknown та confidence/
correlation checks збережені. Корпус: 73 фрази. Наступний невиконаний пункт — 3;
пункти 1–2 нижче залишено як опис вихідних спостережень. Нові перевірки: див. J132.

- Гілка `main`, три локальні коміти **без push**: `a52503d` (аудит №1–№4), `b315540`
  (№5–№7, залишок №3), `43444f6` (J131 контекст). GitHub Actions (Windows + `tests-linux`)
  ще жодного разу не запускався з цими змінами. Push — лише за дорученням користувача.
- Останній прогін (Windows, E253): `tester.py --all --verbose` 915: 914 успішних,
  0 невдалих, 1 пропуск (немає Node); ruff, храповик C901 (порушень 0), `git diff --check` — успішно.
- Наживо після J131 не перевірялося. Живий тест користувача до J131 — E252
  (`logs/sessions/session-20261030T074553.977129Z-11780-2f99a1dc.log`, приватний, сирий
  вміст не цитувати в Git/звітах).

| Запис | Суть |
| --- | --- |
| J121 | ключ відновлення показується один раз; «Душогуб» `enabled=false`, підтвердження цілі, `allowed_targets` |
| J122–J125 | gate matrix (216 комбінацій), `core/turn_gate.py`, розділений `process`, храповик C901, «стоп» у pentest |
| J126 | переносні тести (`tests/platform_fixtures.py`), Linux CI job `tests-linux` |
| J127 | `docs/DEVELOPMENT_RULES.md` (публічний шаблон журналу); `validate_config`, `_conversation_turn`, `_natural_turn` ≤ 10 |
| J128 | «забудь»/очищення пам'яті видаляє `memory.json.bakN` |
| J129 | `core/log_retention.py`: ротація `logs/sessions` (30) і `logs/tests` (50), ≥7 днів, докази захищено |
| J130 | `testing/phrase_corpus.json` (зараз 70 фраз) + `tests/test_phrase_corpus.py` |
| J131 | `final_request` (розмовний вступ перед запитом), чистий `search_query`, «так» на пропозицію Raven (`intent_route=assistant_offer`) |

Деталі кожного запису — у локальному `docs/DEVELOPMENT_LOG.md` (не в Git; у чистому клоні
його немає — не вигадувати записи).

### Рекомендовані наступні задачі (користувач ще НЕ обрав — узгодити перед роботою)

Пріоритет за живим тестом E252; факти відділено від гіпотез.

1. **Вигадане пояснення після нерелевантного пошуку.** Факт (E252): пошук дав нерелевантні
   джерела й «Web summary has no supported claims»; на питання користувача, чому знайдено
   іншу тему, модель відповіла правдоподібною вигадкою. Гіпотеза: `dialogue_context()` у
   `services/web/answers.py` не дає моделі явного сигналу «джерела нерелевантні /
   coverage=insufficient» з назвами джерел. Перевірити, що саме потрапляє в `web_context`
   у `_natural_turn` (`core/processor.py`), і додати чесний статус без вигадування причин.
2. **«Так! Так!» у підтвердженні → «Не розібрав».** Факт (E252). Місце:
   `ConfirmationService._decision` (`core/confirmation.py:182`). Повтор того самого слова
   («так так», «ні ні») вважати цим словом; «так ні» лишається unknown; пороги голосової
   confidence не змінювати. Додати фразу в корпус (`confirm`).
3. **Мовний фільтр відкинув коротке «так» у підтвердженні.** Факт: `stt.language_guard
   decision=reject chars=5` (E252, 09:48:36), користувач повторив. Розпізнаний текст не
   логовано — причина невідома. Спершу дослідити `services/audio/groq_backend.py:132`
   (без логування сирого тексту в публічні звіти), не послаблювати фільтр без доказу.
4. **Groq confidence показується «0.00».** Факт: `confirmation_voice … confidence=0.000`
   і `[CONFIRM VOICE/groq 0.00]` (`core/app.py:438`, `:447`) при `confidence_available=False`.
   Показувати `n/a`; не змінювати логіку рішень (контракт: не вигадувати confidence).
5. **Кнопкові підтвердження native UI не журналюються.** Перше «так» і скасування в E252 без
   джерела (ймовірно кнопки — не доведено). Точки: `native_ui/window.py:447` (`confirm`),
   `native_ui/transport.py:75`, `native_ui/runtime.py:97`. Логувати лише outcome/source/
   request-префікс, без тексту діалогу. UI не виконує tools напряму.
6. **«Повтори, будь ласка, що таке SSD» проходить загальний `direct_request`** (J130,
   `core/natural_turn.py:273`, «повтори» заради `workplace_retry`); scope=null, ризик низький.
   Звужувати лише за рішенням користувача; змінити `note` у корпусі.
7. **Модель відповідає CHAT замість PROPOSAL** всупереч `turn_prompt`. J131 закриває лише
   випадок, коли на «так» модель дає ACTION. Можна додати лічильник/лог таких відповідей.
8. **`python -m testing.complexity_budget` падає в консолі cp1251** на «→»
   (`testing/complexity_budget.py:104`, `:110`). Обхід зараз — `PYTHONUTF8=1`.
9. **Залишок аудиту №3:** `CommandProcessor.process` 21, `_restricted_turn` 19.

Відкриті дії користувача (не робити за нього): push і перевірка CI (чи падають 6
whisper-тестів у Windows CI — faster-whisper не в `requirements-ci.txt`); перенести ключ і
видалити `RECOVERY_KEY.txt`; перевірити `pentest.enabled` у локальному `config.json`
(агент його не відкривав).

### Практичні пастки (перевірено в цій сесії)

- **Ротація журналів:** будь-який запуск `tester.py` видаляє старі незареєстровані теки
  `logs/tests` (лишає 50 новіших, нічого молодшого 7 днів, докази за назвою захищено).
  Перший live-старт `main.py` видалить ~20 старих незареєстрованих `session-*.log`.
  Новий доказ одразу реєструвати (ID + bytes/SHA-256 у `docs/EVIDENCE_REGISTER.md` і
  `docs/evidence/manifest.json`, новіші записи зверху).
- **Храповик C901:** нова функція > 10 або зростання існуючої — порушення в tester.
  Вкладені функції рахуються в батьківську. `--update` базової лінії — лише після спрощення.
- **Консоль Windows cp1251:** для скриптів з українським виводом — `PYTHONUTF8=1`.
  `python -c` з кирилицею в аргументах псується; краще окремий `.py` файл.
- **Регулярні вирази через heredoc/shell:** `\b` легко перетворюється на backspace —
  писати regex через редактор файлів, потім перевіряти відсутність `\x08`.
- **`docs/DEVELOPMENT_LOG.md`:** до J115 CRLF, новіше LF; записи вставляти побайтово
  (binary або `newline=''`), новіші — зверху, не нормалізувати файл.
- **Тести:** лише через `tester.py`; новий файл тестів додати до групи в `GROUPS`
  (`tester.py`), інакше він піде в «other». Фільтр: `--all --filter test_x`.
- **Знімок `tests/test_processor_gate_matrix.py`** і очікування `testing/phrase_corpus.json`
  змінювати лише свідомо, з поясненням (docstring / `note`).

### Рішення, які не переглядати без користувача

- «Стоп» від Groq без confidence лишається без винятку стоп-фрази (J90); кнопка зупинки в UI працює.
- Пошуковий запит — слова користувача; модель не виправляє назви/написання (контракт
  `original_search_intent`/`preserves_search_intent`), J131 лише прибирає «інформацію про»
  і кінцеву пунктуацію.
- `assistant_offer` — вузький виняток: лише так/ні-пропозиція того самого типу дії, лише
  web_search/find_files/open_app/weather, точні аргументи показує підтвердження виконавця.
- `docs/README.md` лишається локальним навігатором; публічні правила — `DEVELOPMENT_RULES.md`.
- Базову лінію храповика оновлювати лише після спрощення; числа — ruff/mccabe, не radon.

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

# Архітектура ValleRa

## Phase 3A.2: Ukrainian STT backends (03.10.2026)

Natural speech: `SpeechListener → acoustic PCM capture → STTBackend →
RecognitionResult → TurnEnvelope`. Default faster-whisper large-v3/uk;
Vosk remains constrained confirmation and explicit low-resource fallback.
Backend model/device metadata stays inside audio/diagnostics. Policies and
typed turn contracts do not depend on the decoder implementation.
See [configuration, safety and offline benchmark](STT_BACKENDS.md).

## Phase 3A.1: contextual action resolution (02.10.2026)

`DialogueState.proposals` володіє одним `PendingActionProposal` лише в пам'яті
сесії. Він містить proposal ID, origin turn ID, session ID, tool, immutable
arguments, reason, created/expires timestamp. TTL — 120 секунд і тільки наступний
operational turn. Це НЕ ConfirmationService/request і не дозвіл виконати дію.

Модель може повернути `PROPOSAL` з JSON чинного intent schema. Processor перевіряє
tool/arguments/enabled skills, реєструє пропозицію та сам формує питання з точних
параметрів. Саме його бачить/чує користувач. Prose з історії не стає permission;
невизначена тема потребує CLARIFY. PROPOSAL/FOLLOWUP JSON не надходить у TTS/history.

Для наступної репліки manager передає локальний active proposal окремо від
історії. `FOLLOWUP` повертає proposal_id і accept/reject/modify/ambiguous;
для modify — повні arguments того самого tool. CHAT та новий ACTION ідуть
звичайними маршрутами і не залишають попередню пропозицію активною.

- Accept потребує model decision, чинної correlated lease та консервативної
  перевірки поточної згоди/дієслова щодо запропонованого tool. Окреме «так» не
  запускає нічого. Негативні/питальні/змішані репліки не стають acceptance.
- Candidate відновлюється з незмінних збережених arguments, а не з нового JSON.
  `origin=contextual_followup` відрізняє його від explicit; operational ID поточної
  репліки не замінюється origin turn ID або proposal ID.
- ActionPolicy перевіряє відповідність proposal/candidate; state consume — один
  раз до await executor. Потім звичайні validation/confirmation/verification.
- Modify нічого не виконує: валідовані нові параметри показуються як нова
  пропозиція з іншим ID; потрібні наступне прийняття й звичайна safety approval.
- TTL, session mismatch, новий turn, reject, pause/stop, активне safety confirmation
  та generation invalidation роблять старі відповіді непридатними. Модель, яка
  запізнилася після cancellation, не може зареєструвати пропозицію або виконати її.
- Safety replies спочатку обробляє app/ConfirmationService. TaskContext selections
  та pending natural clarification мають пріоритет перед assistant proposal.
  Коли починається safety prompt, proposal state очищується; correlation не змінена.

Пропонуються лише web_search/open_app/find_files/weather/window_control/
prepare_workplace із чинного каталогу. Довільні shell/delete tools не додаються.
Мовні guards навмисно консервативні: прийняття поза підтриманими формами або
неоднозначне «другий» без selection context веде до уточнення. Це не повне
розуміння довільних посилань на багатотурову історію.

19 нових offline tests: accept/reject/modify, TTL/session/new topic, normal policy,
deny, scoped/safety priority, duplicate envelope, unreliable STT, late cancellation,
immutable state, parser/control JSON. Targeted 409/409, full 599/599; Ruff/diff чисті.
Суміжна корекція: DecisionKind тепер приймає наявний LLM `unavailable`, щоб
відхилений proposal не породжував enum error. Fallback/provider policy не змінена.
Новий протокол ще потребує live smoke з реальною моделлю; Phase 3B не розпочато.

## Phase 3A: core boundaries (30.09.2026)

Це extraction чинних правил, не нова policy. Потік:

`listen → RecognitionResult → app → TurnEnvelope/dispatch guard → processor
→ DialogueDecision → ActionPermission → execute_intent → SkillResult.execution`.

- `RecognitionPolicy` володіє колишніми refinement/engine-selection правилами
  listener і compatibility mapping `fragmented → action_eligible`. Audio зберігає
  capture, endpoint/Fragment Guard, energy evidence, Whisper wait і timing.
  Прямих `direct_request`, `voice_request`, callback до processor у listener немає.
  Listener ще звертається до policy під час refinement: це навмисна compatibility
  межа, не повністю двофазний STT. Пороги, timeout та порядок short-circuit ті самі.
- `DialogueState` володіє pending natural clarification та read-only scoped hint;
  app з'єднує його з recognition policy. Hint перевіряє той самий каталог/expiry,
  не споживає pending і не дозволяє execution. Confirmation та TaskContext окремі.
- `DialogueDecision` позначає CHAT/CLARIFY/ACTION_CANDIDATE/LOCAL_COMMAND/CONTROL.
  NaturalTurn від LLM адаптується без зміни prompt/decoder, operational ID копіюється
  з CommandContext. `ActionPermission` зберігає цей ID і ALLOW/CLARIFY/SAFE_NO_ACTION.
- `turn_permission` зберігає pre-routing eligibility gate і stop exception.
  `natural_action_permission` зберігає scope/direct-request/literal-target/search
  checks модельної пропозиції. ALLOW лише допускає до executor, не замінює
  validation, confirmation, verification. Exact local routes залишені як були.
- `ExecutionResult` — read-only typed view `SkillResult.data`, не новий результат
  tools. REJECTED/CANCELLED/SUBMITTED/FAILED/VERIFIED/SUBMITTED_UNVERIFIED/UNKNOWN
  розрізняються. accepted, success, verified незалежні; відсутнє evidence = None.
  Executor логуює технічний статус/turn ID без action arguments чи transcript.

Processor усе ще координує routing, cancellation LLM task, streaming response,
контекст уточнення, локальні shortcut branches і підготовку відповіді. Не винесено
tools, confirmation, platform реалізації, TTS, memory або весь dialogue state machine.
Нові contracts не містять Windows API чи platform-specific типів.

Сумісність: RecognitionResult flags, TurnEnvelope.fragmented, process(str),
SkillResult/data, listener `_select_result`/`_should_refine`/prefix probe та processor
`stt_scoped_reply`/`_natural_pending` залишені для існуючих probes/tests. Останній
callback не використовується audio. Нових operational IDs не генерується.

Відомі старі обмеження (не виправлялися): processor metrics досі використовують
`success` з fallback на `accepted`/True; це не незалежний доказ виконання.
`recognition_unreliable` — provenance, а не самостійний новий authorization gate:
runtime зберігає compatibility eligibility через fragmented і окрему conflict
перевірку app. Довільно сконструйований суперечливий envelope не є новим policy
контрактом. Повну заміну legacy flags слід погоджувати окремо.

Перевірка: targeted 325/325, full 580/580 через tester.py; Ruff/diff чисті.
Додано 5 boundary tests і розширено confirmation/execution assertions. Наявні
tests покривають scoped replies, deny/cancel, dedup, stale epoch, direct routes.
Це offline mocks, не живий голос чи запуск програм. Live smoke Phase 2/3A ще потрібен.
Phase 3B: можна окремо вводити platform adapters, не змінюючи ці contracts;
кросплатформна реалізація ще не заявляється завершеною.

## Typed turn transport

`core.models.TurnEnvelope` — frozen/slots dataclass, immutable snapshot одного
dispatch. `ValleRaApp._enqueue_command()` створює його з `RecognitionResult`
або текстового введення; `command_queue` приймає тільки envelope, не positional
tuples. `CommandProcessor.process()` отримує той самий operational `turn_id`,
а для локального маршруту передає його в `CommandContext.turn_id`.

ID видає існуючий `DispatchGuard`: runtime session nonce + sequence. Перед
processor ticket споживається один раз. `session_id` походить із того самого guard;
ID існуючого `TurnTiming` залишається окремим діагностичним ID. Додавання timing
через immutable replacement не змінює operational ID.

Envelope містить source, text (поточний routing input), transcript (до repair),
STT engine/confidence, timing reference та незалежні прапорці:

- `utterance_incomplete`: незавершеність репліки за наявною endpoint оцінкою;
- `capture_truncated`: capture завершився за лімітом;
- `recognition_unreliable`: зафіксована ненадійність refinement/conflict;
- `action_eligible`: чинний STT дозвіл увійти до подальших safety checks, не
  підтвердження й не дозвіл виконати tool;
- `clarification_required`: збережений вибір між повтором фрази й tool-free chat.

Низький confidence, direct_request, confirmation та verification перевіряються
як раніше. Confirmation відповіді не проходять command queue: correlation ID
залишається в ConfirmationService/app. Microphone epoch перевіряється перед
поверненням capture, а TaskContext/Whisper jobs/Speaker generation лишаються
у своїх власників; envelope не дублює ці стани.

Тимчасова сумісність: `RecognitionResult.fragmented/incomplete` адаптуються
до явних полів, `TurnEnvelope.fragmented` є read-only view `not action_eligible`;
`process(str, ...)` залишається для прямих tests/probes. Production app передає
лише envelope. Внутрішні PCM/IPC tuples не є command transport і не змінені.

## Гарантії виконання

- Оригінальний текст зберігається окремо від нормалізованого intent: регістр
  шляхів, нотаток і секретів не втрачається.
- Голосова й текстова черги мають backpressure. Відомі команди виконуються
  локально. З дозволеним `commands.llm_interpretation` нерозпізнана явна
  команда може отримати один окремий JSON-розбір через LLM: без історії,
  без виконання коду, з локальною валідацією та підтвердженням трактування.
  Помилка вже запущеної навички не спричиняє повтору через модель.
- Локальний `TaskContext` зберігає до п'яти знайдених файлів або програм
  на 120 секунд. Відповідь номером дозволяє відкрити лише обраний результат;
  інша репліка, скасування, завершення вибору або нова команда закривають контекст.
- Розбіжність Vosk і Whisper щодо слова «Команда» не може синтезувати дозвіл на
  локальну дію: голосовий цикл просить повторити репліку, не викликаючи LLM
  чи локальну навичку. Недоступний Whisper має окремий fallback на Vosk.
- Кожна навичка ізольована тайм-аутом та обробкою помилок; повільні файлові й
  аудіооперації виконуються поза event loop.
- API-ключі копіюються лише у клієнти провайдерів, після чого вилучаються з
  оточення; дочірні процеси отримують очищене середовище.
- Пентест-режим обмежений підтвердженим scope, перевіряє DNS-адресу перед скануванням,
  фіксує IP для мережевого з'єднання та веде `data/pentest/audit.jsonl`.

```text
 безперервний мікрофон / клавіатура
                ↓
     захоплення одного аудіобуфера
          ↓                 ↓
  Vosk: межі репліки   Whisper: точний текст + VAD
          └────────┬────────┘
            вибір результату
                  ↓
      intent boundary
       ↓        ↓        ↓
 «Команда»  звичайна фраза  pentest mode
       ↓        ↓        ↓
 CommandRouter LLM  PentestService
       ↓        ↓        ↓
 локальна дія відповідь  звіт
                ↓
             Speaker
```

## Каталоги

- `core/` — життєвий цикл, STT/TTS, черги, підтвердження, маршрутизація;
- `services/` — аудіорушії, LLM, сховища, файли, програми, Windows, веб;
- `skills/` — незалежні модулі команд;
- `data/` — локальні дані;
- `cache/` — TTS-кеш;
- `logs/` — журнал;
- `models/` — локальні моделі;
- `tools/` — завантаження моделей і відновлення.
- `tester.py` — єдина точка запуску тестів, живих перевірок і замірів.
- `testing/probes/` — внутрішні реалізації додаткових режимів тестера без CLI.
- `tests/` — регресійні сценарії, які запускає комплексний тестер.

## Конкурентність

- `asyncio` керує циклом;
- Vosk працює поза event loop; важке Whisper-декодування — в одному
  повторно використовуваному дочірньому процесі з локальним PCM-каналом;
- команди потрапляють в `asyncio.Queue`;
- окремий канал приймає лише відповіді підтвердження;
- backpressure не дозволяє накопичувати команди під час попередньої операції;
- Vosk визначає кінець репліки й дає швидку первинну транскрипцію;
- той самий PCM-буфер без повторного відкриття мікрофона уточнює Whisper;
- Silero VAD відсіює тишу, а українська мова, prompt і hotwords стабілізують
  доменні терміни;
- якщо Whisper недоступний або невпевнений, результат Vosk зберігається;
- закрита граматика підтверджень обробляється лише Vosk без затримки Whisper;
- після завершення відповіді автоматично слухається наступна репліка;
- під час TTS мікрофон чекає, щоб відповідь асистента не потрапила назад у STT;
- Gemini має native async streaming; буфер обмежує фрази до 240 символів;
- системні правила Gemini передаються через `system_instruction`, історія —
  як `user` / `model`; резюме й пам'ять передаються як дані користувача;
- після першого фрагмента помилка потоку завершує часткову відповідь без retry;
- порожня відповідь, обмеження API, квота й транспортні помилки мають окремі
  категорії; коди `finish_reason` / `prompt_feedback.block_reason` перевіряються
  до передавання заблокованого фрагмента в TTS;
- збій LLM не прибирає налаштованих кандидатів: наступна репліка може відновити
  зв'язок без перезапуску; завершена порожня/обмежена відповідь не повторюється
  і не перенаправляється іншому провайдеру;
- лише підтверджена локальна команда «нова розмова» архівує стару історію й
  починає нову; помилка API сама по собі ніколи не змінює історію;
- HTTP 429 створює окремий для провайдера дедлайн паузи на монотонному годиннику;
  `Retry-After` / `RetryInfo` враховуються без блокувального очікування й
  автоматичного повторного надсилання реплік; за відсутності вказівки — 60 с;
- поріг впевненості звичайних реплік застосовується перед процесором команд,
  тому шум не витрачає запити до LLM; текстове введення не фільтрується як STT;
- TTS має окрему обмежену чергу; Windows SAPI — постійний дочірній процес,
  стандартний вихід озвучується напряму, вибраний пристрій — через WAV фраз;
- при виході черга TTS очищається, процеси Whisper/SAPI завершуються,
  задачі скасовуються й очікуються; Whisper inference не лишається
  невідмінюваним завданням default executor;
- Whisper-worker ігнорує SIGINT, завершенням при Ctrl+C керує головний процес;
- нагадування перевіряються окремою задачею;
- код LLM у другому етапі запускається окремим процесом.

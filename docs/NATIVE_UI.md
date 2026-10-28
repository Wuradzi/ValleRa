# Нативний інтерфейс Raven (Qt Widgets)

Стан на 28.10.2026: реалізовано опційний Python/PySide6 UI. Перевірено
підставний Core та offscreen Qt на Windows/Python 3.12.7. Живий голос і
запуск на Raspberry Pi в цій ітерації **не перевірялися**.

## Встановлення та запуск

У вже налаштованому середовищі Raven:

```powershell
python -m pip install -r requirements-native.txt
python main.py --ui native
```

Якщо конфігурацію ще не створено, спочатку виконайте звичайний
`python main.py --setup` у терміналі. Native UI не дублює майстер встановлення.
Майстер-пароль наявного сховища вводиться в нативному діалозі, не в чаті.
Відсутність Qt не заважає іншим режимам:

```powershell
python main.py --ui native --text-only
python main.py --ui web
python main.py --web-ui
python main.py --ui headless
python main.py --text-only
```

`--ui native` не запускає браузер, HTTP-сервер, WebEngine або HTML renderer.
Qt встановлюється окремо; headless/Web не імпортують PySide6.
Відкриття джерела користувачем через кнопку посилання запускає типовий браузер
як окрему програму — це не спосіб рендерингу Native UI.

## Функції й керування

- F11 — повний екран; Esc — вихід. Перемикач також є в налаштуваннях.
  Після виходу відновлюється звичайний або розгорнутий режим вікна.
- Підсвічування кільця використовує геометрію SVG емблеми, без окремого зміщення.
- Темна панель, фіолетові акценти, наявний `web_ui/avatar.svg`.
- Стани розпізнавання/обробки/мовлення/помилки та реальний стан мікрофона.
- Текстовий чат, поступове дописування фраз, позначення голосових реплік.
- «Виправити текст» копіює репліку в чернетку, не запускає дію.
- Кнопка надсилання й Enter під час TTS стають «стоп»; чернетка зберігається.
  Shift+Enter — новий рядок. Стоп не відкочує вже виконані локальні дії.
- Історія (до 200 елементів у вікні), нова розмова через існуюче підтвердження,
  безпечні HTTP(S) посилання на джерела, перехід до останнього повідомлення.
- Панель підтвердження з конкретним request ID. Чернетка прив'язується на
  початку введення, а не до довільного нового confirmation при надсиланні.
- Прогрес/кроки та скасування активного завдання з його kind/ID.
- Шестерня: модель/стан STT, пауза, local/Groq після перезапуску,
  окрема згода на хмарний STT, світло та беззвучна демонстрація.
- Закриття головного вікна після питання завершує Core й мікрофон;
  на відміну від закриття вкладки Web UI. Вікно чекає завершення cleanup,
  не вбиває потік і не залишає daemon-Core працювати непомітно.

Параметри теми й світла зберігаються окремо від Web UI у QSettings.
На відміну від CSS браузера, автоматичне читання системного
`prefers-reduced-motion` не перенесено: є явний перемикач світла.
Немає постійної декоративної анімації; світло керується існуючою PCM strength
і гасне при застарілих даних. Linux TTS цим UI **не додається**.

## Межі потоків та безпеки

```text
Qt main thread: Widgets / QSS / cached SVG pixmaps
    → Signal / loop.call_soon_threadsafe
Core QThread: asyncio → NativeTransport → existing UI action API
    → _submit_text / web_control → existing routing + confirmation + policy
    ← publish events + web_state snapshots
Qt main thread: queued Signal → text/state widgets
```

`NativeTransport` повторно використовує validation/submission у LocalWebUI,
але не запускає його сервер. В об'єкт RavenApp додається лише опційне
сповіщення UI; STT/TTS/LLM, consent, eligibility та execution verification
не переписані. Qt-потік не викликає блокувальні Core/мережеві операції.
Тільки worker володіє Core; між потоками передаються копії snapshots.

Оновлення групуються до 10/с. У спокої немає нового періодичного poller.
Існуючий capture loop сповіщає про стан захоплення. Для активних задач,
confirmation та TTS є обмежений refresh 250 мс: виконавці ще не мають
повного push-контракту progress. Цей refresh припиняється після завершення.
Шість рівнів сяйва закешовані; немає blur/GL/великих текстур, перерисовка
емблеми тільки при зміні рівня. Історія та черга подій обмежені.

## Raspberry Pi / Linux ARM64

Перевірений пакет — **PySide6-Essentials 6.8.3**: Python >=3.9,<3.14,
Windows x64; ARM64 wheel має тег **manylinux_2_39_aarch64**.
Це вимагає glibc >=2.39. Перевірте власну систему:

```sh
python3 --version
uname -m
getconf GNU_LIBC_VERSION
```

Система має бути 64-бітною (`aarch64`) із графічною сесією X11/Wayland та
потрібними системними бібліотеками Qt. Raspberry Pi OS Bookworm з glibc 2.36
не підходить для цього pip-wheel. Не оновлюйте glibc вручну поверх ОС.
Варіанти: сумісна новіша 64-bit ОС або PySide6/Qt пакети саме вашого
дистрибутива, якщо вони доступні й містять Widgets/Svg. Альтернатива для
старої системи та дуже малої RAM — окремий нативний Tkinter UI; він
**не реалізований** і не запускається як прихований fallback.

Після сумісного встановлення, у локальній графічній сесії:

```sh
python main.py --ui native --profile raspberry_pi --text-only
```

Профіль `raspberry_pi` не змінено. 905 МБ RAM — обмежений бюджет для всього
асистента; показники GUI без моделей не доводять, що локальний Whisper
поміститься або працюватиме швидко. Офіційна ARM reference-машина Qt значно
потужніша (Pi 5 / 8 GB). Апаратні Pi заміри та повний голосовий smoke потрібні.

Джерела перевірки сумісності (28.10.2026):
[PyPI package/wheels](https://pypi.org/project/PySide6-Essentials/6.8.3/#files),
[Qt supported platforms](https://doc.qt.io/qtforpython-6/overviews/qtdoc-supported-platforms.html).
Поточна сторінка Qt описує новіший реліз; мінімальна glibc для вибраного
пакета перевірена за тегом його wheel, а не припущена зі сторінки latest.

## Перевірки

```powershell
python tester.py --all --filter test_native_ui --verbose
python tester.py --all --verbose
python -m ruff check .
git diff --check
```

Qt-тест використовує окремий offscreen процес і mock Core. Без optional Qt
лише GUI-тест пропускається, transport/headless тести залишаються доступними.
Прогін з Qt зберігає приватний fixture screenshot і JSON ресурсів у
`logs/native-ui/`: це не живий голос, не запуск production моделей і не
Pi benchmark. CPU/VRAM не вимірюються. Витрати RAM у цьому JSON — лише
RSS тестового процесу; час створення UI не є часом повного старту Raven.

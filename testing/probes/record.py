"""User-operated local recording wizard. No ASR, TTS, network or commands.

Only the Record button opens the microphone; each clip is bounded to 20 s.
The user confirms the reference before saving. Original WAVs are not replaced.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import struct
import uuid
import wave

from core.atomic_json import AtomicJSONFile


PHRASES = (
    ("greeting", "Привіт, Валера. Як у тебе справи?", "Звичайний темп."),
    ("conversation", "Я сьогодні трохи втомився. Давай поговоримо про музику.", "Звичайний темп."),
    ("context", "Ти пам’ятаєш, про що ми говорили перед цим?", "Звичайний темп."),
    ("correction", "Це не те. Я мав на увазі інший університет.", "Як у звичайній розмові."),
    ("browser", "Команда, відкрий браузер.", "Команда лише записується, не виконується."),
    ("telegram", "Команда, відкрий Telegram.", "Як зазвичай вимовляєш назву."),
    ("weather", "Команда, знайди погоду в місті Луцьк на завтра.", "Звичайний темп."),
    ("university", "Команда, знайди в інтернеті Луцький національний технічний університет.", "Звичайний темп."),
    ("acronym", "Команда, знайди в інтернеті ЛНТУ.", "Як зазвичай вимовляєш абревіатуру."),
    ("department", "Команда, знайди кафедру кібербезпеки Львівської політехніки.", "Звичайний темп."),
    ("pen", "Поясни, як працює кулькова ручка і звідки надходить чорнило.", "Звичайний темп."),
    ("history", "Що відбувалося з містом Вавилон у Середньовіччі?", "Звичайний темп."),
    ("katana", "Команда, знайди в інтернеті японський меч катана.", "Звичайний темп."),
    ("hardware", "Чим оперативна пам’ять відрізняється від накопичувача SSD?", "Як зазвичай вимовляєш абревіатуру."),
    ("numbers", "На годиннику сімнадцята сорок п’ять, а зустріч почнеться о вісімнадцятій.", "Звичайний темп."),
    ("negation", "Не відкривай браузер. Я лише запитую, чи ти вмієш це робити.", "Не виділяй слова штучно."),
    ("pause_short", "Команда, знайди в інтернеті історію міста Чернівці.", "Зроби паузу приблизно пів секунди після «інтернеті»."),
    ("pause_long", "Я хотів запитати про подорожі Україною на вихідних.", "Зроби паузу приблизно півтори секунди після «запитати»."),
    ("two_sentences", "Спочатку поясни загальний принцип. Потім наведи зрозумілий приклад.", "Природна пауза між реченнями."),
    ("long", "Знайди інформацію про те, як підготуватися до подорожі в гори, якщо я раніше не ходив у тривалі походи.", "Говори природно, не поспішай."),
    ("quiet", "Валера, розкажи щось цікаве про зоряне небо.", "Трохи тихіше звичайного, але не пошепки."),
    ("natural_fast", "Команда, знайди в інтернеті розклад роботи бібліотеки.", "Трохи швидше звичайного, без навмисного ковтання слів."),
    ("followup", "А тепер поясни це простішими словами і без складних термінів.", "Звичайний темп."),
    ("finish", "Дякую, на сьогодні досить. Продовжимо нашу розмову пізніше.", "Звичайний темп."),
)


class CaptureBuffer:
    """Bounded PCM storage; audio callbacks do not write files or run models."""
    def __init__(self, rate):
        if type(rate) is not int or not 8000 <= rate <= 192000:
            raise ValueError("Invalid sample rate")
        self.rate = rate
        self.limit = rate * 2 * 20
        self.pcm = bytearray()
        self.overflow = False

    def feed(self, raw, overflow=False):
        self.overflow |= bool(overflow)
        available = self.limit - len(self.pcm)
        self.pcm.extend(raw[:available - available % 2])
        return len(self.pcm) >= self.limit

    def stats(self):
        values = [value[0] for value in struct.iter_unpack('<h', self.pcm)]
        return {"seconds": len(values) / self.rate,
                "peak": max((abs(v) for v in values), default=0),
                "clipped_samples": sum(abs(v) >= 32760 for v in values),
                "input_overflow": self.overflow,
                "time_limit_reached": len(self.pcm) >= self.limit}


def save_clip(directory, capture, phrase, device, *, reference_confirmed):
    if not reference_confirmed:
        raise ValueError("Reference must be confirmed by the speaker")
    stats = capture.stats()
    if stats['seconds'] < .5 or stats['peak'] <= 1:
        raise ValueError("Recording is too short or contains no signal")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    name = phrase[0] + '-' + uuid.uuid4().hex[:8] + '.wav'
    path = directory / name
    with path.open('xb') as output:
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(capture.rate)
            wav.writeframes(capture.pcm)
    entry = {"id": phrase[0], "reference": phrase[1], "instruction": phrase[2],
             "reference_confirmed": True, "file": name, "sample_rate": capture.rate,
             "device": device, "recorded_utc": datetime.now(timezone.utc).isoformat(),
             "wav_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), **stats}
    index = AtomicJSONFile(directory / 'corpus.json', {
        "schema_version": 1, "synthetic": False, "classification": "private_voice",
        "transmitted": False, "samples": [],
    })
    data = index.load()
    data['samples'].append(entry)
    index.save(data)
    return entry


def main(args):
    import tkinter as tk
    from tkinter import ttk, messagebox
    import sounddevice as sd

    root = tk.Tk()
    root.title('ValleRa — запис тестових фраз (локально)')
    root.geometry('820x610')
    root.minsize(760, 570)
    directory = Path(__file__).resolve().parents[2] / 'logs' / 'voice-corpus' / (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    state = {'stream': None, 'capture': None, 'index': 0, 'saved': set(), 'device': None, 'dirty': False}
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='24 фрази • без інтернету • жодні команди не виконуються', font=('Segoe UI', 12, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='Закрий ValleRa та інші програми, які записують мікрофон.\n'
              'Мікрофон вмикається лише кнопкою «Записати», максимум на 20 секунд.').pack(anchor='w', pady=10)
    devices = [(i, d) for i, d in enumerate(sd.query_devices()) if d['max_input_channels'] > 0]
    names = [f"{i}: {d['name']} ({sd.query_hostapis(d['hostapi'])['name']})" for i, d in devices]
    choice = ttk.Combobox(frame, values=names, state='readonly', width=90)
    choice.pack(fill='x')
    if devices:
        default = sd.default.device[0]
        choice.current(next((j for j, (i, _) in enumerate(devices) if i == default), 0))
    progress = tk.StringVar()
    phrase_text, hint, status = tk.StringVar(), tk.StringVar(), tk.StringVar(value='Мікрофон вимкнено. Обери пристрій та прочитай фразу.')
    ttk.Label(frame, textvariable=progress).pack(anchor='w', pady=(18, 8))
    ttk.Label(frame, textvariable=phrase_text, wraplength=750, font=('Segoe UI', 16)).pack(anchor='w', pady=8)
    ttk.Label(frame, textvariable=hint, wraplength=750).pack(anchor='w', pady=10)
    ttk.Label(frame, textvariable=status, wraplength=750, font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=10)
    confirmed = tk.BooleanVar(value=False)
    check = ttk.Checkbutton(frame, text='Я вимовив фразу вище повністю, без додаткових слів.', variable=confirmed)
    check.pack(anchor='w', pady=8)
    buttons = ttk.Frame(frame)
    buttons.pack(fill='x', pady=8)

    def refresh():
        phrase = PHRASES[state['index']]
        progress.set(f"Фраза {state['index'] + 1} / {len(PHRASES)}. Збережено різних фраз: {len(state['saved'])}.")
        phrase_text.set(phrase[1])
        hint.set(phrase[2] + ' Після кінця фрази зачекай 2 секунди й натисни «Зупинити».')

    def controls(recording):
        record_button.configure(state='disabled' if recording or not devices else 'normal')
        stop_button.configure(state='normal' if recording else 'disabled')
        save_button.configure(state='disabled' if recording or not state['dirty'] else 'normal')
        next_button.configure(state='disabled' if recording else 'normal')
        choice.configure(state='disabled' if recording else 'readonly')
        check.configure(state='disabled' if recording else 'normal')

    def stop():
        stream, state['stream'] = state['stream'], None
        if stream is not None:
            stream.stop()
            stream.close()
        if state['capture'] is not None:
            stats = state['capture'].stats()
            state['dirty'] = True
            warning = ' Перевантаження входу — краще повторити.' if stats['input_overflow'] else ''
            if stats['peak'] < 150:
                warning += ' Дуже тихий сигнал — перевір мікрофон.'
            if stats['clipped_samples']:
                warning += ' Є пікове спотворення — говори трохи далі від мікрофона.'
            status.set(f"Мікрофон вимкнено. Записано {stats['seconds']:.1f} с.{warning}")
        controls(False)

    def record():
        if state['dirty'] and not messagebox.askyesno('Повторити?', 'Відкинути незбережений запис і записати заново?'):
            return
        index, device = devices[choice.current()]
        confirmed.set(False)
        capture = CaptureBuffer(int(device['default_samplerate']))
        state.update(capture=capture, dirty=False, device={'index': index, 'name': device['name'],
                                                          'hostapi': sd.query_hostapis(device['hostapi'])['name']})
        def callback(indata, frames, timing, flags):
            if capture.feed(bytes(indata), flags.input_overflow):
                raise sd.CallbackStop
        try:
            state['stream'] = sd.RawInputStream(device=index, samplerate=capture.rate,
                channels=1, dtype='int16', blocksize=1024, callback=callback)
            state['stream'].start()
        except Exception as exc:
            if state['stream'] is not None:
                state['stream'].close()
                state['stream'] = None
            status.set(f'Мікрофон не запущено ({type(exc).__name__}). Обери інший пристрій.')
            return
        controls(True)
        status.set('● ЗАПИС — говори фразу зараз.')

    def advance():
        if state['dirty'] and not messagebox.askyesno('Пропустити?', 'Відкинути незбережений запис?'):
            return
        state.update(index=(state['index'] + 1) % len(PHRASES), capture=None, dirty=False)
        confirmed.set(False)
        status.set('Мікрофон вимкнено. Готовий до наступної фрази.')
        refresh()
        controls(False)

    def save():
        if not confirmed.get():
            messagebox.showinfo('Перевір текст', 'Підтвердь, що вимовив фразу повністю. Якщо помилився — повтори запис.')
            return
        try:
            entry = save_clip(directory, state['capture'], PHRASES[state['index']], state['device'], reference_confirmed=True)
        except Exception as exc:
            messagebox.showerror('Не збережено', f'Запис не прийнято ({type(exc).__name__}). Перевір сигнал і доступ до папки.')
            return
        state['saved'].add(entry['id'])
        state['dirty'] = False
        advance()
        if len(state['saved']) == len(PHRASES):
            status.set('Усі 24 фрази збережено. Можна закрити вікно.')
            messagebox.showinfo('Готово', f'Усі фрази збережено локально:\n{directory}\nПовідом у чаті, що запис завершено.')

    def tick():
        stream = state['stream']
        if stream is not None:
            if not stream.active:
                stop()
            else:
                seconds = len(state['capture'].pcm) / (2 * state['capture'].rate)
                status.set(f'● ЗАПИС — {seconds:.1f} / 20 с. Після фрази: 2 с тиші → «Зупинити».')
        root.after(150, tick)

    def close():
        if (state['stream'] is not None or state['dirty']) and not messagebox.askyesno(
                'Завершити?', 'Незбережений запис буде відкинуто. Збережені файли залишаться. Закрити?'):
            return
        stop()
        root.destroy()

    record_button = ttk.Button(buttons, text='● Записати / повторити', command=record)
    stop_button = ttk.Button(buttons, text='■ Зупинити', command=stop)
    save_button = ttk.Button(buttons, text='Зберегти → наступна', command=save)
    next_button = ttk.Button(buttons, text='Пропустити →', command=advance)
    for button in (record_button, stop_button, save_button, next_button):
        button.pack(side='left', padx=(0, 8))
    ttk.Label(frame, text=f'Приватні WAV та corpus.json:\n{directory}', wraplength=750).pack(anchor='w', pady=12)
    ttk.Label(frame, text='Збереження не перевіряє розпізнавання. Якщо помилився у словах — повтори фразу.', wraplength=750).pack(anchor='w')
    refresh()
    controls(False)
    root.protocol('WM_DELETE_WINDOW', close)
    root.after(150, tick)
    try:
        root.mainloop()
    finally:
        if state['stream'] is not None:
            state['stream'].stop()
            state['stream'].close()
    print(f'Voice corpus: {directory}; saved={len(state["saved"])}/{len(PHRASES)}', flush=True)
    return 0 if len(state['saved']) == len(PHRASES) else 1

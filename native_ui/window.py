"""Native Qt Widgets presentation. All command semantics remain in Raven Core."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, QSettings, QTimer, Signal, Slot, QRectF, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap, QShortcut, QTransform
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

ROOT = Path(__file__).resolve().parents[1]
STATES = {
    'offline': 'Немає з’єднання', 'ready': 'Я поруч.', 'listening': 'Слухаю тебе.',
    'processing_audio': 'Розпізнаю мовлення…', 'thinking': 'Думаю…',
    'speaking': 'Відповідаю', 'preparing_speech': 'Готую голос…',
    'paused': 'Не поспішаємо.', 'confirmation': 'Потрібне твоє рішення.',
    'voice_error': 'Не чую мікрофона.',
}
TASK_STATES = {'planning': 'Планую', 'awaiting_confirmation': 'Очікує рішення',
               'running': 'Виконую', 'verifying': 'Перевіряю', 'completed': 'Завершено',
               'partial': 'Частковий результат', 'failed': 'Помилка',
               'cancelled': 'Скасовано', 'cancelling': 'Зупиняю',
               'pending': 'Очікує', 'verified': 'Перевірено', 'unknown': 'Не перевірено',
               'skipped': 'Пропущено', 'saved': 'Збережено'}


def label(text='', name=''):
    item = QLabel(text)
    item.setTextFormat(Qt.TextFormat.PlainText)
    item.setWordWrap(True)
    item.setObjectName(name)
    item.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return item


def button(text, name, tooltip):
    item = QPushButton(text)
    item.setObjectName(name)
    item.setToolTip(tooltip)
    item.setAccessibleName(tooltip)
    return item


def icon(kind):
    paths = {
        'send': '<path d="m3 10 18-7-7 18-3-8-8-3Zm8 3L21 3"/>',
        'stop': '<rect x="6" y="6" width="12" height="12" rx="2" fill="#e5c7ff"/>',
        'mic': '<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/>',
        'muted': '<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8M3 3l18 18"/>',
        'history': '<path d="M4 10a8 8 0 1 1 1 7M4 5v5h5M12 7v5l3 2"/>',
        'settings': '<path d="m10 3-1 3-3 1-3 3 2 2-2 2 3 3 3 1 1 3h4l1-3 3-1 3-3-2-2 2-2-3-3-3-1-1-3Z"/><circle cx="12" cy="12" r="3"/>',
        'spark': '<path d="m9 5 2 6 6 2-6 2-2 6-2-6-6-2 6-2Z" fill="#ae79f4"/><path d="m18 1 1 3 3 1-3 1-1 3-1-3-3-1 3-1Z" fill="#ae79f4"/>',
    }
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
           'fill="none" stroke="#e5c7ff" stroke-width="1.6" stroke-linecap="round" '
           'stroke-linejoin="round">' + paths[kind] + '</svg>')
    renderer = QSvgRenderer(svg.encode())
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class Emblem(QWidget):
    """Six cached glow levels; redraw on new strength only, no animation loop."""
    def __init__(self):
        super().__init__()
        self.setFixedSize(190, 200)
        renderer = QSvgRenderer(str(ROOT / 'web_ui/avatar.svg'))
        viewbox = renderer.viewBoxF()
        scale = min(166 / viewbox.width(), 200 / viewbox.height())
        self.artwork_rect = QRectF(95 - viewbox.width()*scale/2, 0,
                                  viewbox.width()*scale, viewbox.height()*scale)
        transform = QTransform()
        transform.translate(self.artwork_rect.x(), self.artwork_rect.y())
        transform.scale(scale, scale)
        transform.translate(-viewbox.x(), -viewbox.y())
        self.glow_rect = transform.mapRect(renderer.boundsOnElement('raven-ring'))
        self.frames = []
        for level in range(6):
            pixmap = QPixmap(380, 400)
            pixmap.setDevicePixelRatio(2)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if level:
                for inset in range(12, 0, -2):
                    painter.setPen(QPen(QColor(160, 65, 235, level * (14 - inset)), 3))
                    painter.drawEllipse(self.glow_rect.adjusted(-inset/2, -inset/2, inset/2, inset/2))
            renderer.render(painter, self.artwork_rect)
            painter.end()
            self.frames.append(pixmap)
        self.level = 0
        self.expiry = QTimer(self)
        self.expiry.setSingleShot(True)
        self.expiry.timeout.connect(lambda: self.set_level(0))

    def set_level(self, level):
        level = max(0, min(5, int(level)))
        if level != self.level:
            self.level = level
            self.update()

    def pulse(self, state, enabled):
        data = state.get('tts_glow') or {}
        age = data.get('age_ms', 1000)
        strength = data.get('strength', 0)
        valid = (enabled and state.get('tts_playback') and data.get('source') == 'pcm'
                 and isinstance(age, (float, int)) and 0 <= age < 300
                 and type(strength) is int and 0 <= strength <= 5)
        self.expiry.stop()
        self.set_level(strength if valid else 0)
        if valid:
            self.expiry.start(max(1, int(300-age)))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.frames[self.level])


class Composer(QPlainTextEdit):
    submitted = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class RavenWindow(QMainWindow):
    action = Signal(object)
    shutdown_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Raven — простір розмови')
        self.setWindowIcon(QIcon(str(ROOT / 'web_ui/avatar.svg')))
        self.resize(900, 850)
        self.setMinimumSize(380, 580)
        self.state = {}
        self.ready = False
        self.closing = False
        self.core_done = False
        self.sending = False
        self.stopping = False
        self.assistant = None
        self.rows = []
        self.cursor = 0
        self.draft_bound = False
        self.draft_id = None
        self.expanded = False
        self.settings_store = QSettings('Raven', 'NativeUI')
        self.icons = {name: icon(name) for name in ('send', 'stop', 'mic', 'muted', 'history', 'settings')}
        host = QWidget()
        host.setObjectName('background')
        self.page = QScrollArea()
        self.page.setWidgetResizable(True)
        self.page.setFrameShape(QFrame.Shape.NoFrame)
        self.page.setWidget(host)
        self.setCentralWidget(self.page)
        outer = QVBoxLayout(host)
        outer.setContentsMargins(24, 72, 24, 24)
        self.card = QFrame()
        self.card.setObjectName('card')
        self.card.setMaximumWidth(990)
        outer.addWidget(self.card, 1, Qt.AlignmentFlag.AlignHCenter)
        self.card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(24, 138, 24, 24)
        layout.setSpacing(12)
        self.emblem = Emblem()
        self.emblem.setParent(host)
        settings = button('', 'settings', 'Стан сесії та налаштування')
        settings.setIcon(self.icons['settings'])
        settings.setFixedSize(38, 38)
        settings.clicked.connect(self.open_settings)
        # Overlay gear without displacing the emblem's center.
        settings.setParent(self.card)
        self.settings_button = settings
        self.status = label('З’єднуємося…', 'status')
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status)
        controls = QHBoxLayout()
        controls.addStretch()
        self.microphone = button('', 'round', 'Увімкнути мікрофон')
        self.microphone.setCheckable(True)
        self.microphone.setFixedSize(56, 56)
        self.microphone.clicked.connect(lambda: self.emit_action('microphone', enabled=not self.state.get('microphone', False)))
        controls.addWidget(self.microphone)
        controls.addSpacing(18)
        self.history = button('', 'round', 'Історія розмови')
        self.history.setIcon(self.icons['history'])
        self.history.setFixedSize(56, 56)
        self.history.setCheckable(True)
        self.history.clicked.connect(self.toggle_history)
        controls.addWidget(self.history)
        controls.addStretch()
        layout.addLayout(controls)
        self.mic_detail = label('Очікування запуску Core.', 'detail')
        self.mic_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.mic_detail)
        self.notice = label('', 'notice')
        self.notice.hide()
        layout.addWidget(self.notice)
        self.task_box = QFrame()
        task_layout = QVBoxLayout(self.task_box)
        self.task_text = QPlainTextEdit()
        self.task_text.setReadOnly(True)
        self.task_text.setFixedHeight(70)
        task_layout.addWidget(self.task_text)
        self.task_details = button('План і результати кроків', 'secondary', 'Показати кроки завдання')
        self.task_details.setCheckable(True)
        task_layout.addWidget(self.task_details)
        self.steps = QPlainTextEdit()
        self.steps.setReadOnly(True)
        self.steps.setMaximumHeight(100)
        self.steps.hide()
        self.task_details.toggled.connect(self.steps.setVisible)
        task_layout.addWidget(self.steps)
        self.cancel_task = button('Скасувати завдання', 'secondary', 'Скасувати активне завдання')
        self.cancel_task.clicked.connect(self.cancel_progress)
        task_layout.addWidget(self.cancel_task)
        self.task_box.hide()
        layout.addWidget(self.task_box)
        self.new_chat = button('Нова розмова +', 'secondary', 'Нова розмова з підтвердженням')
        self.new_chat.clicked.connect(lambda: self.emit_action('message', text='Команда: нова розмова', request_id=None))
        self.new_chat.hide()
        layout.addWidget(self.new_chat, alignment=Qt.AlignmentFlag.AlignRight)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(120)
        self.messages = QWidget()
        self.message_layout = QVBoxLayout(self.messages)
        self.message_layout.setContentsMargins(0, 12, 0, 12)
        self.message_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.empty = label('Тут з’явиться наша розмова.', 'detail')
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_layout.addWidget(self.empty)
        self.scroll.setWidget(self.messages)
        layout.addWidget(self.scroll, 1)
        self.jump = button('До останніх повідомлень ↓', 'secondary', 'Прокрутити до останнього повідомлення')
        self.jump.clicked.connect(self.jump_latest)
        self.jump.hide()
        layout.addWidget(self.jump)
        self.confirmation = QFrame()
        self.confirmation.setObjectName('confirmation')
        confirm_layout = QVBoxLayout(self.confirmation)
        self.confirmation_text = QPlainTextEdit()
        self.confirmation_text.setReadOnly(True)
        self.confirmation_text.setFixedHeight(85)
        confirm_layout.addWidget(self.confirmation_text)
        choices = QHBoxLayout()
        self.reject = button('Скасувати', 'secondary', 'Відхилити поточну дію')
        self.accept = button('Підтверджую', 'primary', 'Підтвердити поточну дію')
        self.reject.clicked.connect(lambda: self.confirm(False))
        self.accept.clicked.connect(lambda: self.confirm(True))
        choices.addWidget(self.reject)
        choices.addWidget(self.accept)
        confirm_layout.addLayout(choices)
        self.confirmation.hide()
        layout.addWidget(self.confirmation)
        composer_box = QFrame()
        composer_box.setObjectName('composer')
        composer_layout = QHBoxLayout(composer_box)
        composer_layout.setContentsMargins(14, 8, 8, 8)
        spark = QLabel()
        spark.setPixmap(icon('spark').pixmap(28, 28))
        composer_layout.addWidget(spark)
        self.editor = Composer()
        self.editor.setPlaceholderText('Напиши або продиктуй свою думку…')
        self.editor.setAccessibleName('Твоє повідомлення')
        self.editor.setFixedHeight(58)
        self.editor.textChanged.connect(self.draft_changed)
        self.editor.submitted.connect(self.submit)
        composer_layout.addWidget(self.editor, 1)
        self.send = button('', 'primary', 'Надіслати повідомлення')
        self.send.setFixedSize(50, 50)
        self.send.clicked.connect(self.submit)
        composer_layout.addWidget(self.send)
        layout.addWidget(composer_box)
        self.build_settings()
        self._restore_maximized = False
        self.fullscreen_shortcut = QShortcut('F11', self)
        self.fullscreen_shortcut.activated.connect(self.toggle_fullscreen)
        self.escape_shortcut = QShortcut('Esc', self)
        self.escape_shortcut.activated.connect(self.exit_fullscreen)
        self.setStyleSheet((Path(__file__).with_name('theme.qss')).read_text(encoding='utf-8'))
        self.update_controls()

    def toggle_fullscreen(self):
        self.dialog.close()
        if self.isFullScreen():
            self.exit_fullscreen()
        else:
            self._restore_maximized = self.isMaximized()
            self.showFullScreen()
            self.fullscreen_button.setText('Вийти з повного екрана · F11 / Esc')

    def exit_fullscreen(self):
        if self.isFullScreen():
            if self._restore_maximized:
                self.showMaximized()
            else:
                self.showNormal()
            self.fullscreen_button.setText('Повний екран · F11')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.position_chrome)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.position_chrome)

    def position_chrome(self):
        if hasattr(self, 'settings_button'):
            self.card.setFixedWidth(min(990, max(300, self.page.viewport().width()-48)))
            self.card.parentWidget().layout().activate()
            self.settings_button.move(self.card.width()-55, 18)
            self.settings_button.raise_()
            self.emblem.move(self.card.x()+(self.card.width()-190)//2, 8)
            self.emblem.raise_()
            available = max(240, self.scroll.viewport().width()-16)
            for kind, row in self.rows:
                row.setFixedWidth(int(available * (.85 if kind == 'user' else 1)))

    def build_settings(self):
        self.dialog = QDialog(self)
        self.dialog.setWindowTitle('Поточна сесія')
        self.dialog.resize(470, 550)
        body = QVBoxLayout(self.dialog)
        self.fullscreen_button = button('Повний екран · F11', 'secondary', 'Перемкнути повноекранний режим')
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        body.addWidget(self.fullscreen_button)
        self.provider = label('Модель: —')
        body.addWidget(self.provider)
        self.pause = button('Пауза розмови', 'secondary', 'Пауза/продовження розмови')
        self.pause.clicked.connect(lambda: self.emit_action('resume' if self.state.get('paused') else 'pause'))
        body.addWidget(self.pause)
        body.addWidget(label('Джерело STT після перезапуску'))
        self.backend = QComboBox()
        self.backend.addItem('Локальна модель · автоматичний профіль', 'local')
        self.backend.addItem('Groq · хмарна модель', 'groq')
        body.addWidget(self.backend)
        self.stt_status = label()
        body.addWidget(self.stt_status)
        body.addWidget(label('Groq передає аудіо до api.groq.com і витрачає API-квоту. Потрібен GROQ_API_KEY.'))
        self.consent = QCheckBox('Дозволяю передачу голосових реплік до Groq після перезапуску.')
        body.addWidget(self.consent)
        self.save_stt = button('Зберегти вибір STT', 'secondary', 'Зберегти STT для наступного запуску')
        self.save_stt.clicked.connect(lambda: self.emit_action('stt_backend', backend=self.backend.currentData(), cloud_consent=self.consent.isChecked()))
        self.backend.currentIndexChanged.connect(self.update_controls)
        self.consent.toggled.connect(self.update_controls)
        body.addWidget(self.save_stt)
        self.lights = QCheckBox('Пульсація емблеми під час мовлення')
        self.lights.setChecked(self.settings_store.value('lights', True, type=bool))
        self.lights.toggled.connect(self.light_preference)
        body.addWidget(self.lights)
        preview = button('Перевірити світло · 3 с', 'secondary', 'Зразок світла без голосу та API')
        preview.clicked.connect(self.preview)
        body.addWidget(preview)
        body.addWidget(label('Мікрофон обробляє Raven, не UI. Під час озвучення слухання призупинене. Стоп не скасовує вже запущені локальні дії. Закриття цього вікна завершує Raven. Не надсилайте секрети в чат.'))
        close = button('Закрити', 'secondary', 'Закрити налаштування')
        close.clicked.connect(self.dialog.close)
        body.addWidget(close)

    def light_preference(self, enabled):
        self.settings_store.setValue('lights', enabled)
        if not enabled:
            self.emblem.set_level(0)

    def preview(self):
        if self.lights.isChecked():
            self.dialog.close()
            self.emblem.set_level(5)
            self.emblem.expiry.start(3000)

    def open_settings(self):
        self.dialog.show()

    def stop_mode(self):
        return bool(self.state.get('tts_playback') or self.state.get('tts_busy') or self.stopping)

    def update_controls(self):
        ready = self.ready and not self.closing and not self.core_done
        self.editor.setEnabled(ready and not self.sending)
        stop = self.stop_mode()
        self.send.setIcon(self.icons['stop' if stop else 'send'])
        self.send.setToolTip('Зупинити відповідь та озвучення' if stop else 'Надіслати повідомлення')
        self.send.setAccessibleName(self.send.toolTip())
        self.send.setEnabled(ready and (not self.stopping if stop else not self.sending and bool(self.editor.toPlainText().strip())))
        self.microphone.setEnabled(ready and bool(self.state.get('microphone_available')))
        mic = bool(self.state.get('microphone'))
        self.microphone.setChecked(mic)
        self.microphone.setIcon(self.icons['mic' if mic else 'muted'])
        self.microphone.setToolTip('Вимкнути мікрофон' if mic else 'Увімкнути мікрофон')
        self.microphone.setAccessibleName(self.microphone.toolTip())
        self.new_chat.setEnabled(ready and not self.sending)
        pending = bool(self.state.get('confirmation'))
        self.accept.setEnabled(ready and pending)
        self.reject.setEnabled(ready and pending)
        self.pause.setEnabled(ready and not pending and self.state.get('mode') == 'chat')
        self.backend.setEnabled(ready)
        self.save_stt.setEnabled(ready and (self.backend.currentData() != 'groq' or self.consent.isChecked()))
        task = self.state.get('task') or {}
        self.cancel_task.setEnabled(ready and bool(task.get('active')) and not task.get('cancel_requested', False))

    def emit_action(self, name, **fields):
        if self.ready and not self.closing and not self.core_done:
            self.action.emit({'action': name, **fields})

    def draft_changed(self):
        if not self.draft_bound and self.editor.toPlainText():
            self.draft_bound = True
            self.draft_id = self.state.get('confirmation')
        if not self.editor.toPlainText():
            self.draft_bound = False
            self.draft_id = None
        self.update_controls()

    def submit(self):
        if not self.send.isEnabled():
            return
        if self.stop_mode():
            self.stopping = True
            self.emit_action('stop')
        else:
            text = self.editor.toPlainText().strip()
            if len(text) > 4000:
                self.show_error('Повідомлення має містити не більше 4000 символів.')
                return
            self.sending = True
            self.emit_action('message', text=text, request_id=self.draft_id)
        self.update_controls()

    def confirm(self, accept):
        request_id = self.state.get('confirmation')
        if request_id:
            self.emit_action('confirm', request_id=request_id, accept=accept)

    def cancel_progress(self):
        task = self.state.get('task') or {}
        self.emit_action('cancel_progress', task_id=task.get('id'), kind=task.get('kind'))

    @Slot(object)
    def action_result(self, result):
        if self.closing:
            return
        if result.get('action') == 'message':
            if self.sending and result.get('ok'):
                self.editor.clear()
            self.sending = False
        if result.get('action') == 'stop':
            self.stopping = False
        if result.get('error') or result.get('message'):
            self.show_error(result.get('error') or result['message'])
        self.update_controls()

    @Slot(str)
    def show_error(self, text):
        self.notice.setText(text)
        self.notice.setVisible(bool(text))

    def toggle_history(self, expanded):
        self.expanded = expanded
        self.new_chat.setVisible(expanded)
        self.trim_visible()

    def trim_visible(self):
        users = [i for i, (kind, _) in enumerate(self.rows) if kind == 'user']
        start = users[max(0, len(users)-3)] if users else max(0, len(self.rows)-6)
        for index, (_, row) in enumerate(self.rows):
            row.setVisible(self.expanded or index >= start)

    def jump_latest(self):
        self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())
        self.jump.hide()

    def edit_transcript(self, text):
        if not self.ready or self.sending or self.state.get('confirmation'):
            return
        if self.editor.toPlainText():
            self.show_error('У полі вже є чернетка. Надішліть або очистіть її.')
            return
        self.editor.setPlainText(text)
        self.draft_bound = True
        self.draft_id = None
        self.editor.setFocus()

    def append_event(self, event):
        kind, text = event.get('kind'), str(event.get('text', ''))[:12000]
        if kind in {'playback', 'response_end'}:
            if kind == 'response_end':
                self.assistant = None
            return
        if kind == 'new_conversation':
            for _, row in self.rows:
                self.message_layout.removeWidget(row)
                row.deleteLater()
            self.rows.clear()
            self.assistant = None
            self.empty.show()
            return
        self.empty.hide()
        bar = self.scroll.verticalScrollBar()
        follow = bar.maximum()-bar.value() < 90
        if kind == 'assistant' and self.assistant is not None:
            self.assistant.setText((self.assistant.text() + ' ' + text)[-32000:])
        else:
            row = QFrame()
            row.setObjectName('user' if kind == 'user' else 'bubble')
            content = QVBoxLayout(row)
            content.setContentsMargins(12, 10, 12, 10)
            if kind == 'source':
                href = str(event.get('href', ''))
                try:
                    url = urlsplit(href)
                    safe = url.scheme in {'https', 'http'} and url.hostname and not url.username and not url.password
                except ValueError:
                    safe = False
                if not safe:
                    row.deleteLater()
                    return
                link = button('↗ ' + text + '\n' + url.hostname, 'secondary', 'Відкрити джерело у типовому браузері')
                link.clicked.connect(lambda _=False, value=href: QDesktopServices.openUrl(QUrl(value)))
                content.addWidget(link)
            else:
                self.assistant = None
                if kind in {'user', 'assistant'}:
                    content.addWidget(label(('ТИ · розпізнано з голосу' if event.get('source') == 'voice' else 'ТИ') if kind == 'user' else 'РЕЙВЕН', 'detail'))
                message = label(text)
                content.addWidget(message)
                if kind == 'assistant':
                    self.assistant = message
                if kind == 'user' and event.get('source') == 'voice':
                    edit = button('Виправити текст', 'secondary', 'Скопіювати репліку без автоматичного надсилання')
                    edit.clicked.connect(lambda _=False, value=text: self.edit_transcript(value))
                    content.addWidget(edit)
            self.rows.append((kind, row))
            row.setFixedWidth(int(max(240, self.scroll.viewport().width()-16) * (.85 if kind == 'user' else 1)))
            self.message_layout.addWidget(row, 0, Qt.AlignmentFlag.AlignRight if kind == 'user' else Qt.AlignmentFlag.AlignLeft)
        while len(self.rows) > 200:
            _, old = self.rows.pop(0)
            self.message_layout.removeWidget(old)
            old.deleteLater()
        self.trim_visible()
        if follow:
            QTimer.singleShot(0, self.jump_latest)
        else:
            self.jump.show()

    @Slot(object)
    def receive(self, packet):
        if self.closing or self.core_done:
            return
        self.state = packet['state']
        self.ready = self.state.get('status') != 'offline'
        self.status.setText(STATES.get(self.state.get('status'), 'Я поруч.'))
        self.mic_detail.setText('Голосове введення недоступне' if not self.state.get('microphone_available') else
                               'Мікрофон дозволено · під час озвучення слухання призупинене' if self.state.get('microphone') else
                               'Мікрофон вимкнено · захоплення зупинене')
        self.confirmation.setVisible(bool(self.state.get('confirmation')))
        prompt = self.state.get('confirmation_prompt', '')
        if self.confirmation_text.toPlainText() != prompt:
            self.confirmation_text.setPlainText(prompt)
        self.provider.setText('Модель: ' + str(self.state.get('provider', '—')))
        self.pause.setText('Повернутися до розмови' if self.state.get('paused') else 'Пауза розмови')
        if not hasattr(self, '_stt_initialized') and self.state.get('stt_saved'):
            self.backend.setCurrentIndex(max(0, self.backend.findData(self.state['stt_saved'])))
            self._stt_initialized = True
        self.stt_status.setText(f"Зараз: {self.state.get('stt_active', '—')}. Збережено: {self.state.get('stt_saved', '—')}."
                               + (' Потрібен перезапуск.' if self.state.get('stt_restart_required') else ''))
        task = self.state.get('task') or {}
        self.task_box.setVisible(bool(task))
        task_text = (' · '.join(str(task.get(k, '')) for k in ('title', 'detail', 'cancellation'))
                     + '\n' + TASK_STATES.get(task.get('status'), str(task.get('status', ''))))
        if self.task_text.toPlainText() != task_text:
            self.task_text.setPlainText(task_text)
        steps = '\n'.join(f"{i}. {step.get('name', '')} — {TASK_STATES.get(step.get('status'), step.get('status', ''))}: {step.get('detail', '')}"
                          for i, step in enumerate(task.get('steps', ()), 1))
        if self.steps.toPlainText() != steps:
            self.steps.setPlainText(steps)
        self.emblem.pulse(self.state, self.lights.isChecked() and self.isVisible() and not self.isMinimized())
        for event in packet.get('events', ()):
            if event.get('id', 0) > self.cursor:
                self.cursor = event['id']
                self.append_event(event)
        self.update_controls()

    def closeEvent(self, event):
        if self.core_done:
            event.accept()
            return
        event.ignore()
        if not self.closing:
            answer = QMessageBox.question(self, 'Завершити Raven?',
                                          'Завершити асистента й вимкнути мікрофон? Уже виконані локальні дії не відкочуються.')
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.closing = True
            self.dialog.close()
            self.status.setText('Завершую роботу…')
            self.emblem.set_level(0)
            self.update_controls()
            self.shutdown_requested.emit()

    @Slot()
    def core_finished(self):
        self.core_done = True
        self.ready = False
        self.emblem.set_level(0)
        self.update_controls()
        if self.closing:
            self.close()
        else:
            self.status.setText('Raven завершено')

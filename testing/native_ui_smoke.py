"""Offscreen Qt fixture invoked only by tester.py tests; no live services."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

# Executed as a subprocess so QApplication always belongs to the main thread.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from native_ui.runtime import Bridge, CoreThread  # noqa: E402
from native_ui.window import RavenWindow  # noqa: E402


def run():
    started = time.perf_counter()
    qt = QApplication([])
    # The offscreen plugin on Windows does not enumerate system fonts.
    # Use the installed font for readable fixture rendering, not a bundled asset.
    if sys.platform == 'win32':
        font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/segoeui.ttf'
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
    qt.setQuitOnLastWindowClosed(False)
    window = RavenWindow()
    window.show()
    qt.processEvents()
    QTest.qWait(30)
    construct_ms = round((time.perf_counter()-started)*1000)
    # Halo and artwork share the SVG coordinate system, not hand-tuned offsets.
    ring = window.emblem.glow_rect
    assert abs(ring.center().x() - 95) < .01
    assert abs(ring.center().y() - 204 * 166 / 400) < .01
    assert abs(ring.width() - ring.height()) < .01
    assert 144 < ring.width() < 146
    window.activateWindow()
    QTest.qWait(30)
    QTest.keyClick(window, Qt.Key.Key_F11)
    qt.processEvents()
    assert window.isFullScreen()
    QTest.keyClick(window, Qt.Key.Key_Escape)
    qt.processEvents()
    assert not window.isFullScreen() and not window.isMaximized()
    window.showMaximized()
    qt.processEvents()
    window.fullscreen_button.click()
    assert window.isFullScreen()
    window.exit_fullscreen()
    assert window.isMaximized()
    window.showNormal()
    qt.processEvents()
    actions = []
    window.action.connect(actions.append)
    state = dict(status='ready', microphone=True, microphone_available=True, mode='chat',
                 provider='mock', stt_active='local', stt_saved='local')
    window.receive({'state': state, 'events': []})
    assert window.microphone.isEnabled() and window.microphone.isChecked()
    window.editor.setPlainText('Вітаю')
    window.submit()
    assert actions[-1] == {'action': 'message', 'text': 'Вітаю', 'request_id': None}
    window.action_result({'action': 'message', 'ok': True})
    assert not window.editor.toPlainText()
    events = [dict(id=1, kind='user', text='Вітаю', source='voice'),
              dict(id=2, kind='assistant', text='Я поруч.'),
              dict(id=3, kind='assistant', text='Слухаю.'),
              dict(id=4, kind='source', text='Джерело', href='javascript:alert(1)')]
    window.receive({'state': state, 'events': events})
    assert len(window.rows) == 2 and window.assistant.text() == 'Я поруч. Слухаю.'
    window.receive({'state': state, 'events': events})
    assert len(window.rows) == 2  # replay is idempotent
    window.editor.setPlainText('чернетка до підтвердження')
    window.receive({'state': {**state, 'confirmation': 'B', 'confirmation_prompt': 'Відкрити браузер?'}, 'events': []})
    window.submit()
    assert actions[-1]['request_id'] is None  # stale draft never binds to B
    window.action_result({'action': 'message', 'ok': True})
    window.editor.setPlainText('так')
    window.receive({'state': {**state, 'confirmation': 'C'}, 'events': []})
    window.submit()
    assert actions[-1]['request_id'] == 'B'
    window.action_result({'action': 'message', 'ok': True})
    window.confirm(False)
    assert actions[-1] == {'action': 'confirm', 'request_id': 'C', 'accept': False}
    window.receive({'state': {**state, 'tts_busy': True, 'status': 'speaking'}, 'events': []})
    window.editor.setPlainText('зберегти чернетку')
    window.submit()
    assert actions[-1]['action'] == 'stop'
    assert window.editor.toPlainText() == 'зберегти чернетку'
    window.action_result({'action': 'stop', 'ok': True})
    window.receive({'state': state, 'events': []})
    window.microphone.click()
    assert actions[-1] == {'action': 'microphone', 'enabled': False}
    task = {'id': 'job1', 'kind': 'file_search', 'title': 'Пошук', 'active': True,
            'status': 'running', 'detail': 'Перевіряю каталоги', 'steps': []}
    window.receive({'state': {**state, 'task': task}, 'events': []})
    window.cancel_task.click()
    assert actions[-1] == {'action': 'cancel_progress', 'task_id': 'job1', 'kind': 'file_search'}
    window.backend.setCurrentIndex(1)
    window.consent.setChecked(False)
    assert not window.save_stt.isEnabled()
    window.consent.setChecked(True)
    window.save_stt.click()
    assert actions[-1] == {'action': 'stt_backend', 'backend': 'groq', 'cloud_consent': True}
    window.receive({'state': {**state, 'status': 'offline'}, 'events': []})
    assert not window.microphone.isEnabled() and not window.send.isEnabled()
    window.show_error('<b>Plain text only</b>')
    assert window.notice.text() == '<b>Plain text only</b>'
    for phase in ('listening', 'thinking', 'processing_audio', 'speaking', 'voice_error'):
        window.receive({'state': {**state, 'status': phase}, 'events': []})
        assert window.status.text()
    window.receive({'state': state, 'events': []})
    window.editor.clear()
    window.notice.hide()
    window.history.setChecked(True)
    window.toggle_history(True)
    qt.processEvents()
    assert abs((window.emblem.x()+window.emblem.width()/2)
               - (window.card.x()+window.card.width()/2)) < 2
    output = ROOT / 'logs' / 'native-ui'
    output.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%dT%H%M%S', time.gmtime()) + f'-{os.getpid()}'
    image_path = output / f'{stamp}.png'
    window.emblem.set_level(5)
    qt.processEvents()
    assert window.grab().save(str(image_path))
    window.emblem.set_level(0)
    # The worker waits asynchronously while the GUI continues to service timers.
    bridge = Bridge()
    ticks = []
    cleaned = []
    results = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(1))
    bridge.result.connect(results.append)

    async def factory(bridge):
        class App:
            running = True
            listener = None
            _tasks = [True]
            confirmation = SimpleNamespace(request_id=None, submit=lambda *a: False)

            def web_state(self):
                return state

            async def web_control(self, action, data):
                await asyncio.sleep(.25)
                return 200, {'ok': True}
        await bridge.attach(App())
        try:
            await asyncio.Event().wait()
        finally:
            await bridge.transport.close()
            cleaned.append(True)
        return 0

    worker = CoreThread(bridge, factory)
    worker.start()
    timer.start()
    deadline = time.monotonic()+3
    while bridge.loop is None and time.monotonic() < deadline:
        QTest.qWait(10)
    assert bridge.loop is not None
    bridge.submit({'action': 'stop'})
    deadline = time.monotonic()+3
    while not results and time.monotonic() < deadline:
        QTest.qWait(10)
    assert results and results[-1]['ok'] and len(ticks) >= 5
    bridge.shutdown()
    deadline = time.monotonic()+3
    while worker.isRunning() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert not worker.isRunning() and cleaned
    worker.wait()
    timer.stop()
    from unittest.mock import patch
    from PySide6.QtWidgets import QMessageBox
    stopped = []
    window.shutdown_requested.connect(lambda: stopped.append(True))
    with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
        window.close()
    assert window.closing and stopped and window.isVisible()
    count = len(window.rows)
    window.receive({'state': state, 'events': [{'id': 999, 'kind': 'assistant', 'text': 'late'}]})
    assert len(window.rows) == count
    window.core_finished()
    window.close()
    qt.processEvents()
    import psutil
    measurement = {'fixture': 'offscreen_mock_core_not_live', 'platform': sys.platform,
                   'ui_construct_ms': construct_ms,
                   'ui_construct_and_checks_ms': round((time.perf_counter()-started)*1000),
                   'process_rss_bytes': psutil.Process().memory_info().rss,
                   'gui_ticks_while_core_waited': len(ticks),
                   'real_pi': 'NOT_TESTED', 'cpu_benchmark': 'NOT_MEASURED'}
    (output / f'{stamp}.json').write_text(json.dumps(measurement, indent=2), encoding='utf-8')
    print(json.dumps(measurement))


if __name__ == '__main__':
    run()

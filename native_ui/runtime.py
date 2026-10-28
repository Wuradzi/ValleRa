"""Qt owns the main thread; one worker owns asyncio and all Core objects."""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit

from native_ui.transport import NativeTransport


class Bridge(QObject):
    snapshot = Signal(object)
    result = Signal(object)
    failure = Signal(str)
    password_needed = Signal(object)

    def __init__(self):
        super().__init__()
        self.loop = None
        self.transport = None
        self.app = None
        self.root = None
        self.closing = threading.Event()
        self.password_future = None
        self.jobs = set()

    def password(self, prompt):
        if self.closing.is_set():
            return ''
        future = concurrent.futures.Future()
        self.password_future = future
        self.password_needed.emit((prompt, future))
        try:
            value = future.result()
            from core.logging_setup import register_secret
            register_secret(value)
            return value
        finally:
            self.password_future = None

    async def attach(self, app):
        self.loop = asyncio.get_running_loop()
        self.root = asyncio.current_task()
        self.app = app
        self.transport = NativeTransport(app, self.snapshot.emit)
        app.web_ui = self.transport
        await self.transport.start()
        if self.closing.is_set():
            self.loop.call_soon(self._cancel)

    @Slot(object)
    def submit(self, data):
        if self.closing.is_set() or self.loop is None or self.loop.is_closed() or self.transport is None:
            self.result.emit({'ok': False, 'action': data.get('action'), 'error': 'Core ще не готовий.'})
            return
        # GUI only schedules work; it never calls Core methods synchronously.
        def schedule():
            if self.closing.is_set():
                return
            task = self.loop.create_task(self._dispatch(dict(data)))
            self.jobs.add(task)
            task.add_done_callback(self.jobs.discard)
        try:
            self.loop.call_soon_threadsafe(schedule)
        except RuntimeError:
            self.result.emit({'ok': False, 'action': data.get('action'), 'error': 'Сесію завершено.'})

    async def _dispatch(self, data):
        try:
            status, result = await self.transport.dispatch(data)
            self.result.emit({**result, 'ok': status < 400, 'action': data.get('action')})
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).exception('Native action failed')
            self.result.emit({'ok': False, 'action': data.get('action'),
                              'error': 'Не вдалося передати дію. Перевірте журнал.'})

    @Slot()
    def shutdown(self):
        self.closing.set()
        future = self.password_future
        if future is not None and not future.done():
            future.set_result('')
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._cancel)

    def _cancel(self):
        for job in tuple(self.jobs):
            job.cancel()
        if self.app is not None:
            self.app.running = False
            self.app.confirmation.submit('відміна', self.app.confirmation.request_id)
            if self.app.listener is not None:
                self.app.listener.interrupt()
            if not getattr(self.app, '_tasks', ()) and self.root and not self.root.done():
                # app.run installs cleanup after speaker startup. Do not cut
                # initialization before that cleanup boundary exists.
                self.loop.call_later(.1, self._cancel)
                return
        if self.root and not self.root.done():
            self.root.cancel()


class CoreThread(QThread):
    def __init__(self, bridge, factory):
        super().__init__()
        self.bridge = bridge
        self.factory = factory
        self.exit_code = 0

    def run(self):
        try:
            self.exit_code = asyncio.run(self.factory(self.bridge))
            if self.exit_code:
                self.bridge.failure.emit('Raven не запущено. Перевірте конфігурацію та журнал сесії.')
        except asyncio.CancelledError:
            pass
        except Exception:
            logging.getLogger(__name__).exception('Native core stopped')
            self.exit_code = 1
            self.bridge.failure.emit('Core зупинено через помилку. Подробиці — у журналі сесії.')


def run_native(factory):
    from native_ui.window import RavenWindow

    qt = QApplication.instance() or QApplication([])
    qt.setApplicationName('Raven')
    qt.setOrganizationName('Raven')
    bridge = Bridge()
    window = RavenWindow()
    worker = CoreThread(bridge, factory)
    window.action.connect(bridge.submit)
    window.shutdown_requested.connect(bridge.shutdown)
    bridge.snapshot.connect(window.receive)
    bridge.result.connect(window.action_result)
    bridge.failure.connect(window.show_error)
    worker.finished.connect(window.core_finished)

    def password_dialog(request):
        prompt, future = request
        if future.done():
            return
        password, accepted = QInputDialog.getText(window, 'Сховище Raven', prompt, QLineEdit.EchoMode.Password)
        if not future.done():
            future.set_result(password if accepted else '')
    bridge.password_needed.connect(password_dialog)
    window.show()
    worker.start()
    try:
        qt.exec()
    finally:
        # Normal close is deferred until finished; never terminate a live QThread.
        bridge.shutdown()
        worker.wait()
    return worker.exit_code

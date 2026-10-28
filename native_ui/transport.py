"""In-process reuse of the existing UI action boundary. No HTTP server."""
from __future__ import annotations

import asyncio
import copy
import json
import logging

from core.web_ui import LocalWebUI


class Wakeup:
    """Speaker callback compatible with LocalWebUI.changed, not a poller."""
    def __init__(self, notify):
        self.set = notify


class NativeTransport(LocalWebUI):
    console_input = False
    source_label = 'NATIVE'

    def __init__(self, app, deliver):
        super().__init__(app)
        self.loop = asyncio.get_running_loop()
        self.deliver = deliver
        self.changed = Wakeup(self.notify)
        self._scheduled = None
        self._active_refresh = None
        self._last_state = None
        self._closed = False

    async def start(self):
        self.notify()

    def notify(self):
        # Glow may be delivered from an audio thread. Coalesce on the core loop.
        if not self._closed:
            self.loop.call_soon_threadsafe(self._schedule)

    def _schedule(self):
        if not self._closed and self._scheduled is None:
            self._scheduled = self.loop.call_later(.1, self._flush)

    def _flush(self):
        self._scheduled = None
        if self._closed:
            return
        state = self.app.web_state()
        if not state.get('tts_playback'):
            state['tts_glow'] = None
        # Transfer snapshots, never live mutable objects, into the Qt thread.
        if state != self._last_state or self.events:
            self._last_state = copy.deepcopy(state)
            events = list(self.events)
            self.events.clear()
            self.deliver({'state': copy.deepcopy(state), 'events': events})
        # Executors currently expose snapshot-only progress. Refresh only while
        # a turn/task is active, never continuously while idle or paused.
        active = (state.get('status') in {'thinking', 'confirmation', 'processing_audio'}
                  or state.get('tts_busy') or (state.get('task') or {}).get('active'))
        if self._active_refresh:
            self._active_refresh.cancel()
            self._active_refresh = None
        if active:
            self._active_refresh = self.loop.call_later(.25, self.notify)

    async def dispatch(self, data):
        if self._closed:
            return 409, {'error': 'Сесію завершено.'}
        try:
            return await self._api('POST', '/api/action', json.dumps(data).encode())
        finally:
            self.notify()

    async def _submit(self, text, confirmation_id=None):
        try:
            await super()._submit(text, confirmation_id)
        finally:
            self.notify()

    async def close(self):
        if self._closed:
            return
        self._closed = True
        for handle in (self._scheduled, self._active_refresh):
            if handle:
                handle.cancel()
        await super().close()
        logging.getLogger(__name__).info('Native transport closed')

"""Small, opt-in loopback UI. No remote binding, filesystem API or tool bypass."""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections import deque
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from core.security import redact_user_text

logger = logging.getLogger(__name__)
ASSETS = {"/": ("index.html", "text/html"), "/app.css": ("app.css", "text/css"),
          "/app.js": ("app.js", "text/javascript"), "/avatar.svg": ("avatar.svg", "image/svg+xml")}


class LocalWebUI:
    def __init__(self, app, port=0):
        self.app = app
        self.port = port
        self.token = secrets.token_urlsafe(32)
        self.events = deque(maxlen=300)
        self.sequence = 0
        self.changed = asyncio.Event()
        self.server = None
        self.connections = set()
        self.submission = None
        self.assets = {}

    def publish(self, kind, text="", **data):
        self.sequence += 1
        self.events.append({"id": self.sequence, "kind": kind,
                            "text": redact_user_text(str(text))[:12000], **data})
        self.changed.set()

    async def start(self):
        root = Path(__file__).resolve().parent.parent / "web_ui"
        for route, (name, mime) in ASSETS.items():
            self.assets[route] = ((root / name).read_bytes(), mime + "; charset=utf-8")
        self.server = await asyncio.start_server(
            self._connection, "127.0.0.1", self.port, limit=16384,
        )
        self.port = self.server.sockets[0].getsockname()[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        # A fragment isn't sent to the HTTP server or included in Referer.
        return f"{self.origin}/#token={self.token}"

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if self.submission and not self.submission.done():
            self.submission.cancel()
            await asyncio.gather(self.submission, return_exceptions=True)
        active = list(self.connections)
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)

    async def _submit(self, text):
        try:
            logging.getLogger("session.dialogue").info("[WEB] %s", redact_user_text(text))
            await self.app._submit_text(text)
        except Exception:
            logger.exception("Web UI submission failed")
            self.publish("notice", "Повідомлення не вдалося передати. Перевірте журнал сесії.")

    async def _api(self, method, target, body):
        url = urlsplit(target)
        if method == "GET" and url.path == "/api/events":
            try:
                after = int(parse_qs(url.query).get("after", ["0"])[0])
            except (ValueError, TypeError):
                return 400, {"error": "Некоректний курсор."}
            if after < 0 or after > self.sequence:
                after = 0
            if after == self.sequence:
                self.changed.clear()
                try:
                    await asyncio.wait_for(self.changed.wait(), 1.0)
                except TimeoutError:
                    pass
            return 200, {"events": [e for e in self.events if e["id"] > after],
                         "cursor": self.sequence, "state": self.app.web_state(),
                         "truncated": bool(self.events and after < self.events[0]["id"] - 1)}
        if method != "POST" or url.path != "/api/action":
            return 404, {"error": "Невідомий маршрут."}
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return 400, {"error": "Некоректний JSON."}
        if not isinstance(data, dict):
            return 400, {"error": "Очікується об’єкт."}
        action = data.get("action")
        if action == "message":
            text = data.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 4000:
                return 400, {"error": "Повідомлення має містити від 1 до 4000 символів."}
            if self.submission and not self.submission.done():
                return 409, {"error": "Попереднє повідомлення ще передається. Зачекайте."}
            # The existing router, confirmation service and command queue own all actions.
            self.submission = asyncio.create_task(self._submit(text.strip()), name="web-submit")
        elif action in {"stop", "pause", "resume", "microphone", "confirm", "cancel_task"}:
            return await self.app.web_control(action, data)
        else:
            return 400, {"error": "Невідома дія."}
        return 202, {"ok": True}

    async def _connection(self, reader, writer):
        task = asyncio.current_task()
        if len(self.connections) >= 24:
            writer.close()
            return
        self.connections.add(task)
        try:
            async with asyncio.timeout(15):
                await self._request(reader, writer)
        except (TimeoutError, ConnectionError, asyncio.IncompleteReadError, ValueError):
            pass
        except Exception:
            # Never log headers, tokens, request bodies or private URLs.
            logger.exception("Local UI transport failed")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            self.connections.discard(task)

    async def _request(self, reader, writer):
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
        except asyncio.LimitOverrunError:
            await self._respond(writer, 431, {"error": "Завеликі заголовки."})
            return
        lines = raw.decode("iso-8859-1").split("\r\n")
        try:
            method, target, version = lines[0].split(" ")
            headers = {}
            for line in lines[1:]:
                if not line:
                    continue
                key, value = line.split(":", 1)
                key = key.lower().strip()
                if key in headers:
                    raise ValueError("duplicate header")
                headers[key] = value.strip()
        except ValueError:
            await self._respond(writer, 400, {"error": "Некоректний запит."})
            return
        if (version != "HTTP/1.1" or headers.get("host") != f"127.0.0.1:{self.port}"
                or "transfer-encoding" in headers):
            await self._respond(writer, 403, {"error": "Лише локальний доступ."})
            return
        # Reject DNS rebinding, foreign web pages and cross-site form submissions.
        if (headers.get("origin", self.origin) != self.origin
                or headers.get("sec-fetch-site") not in {None, "same-origin", "none"}):
            await self._respond(writer, 403, {"error": "Стороннє джерело запиту."})
            return
        if method == "GET" and target in self.assets:
            content, mime = self.assets[target]
            await self._respond(writer, 200, content, mime)
            return
        auth = headers.get("authorization", "")
        if not secrets.compare_digest(auth.encode("utf-8"), ("Bearer " + self.token).encode("ascii")):
            await self._respond(writer, 401, {"error": "Відкрийте посилання поточної сесії з термінала."})
            return
        try:
            size = int(headers.get("content-length", "0"))
        except ValueError:
            size = -1
        if not 0 <= size <= 16384:
            await self._respond(writer, 413, {"error": "Завеликий запит."})
            return
        if method == "POST" and headers.get("content-type", "").split(";")[0] != "application/json":
            await self._respond(writer, 415, {"error": "Потрібен application/json."})
            return
        body = await reader.readexactly(size)
        status, result = await self._api(method, target, body)
        await self._respond(writer, status, result)

    async def _respond(self, writer, status, content, mime="application/json; charset=utf-8"):
        if not isinstance(content, bytes):
            content = json.dumps(content, ensure_ascii=False).encode("utf-8")
        policy = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
                  "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        header = (f"HTTP/1.1 {status} Response\r\nContent-Type: {mime}\r\n"
                  f"Content-Length: {len(content)}\r\nConnection: close\r\n"
                  "Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n"
                  "Referrer-Policy: no-referrer\r\nX-Frame-Options: DENY\r\n"
                  "Permissions-Policy: microphone=(), camera=(), geolocation=()\r\n"
                  f"Content-Security-Policy: {policy}\r\n\r\n")
        writer.write(header.encode("ascii") + content)
        await writer.drain()

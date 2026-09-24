"""Bounded public-page reader. No browser, cookies, proxy, scripts or local URLs."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from email.parser import BytesParser
from html.parser import HTMLParser
import ipaddress
import re
import socket
import ssl
import zlib
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from services.web.intents import query_terms


class PageError(ValueError):
    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status


def read_failure_reason(exc):
    """Only fixed categories/status numbers, never URLs or HTTP response bodies."""
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ssl.SSLError):
        return "tls_error"
    if isinstance(exc, socket.gaierror):
        return "dns_error"
    if isinstance(exc, PageError):
        if str(exc) == "Browser verification required":
            return "browser_verification"
        if type(exc.status) is int and 100 <= exc.status <= 599:
            return f"http_{exc.status}"
        return {"Insufficient visible text": "insufficient_text", "Unsupported content type": "content_type",
                "Compressed content not accepted": "compressed_content", "Page too large": "page_too_large",
                "Invalid compressed content": "invalid_compression",
                "DNS contains non-public addresses": "non_public_address", "Redirect cycle": "redirect_cycle",
                "Too many redirects": "redirect_limit"}.get(str(exc), "page_rejected")
    if isinstance(exc, OSError):
        return "connection_error"
    return "read_failed"


def clean_text(text):
    return " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", " ", text).split())


def is_verification_page(title, visible):
    """Conservative short-interstitial heuristic, not a CAPTCHA solver.

    A CAPTCHA widget/script or a mention in a normal article is not enough.
    Unknown challenges can be missed; HTTP 401/403 alone is not proof.
    """
    title = clean_text(title).casefold().strip(' .!…')
    visible = clean_text(visible).casefold()
    if len(visible) > 1800:
        return False
    prompts = ('verify you are human', 'verifying you are human', 'checking your browser',
               'checking if the site connection is secure', 'підтвердьте, що ви людина',
               'перевірка вашого браузера')
    if title in {'just a moment', 'verify you are human', 'security verification',
                 'checking your browser', 'robot or human?', 'attention required! | cloudflare'}:
        if (not visible or any(prompt in visible[:500] for prompt in prompts)
                or 'enable javascript and cookies' in visible[:500]
                or ('please wait' in visible and len(visible) < 300)):
            return True
    if any(visible.startswith(prompt) for prompt in prompts):
        return True
    return ('verification successful' in visible[:150]
            and 'you will now be taken to the requested page' in visible[:350])


def relevant_excerpt(text, query="", limit=5000):
    """Select bounded windows from already downloaded text, without more HTTP/LLM calls.

    Simple lexical ranking, not a semantic relevance or truth guarantee.
    Keep the introduction and restore document order for selected windows.
    """
    text = clean_text(text)
    if len(text) <= limit or not query:
        return text[:limit]
    terms = query_terms(query)
    if not terms:
        return text[:limit]
    # Split on whitespace to avoid cutting a search word at a window boundary.
    windows, current = [], ""
    for word in text.split():
        if len(current) + len(word) + 1 > 900 and current:
            windows.append(current)
            current = ""
        current = (current + " " + word).strip()
    if current:
        windows.append(current)
    ranked = sorted(range(1, len(windows)),
                    key=lambda i: (-sum(term in windows[i].casefold() for term in terms), i))
    chosen = {0}
    used = len(windows[0])
    for index in ranked:
        if used + len(windows[index]) + 5 <= limit:
            chosen.add(index)
            used += len(windows[index]) + 5
    return " […] ".join(windows[i] for i in sorted(chosen))[:limit]


def public_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 for c in url) or "\\" in url:
        raise PageError("Invalid URL")
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        if (parsed.scheme not in {"http", "https"} or not host or parsed.username is not None
                or parsed.password is not None or parsed.port not in {None, 80, 443}):
            raise PageError("Unsupported URL")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise PageError("Local destination")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise PageError("Non-public address")
        authority = f"[{host}]" if ":" in host else host
        if parsed.port:
            authority += f":{parsed.port}"
        return urlunsplit((parsed.scheme, authority, quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~"),
                           quote(parsed.query, safe="%/:?@!$&'()*+,;=-._~"), ""))
    except (ValueError, UnicodeError) as exc:
        raise PageError("Invalid public URL") from exc


class VisibleText(HTMLParser):
    IGNORED = {"script", "style", "noscript", "svg", "nav", "header", "footer", "form", "template"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    BOILERPLATE = re.compile(r"(?:^|[\s_-])(?:menu|navbar|sidebar|breadcrumb|breadcrumbs|cookie|cookies|"
                            r"related|share|sharing|social|comments|advert|advertisement|ads|toc)(?:$|[\s_-])", re.I)

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.main_parts = []
        self.title = []
        self.published = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and (attrs.get("property") or attrs.get("name") or "").lower() in {
            "article:published_time", "datepublished", "date", "pubdate"
        }:
            value = attrs.get("content") or ""
            if re.match(r"^\d{4}-\d{2}-\d{2}(?:T|$)", value):
                try:
                    self.published = date.fromisoformat(value[:10]).isoformat()
                except ValueError:
                    pass
        content_header = tag == 'header' and bool(self.stack and self.stack[-1][2])
        hidden = ((tag in self.IGNORED and not content_header) or tag == "aside" or "hidden" in attrs or attrs.get("aria-hidden") == "true"
                  or attrs.get("role", "").lower() in {"navigation", "banner", "contentinfo", "complementary", "dialog"}
                  or (tag not in {"html", "body", "main", "article"} and
                      bool(self.BOILERPLATE.search((attrs.get("id") or "") + " " + (attrs.get("class") or ""))))
                  or bool(re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", attrs.get("style") or "", re.I)))
        if tag not in self.VOID:
            main = tag in {"main", "article"} or attrs.get("role") == "main" or attrs.get("itemprop") == "articleBody"
            self.stack.append((tag, hidden or bool(self.stack and self.stack[-1][1]),
                               main or bool(self.stack and self.stack[-1][2])))
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "article"}:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.stack and self.stack[-1][1]:
            return
        if any(item[0] == "title" for item in self.stack):
            self.title.append(data)
        else:
            self.parts.append(data)
            if self.stack and self.stack[-1][2]:
                self.main_parts.append(data)

    def extract(self, query=""):
        # Fixed excerpt budget; markup never becomes commands or tools.
        main = " ".join(self.main_parts)
        # Prefer semantic content containers, but do not lose a short page
        # because an unrelated tiny widget used an <article> element.
        text = main if len(main.split()) >= 40 else " ".join(self.parts)
        return relevant_excerpt(text, query)


class PublicPageReader:
    # Bound both transferred and expanded bytes; never parse a truncated page.
    MAX_BYTES = 2_000_000

    @classmethod
    def decode_body(cls, body, encoding):
        if encoding == 'identity':
            return body
        if encoding != 'gzip':
            raise PageError('Compressed content not accepted')
        try:
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            result = decoder.decompress(body, cls.MAX_BYTES + 1)
            if len(result) > cls.MAX_BYTES or decoder.unconsumed_tail:
                raise PageError('Page too large')
            if not decoder.eof or decoder.unused_data:
                raise PageError('Invalid compressed content')
            return result
        except zlib.error as exc:
            raise PageError('Invalid compressed content') from exc

    def __init__(self, timeout=7.0):
        self.timeout = timeout
        self.slots = asyncio.Semaphore(3)

    async def resolve(self, host, port):
        rows = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(row[4][0] for row in rows))
        # Reject mixed public/private answers, and pin the validated IP at connect time.
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise PageError("DNS contains non-public addresses")
        return addresses[0]

    async def exchange(self, url):
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        address = await self.resolve(parsed.hostname, port)
        options = {}
        if parsed.scheme == "https":
            options = {"ssl": ssl.create_default_context(), "server_hostname": parsed.hostname}
        reader, writer = await asyncio.open_connection(address, port, limit=65536, **options)
        try:
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            writer.write((f"GET {target} HTTP/1.1\r\nHost: {parsed.netloc}\r\n"
                          "User-Agent: ValleRa/1.0 (public page reader)\r\n"
                          "Accept: text/html, text/plain\r\nAccept-Encoding: gzip, identity\r\n"
                          "Connection: close\r\n\r\n").encode("ascii"))
            await writer.drain()
            head = await reader.readuntil(b"\r\n\r\n")
            status_line, fields = head.split(b"\r\n", 1)
            match = re.fullmatch(rb"HTTP/1\.[01] (\d{3})(?: .*)?", status_line)
            if not match:
                raise PageError("Invalid HTTP status")
            status = int(match[1])
            headers = BytesParser().parsebytes(fields)
            if headers.get('cf-mitigated', '').strip().lower() == 'challenge':
                raise PageError('Browser verification required', status=status)
            if status in {301, 302, 303, 307, 308}:
                return status, headers, b""
            if status != 200:
                raise PageError("Page unavailable", status=status)
            if headers.get_content_type() not in {"text/html", "text/plain"}:
                raise PageError("Unsupported content type")
            encoding = headers.get("Content-Encoding", "identity").strip().lower()
            if encoding not in {'identity', 'gzip'}:
                raise PageError("Compressed content not accepted")
            data = bytearray()
            transfer = headers.get("Transfer-Encoding", "").lower()
            if transfer and transfer != "chunked":
                raise PageError("Unsupported transfer encoding")
            length = headers.get("Content-Length")
            if transfer:
                while True:
                    line = await reader.readuntil(b"\r\n")
                    if len(line) > 128:
                        raise PageError("Invalid chunk")
                    size = int(line.strip().split(b";", 1)[0], 16)
                    if size == 0:
                        break
                    if size < 0 or len(data) + size > self.MAX_BYTES:
                        raise PageError("Page too large")
                    data.extend(await reader.readexactly(size))
                    if await reader.readexactly(2) != b"\r\n":
                        raise PageError("Invalid chunk delimiter")
            elif length is not None:
                size = int(length)
                if not 0 <= size <= self.MAX_BYTES:
                    raise PageError("Page too large")
                data.extend(await reader.readexactly(size))
            else:
                while block := await reader.read(16384):
                    data.extend(block)
                    if len(data) > self.MAX_BYTES:
                        raise PageError("Page too large")
            return status, headers, self.decode_body(bytes(data), encoding)
        finally:
            writer.close()
            try:
                async with asyncio.timeout(0.5):
                    await writer.wait_closed()
            except (OSError, TimeoutError):
                pass

    async def read(self, url, query=""):
        async with asyncio.timeout(self.timeout), self.slots:
            visited = set()
            for _ in range(4):
                url = public_url(url)
                if url in visited:
                    raise PageError("Redirect cycle")
                visited.add(url)
                status, headers, body = await self.exchange(url)
                if status in {301, 302, 303, 307, 308}:
                    location = headers.get("Location")
                    if not location:
                        raise PageError("Missing redirect target")
                    url = urljoin(url, location)
                    continue
                encoding = headers.get_content_charset() or "utf-8"
                text = body.decode(encoding, errors="replace")
                parser = VisibleText()
                if headers.get_content_type() == "text/html":
                    parser.feed(text)
                    if is_verification_page(' '.join(parser.title), ' '.join(parser.parts)):
                        raise PageError('Browser verification required')
                    text = parser.extract(query)
                else:
                    if is_verification_page('', text):
                        raise PageError('Browser verification required')
                    text = relevant_excerpt(text, query)
                if len(text.split()) < 15:
                    raise PageError("Insufficient visible text")
                return {"href": url, "title": clean_text(" ".join(parser.title))[:150], "text": text,
                        "published": parser.published,
                        "retrieved": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            raise PageError("Too many redirects")

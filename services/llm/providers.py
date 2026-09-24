from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from typing import TypeVar

from services.llm.base import LLMProvider, ProviderStatus
from services.llm.errors import ResponseError
from services.llm.response_diagnostics import ResponseDiagnostics
from services.llm.timing import RequestTiming, attach_timing, traced

T = TypeVar("T")


def _error_detail(exc: Exception) -> str:
    return str(exc).strip() or repr(exc) or type(exc).__name__


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, model: str, api_key: str = "", timeout: int = 10):
        super().__init__(model, api_key, timeout)
        self._shared_client = None
        self._client_loop = None
        self._active_requests: set[asyncio.Task] = set()
        self._closed = False
        self._close_task = None

    _BLOCKED_REASONS = {
        "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII",
        "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT", "IMAGE_RECITATION",
        "MODEL_ARMOR", "JAILBREAK",
    }
    _KNOWN_REASONS = _BLOCKED_REASONS | {
        "STOP", "MAX_TOKENS", "LANGUAGE", "OTHER", "MALFORMED_FUNCTION_CALL",
        "UNEXPECTED_TOOL_CALL", "NO_IMAGE", "IMAGE_OTHER", "FINISH_REASON_UNSPECIFIED",
        "BLOCK_REASON_UNSPECIFIED", "BLOCKED_REASON_UNSPECIFIED",
    }

    @classmethod
    def _reason_code(cls, value) -> str:
        # SDK enum values only: never log finish_message or block_reason_message,
        # which can contain the user's text or other sensitive API data.
        if value is None:
            return "UNSPECIFIED"
        code = getattr(value, "value", value)
        return code if isinstance(code, str) and code in cls._KNOWN_REASONS else "UNKNOWN"

    @classmethod
    def _response_reason(cls, response) -> str:
        feedback = getattr(response, "prompt_feedback", None)
        block = cls._reason_code(getattr(feedback, "block_reason", None))
        if block not in {"UNSPECIFIED", "BLOCK_REASON_UNSPECIFIED", "BLOCKED_REASON_UNSPECIFIED"}:
            raise ResponseError("response_blocked", block)
        candidates = getattr(response, "candidates", None) or []
        reason = cls._reason_code(getattr(candidates[0], "finish_reason", None)) if candidates else "UNSPECIFIED"
        if reason in cls._BLOCKED_REASONS:
            raise ResponseError("response_blocked", reason)
        return reason

    @staticmethod
    def _validate_completion(has_text: bool, reason: str) -> None:
        if reason == "MAX_TOKENS":
            raise ResponseError("output_limit", reason)
        if reason not in {"STOP", "UNSPECIFIED", "FINISH_REASON_UNSPECIFIED"}:
            raise ResponseError("unsupported_response", reason)
        if not has_text:
            raise ResponseError("empty_response", reason)

    @staticmethod
    def _request(messages: list[dict[str, str]]):
        from google.genai import types

        instructions = []
        contents = []
        for message in messages:
            text = message.get("content", "")
            if not text.strip():
                continue
            role = message.get("role")
            if role == "system":
                instructions.append(text)
            elif role in {"user", "assistant"}:
                contents.append(types.Content(
                    role="model" if role == "assistant" else "user",
                    parts=[types.Part(text=text)],
                ))
            else:
                raise ValueError("Непідтримувана роль повідомлення Gemini")
        if not contents:
            raise ValueError("Gemini отримав порожній діалог")
        # Preserve all message text and its role. A flattened transcript is one
        # user turn to the API, not an actual multi-turn conversation.
        config = types.GenerateContentConfig(
            system_instruction="\n\n".join(instructions) or None,
        )
        return contents, config

    def _client(self, timeout_seconds: int):
        from google import genai
        from google.genai import types
        import httpx

        if self._closed:
            raise RuntimeError("Gemini provider is closed")
        loop = asyncio.get_running_loop()
        if self._client_loop is not None and self._client_loop is not loop:
            raise RuntimeError("Gemini client cannot be shared across event loops")
        # No await between checking/creating: concurrent requests on this loop
        # cannot create two clients. Timeouts belong to each request, not the pool.
        if self._shared_client is None:
            self._shared_client = genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(
                    timeout=max(self.timeout, 60) * 1000,
                    async_client_args={'limits': httpx.Limits(keepalive_expiry=60.0),
                                       'event_hooks': {'request': [attach_timing]}},
                    # LLMManager owns retries/429 cooldowns, not the SDK.
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
            self._client_loop = loop
        task = asyncio.current_task()
        if task is not None:
            self._active_requests.add(task)
        return self._shared_client

    async def _release_client(self, client) -> None:
        # Finishing/cancelling one request must not close sibling requests.
        self._active_requests.discard(asyncio.current_task())

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            owner = asyncio.current_task()
            self._close_task = asyncio.create_task(self._shutdown(owner))
            self._close_task.add_done_callback(self._consume_background_result)
        # A caller cancelled during shutdown must not abandon the transport.
        await asyncio.shield(self._close_task)

    async def _shutdown(self, owner) -> None:
        active = {task for task in self._active_requests if task is not owner and not task.done()}
        for task in active:
            task.cancel()
        if active:
            _, pending = await asyncio.wait(active, timeout=2)
            for task in pending:
                task.add_done_callback(self._consume_background_result)
        client, self._shared_client = self._shared_client, None
        if client is not None:
            async with asyncio.timeout(5):
                await self._close_client(client)
        self._active_requests.clear()

    async def healthcheck(self) -> ProviderStatus:
        if not self.api_key:
            return ProviderStatus(self.name, False, "API-ключ відсутній")

        healthcheck_timeout = max(self.timeout, 30)

        try:
            await self._with_deadline(
                self._check_async(healthcheck_timeout),
                healthcheck_timeout,
                cancel_on_timeout=True,
            )
            return ProviderStatus(self.name, True, self.model)

        except asyncio.TimeoutError:
            return ProviderStatus(
                self.name,
                False,
                f"Тайм-аут перевірки Gemini: {healthcheck_timeout} с",
            )

        except Exception as exc:
            detail = _error_detail(exc)
            return ProviderStatus(
                self.name,
                False,
                f"{type(exc).__name__}: {detail}",
            )

    async def _check_async(self, timeout_seconds: int) -> None:
        """
        Перевіряє доступність саме вибраної моделі.

        Client зберігається в локальній змінній протягом усього запиту.
        Це важливо, бо models.list() може повертати пейджер/ітератор,
        якому ще потрібен живий HTTP-клієнт.
        """
        from google.genai import types
        client = self._client(timeout_seconds)

        try:
            model_info = await client.aio.models.get(
                model=self.model,
                config=types.GetModelConfig(http_options=types.HttpOptions(timeout=timeout_seconds * 1000)),
            )

            if model_info is None:
                raise RuntimeError(
                    f"Модель {self.model} недоступна"
                )

            supported_actions = getattr(
                model_info,
                "supported_actions",
                None,
            ) or []

            if (
                supported_actions
                and "generateContent" not in supported_actions
            ):
                raise RuntimeError(
                    f"Модель {self.model} не підтримує generateContent"
                )

        finally:
            await self._release_client(client)

    async def chat(self, messages: list[dict[str, str]]) -> str:
        chat_timeout = max(self.timeout, 60)

        try:
            return await self._with_deadline(
                self._chat_async(messages, chat_timeout),
                chat_timeout,
                cancel_on_timeout=True,
            )

        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"Gemini не відповів за {chat_timeout} секунд"
            ) from exc

    async def chat_structured(self, messages: list[dict[str, str]], schema: dict) -> str:
        timeout = max(self.timeout, 60)
        return await self._with_deadline(
            self._chat_async(messages, timeout, response_schema=schema),
            timeout, cancel_on_timeout=True,
        )

    async def _chat_async(
        self,
        messages: list[dict[str, str]],
        timeout_seconds: int,
        *,
        response_schema: dict | None = None,
    ) -> str:
        contents, config = self._request(messages)
        from google.genai import types
        config.http_options = types.HttpOptions(timeout=timeout_seconds * 1000)
        if response_schema is not None:
            # Request-level options only: ordinary chat/streaming are unchanged.
            config.response_mime_type = "application/json"
            config.response_json_schema = response_schema
        client = self._client(timeout_seconds)

        try:
            response = await client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )

            diagnostics = ResponseDiagnostics()
            diagnostics.observe(response)
            try:
                reason = self._response_reason(response)
                text = response.text or ""
                self._validate_completion(bool(text.strip()), reason)
            except ResponseError as exc:
                diagnostics.attach(exc)
                raise

            return text.strip()

        finally:
            await self._release_client(client)

    async def chat_stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        timing = RequestTiming(messages)
        contents, config = self._request(messages)
        timeout = max(self.timeout, 60)
        from google.genai import types
        config.http_options = types.HttpOptions(timeout=timeout * 1000)
        client = self._client(timeout)
        timing.mark("client_ready")
        stream = None
        reason = "UNSPECIFIED"
        has_text = False
        diagnostics = ResponseDiagnostics()
        status = "incomplete"
        try:
            async with asyncio.timeout(timeout):
                stream = await traced(timing, client.aio.models.generate_content_stream(
                    model=self.model, contents=contents, config=config,
                ))
                timing.mark("stream_ready")
                while True:
                    try:
                        chunk = await traced(timing, anext(stream))
                    except StopAsyncIteration:
                        break
                    timing.observe(chunk)
                    diagnostics.observe(chunk)
                    current_reason = self._response_reason(chunk)
                    if current_reason not in {"UNSPECIFIED", "FINISH_REASON_UNSPECIFIED"}:
                        reason = current_reason
                    text = chunk.text or ""
                    if text:
                        if text.strip():
                            timing.mark("first_text")
                        has_text = has_text or bool(text.strip())
                        yield text
                self._validate_completion(has_text, reason)
                status = "ok"
        except ResponseError as exc:
            diagnostics.attach(exc)
            raise
        finally:
            try:
                if stream is not None:
                    await stream.aclose()
            finally:
                try:
                    await self._release_client(client)
                finally:
                    timing.finish(status)

    @staticmethod
    async def _close_client(client) -> None:
        try:
            await client.aio.aclose()
        finally:
            # The SDK documents that aclose() releases only the async transport;
            # Client.close() releases the sync transport created by the parent.
            await asyncio.to_thread(client.close)

    @staticmethod
    async def _with_deadline(
        awaitable: Awaitable[T],
        timeout_seconds: float,
        *,
        cancel_on_timeout: bool,
    ) -> T:
        task = asyncio.create_task(awaitable)
        try:
            done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
        except asyncio.CancelledError:
            task.cancel()
            task.add_done_callback(GeminiProvider._consume_background_result)
            raise
        if task in done:
            return task.result()

        if cancel_on_timeout:
            task.cancel()
        task.add_done_callback(GeminiProvider._consume_background_result)
        raise asyncio.TimeoutError

    @staticmethod
    def _consume_background_result(task: asyncio.Task) -> None:
        try:
            task.result()
        except BaseException:
            # The foreground already reported the timeout. Consume the eventual
            # SDK result so asyncio does not emit an unhandled-task warning.
            pass


class GroqProvider(LLMProvider):
    name = "groq"

    def _client(self):
        from groq import Groq
        return Groq(api_key=self.api_key, timeout=self.timeout)

    async def healthcheck(self) -> ProviderStatus:
        if not self.api_key:
            return ProviderStatus(self.name, False, "API-ключ відсутній")
        try:
            await asyncio.wait_for(asyncio.to_thread(self._client().models.list), self.timeout)
            return ProviderStatus(self.name, True, self.model)
        except Exception as exc:
            return ProviderStatus(self.name, False, _error_detail(exc))

    async def chat(self, messages: list[dict[str, str]]) -> str:
        def call():
            response = self._client().chat.completions.create(
                model=self.model,
                messages=messages,
            )
            return response.choices[0].message.content or ""
        return await asyncio.wait_for(asyncio.to_thread(call), self.timeout)


class OpenAIProvider(LLMProvider):
    name = "openai"

    def _client(self):
        from openai import OpenAI
        return OpenAI(api_key=self.api_key, timeout=self.timeout)

    async def healthcheck(self) -> ProviderStatus:
        if not self.api_key:
            return ProviderStatus(self.name, False, "API-ключ відсутній")
        try:
            await asyncio.wait_for(asyncio.to_thread(self._client().models.list), self.timeout)
            return ProviderStatus(self.name, True, self.model)
        except Exception as exc:
            return ProviderStatus(self.name, False, _error_detail(exc))

    async def chat(self, messages: list[dict[str, str]]) -> str:
        def call():
            response = self._client().responses.create(
                model=self.model,
                input=messages,
            )
            return response.output_text or ""
        return await asyncio.wait_for(asyncio.to_thread(call), self.timeout)


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def _client(self):
        import anthropic
        return anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout)

    async def healthcheck(self) -> ProviderStatus:
        if not self.api_key:
            return ProviderStatus(self.name, False, "API-ключ відсутній")
        try:
            await asyncio.wait_for(
                asyncio.to_thread(lambda: self._client().models.list(limit=1)),
                self.timeout,
            )
            return ProviderStatus(self.name, True, self.model)
        except Exception as exc:
            return ProviderStatus(self.name, False, _error_detail(exc))

    async def chat(self, messages: list[dict[str, str]]) -> str:
        def call():
            system = "\n".join(m["content"] for m in messages if m["role"] == "system")
            dialog = [m for m in messages if m["role"] in {"user", "assistant"}]
            response = self._client().messages.create(
                model=self.model,
                max_tokens=1200,
                system=system,
                messages=dialog,
            )
            return "".join(block.text for block in response.content if block.type == "text")
        return await asyncio.wait_for(asyncio.to_thread(call), self.timeout)


class OllamaProvider(LLMProvider):
    name = "ollama"

    def _client(self):
        from ollama import Client
        return Client(host="http://localhost:11434")

    async def healthcheck(self) -> ProviderStatus:
        try:
            detail = await asyncio.wait_for(asyncio.to_thread(self._check), self.timeout)
            return ProviderStatus(self.name, bool(detail), detail or "модель не встановлена")
        except Exception as exc:
            return ProviderStatus(self.name, False, _error_detail(exc))

    def _check(self) -> str:
        response = self._client().list()
        models = response.get("models", []) if isinstance(response, dict) else response.models
        names = []
        for model in models:
            names.append(
                model.get("model", "")
                if isinstance(model, dict)
                else getattr(model, "model", "")
            )
        if self.model:
            return self.model if self.model in names else ""
        return names[0] if names else ""

    async def chat(self, messages: list[dict[str, str]]) -> str:
        if not self.model:
            raise RuntimeError("Модель Ollama не вибрана у config.json")

        def call():
            response = self._client().chat(model=self.model, messages=messages)
            return (
                response["message"]["content"]
                if isinstance(response, dict)
                else response.message.content
            )

        return await asyncio.wait_for(
            asyncio.to_thread(call),
            max(self.timeout, 120),
        )

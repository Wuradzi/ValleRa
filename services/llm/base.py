from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from collections.abc import AsyncIterator


@dataclass(slots=True)
class ProviderStatus:
    name: str
    available: bool
    detail: str = ""


class LLMProvider(ABC):
    name: str

    def __init__(self, model: str, api_key: str = "", timeout: int = 10):
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    @abstractmethod
    async def healthcheck(self) -> ProviderStatus:
        raise NotImplementedError

    @abstractmethod
    async def chat(self, messages: list[dict[str, str]]) -> str:
        raise NotImplementedError

    async def chat_stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        # Providers without native streaming retain their existing behavior.
        yield await self.chat(messages)

    async def chat_structured(self, messages: list[dict[str, str]], schema: dict) -> str:
        # Providers without schema support still undergo local output validation.
        return await self.chat(messages)

    async def close(self) -> None:
        """Release provider-owned resources at application shutdown."""

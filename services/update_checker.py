from __future__ import annotations

import httpx

from version import VERSION


class UpdateChecker:
    def __init__(self, repository: str):
        self.repository = repository.strip().strip("/")

    async def check(self) -> str | None:
        if not self.repository or "/" not in self.repository:
            return None
        url = f"https://api.github.com/repos/{self.repository}/releases/latest"
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            response = await client.get(
                url,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "ValleRa"},
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()

        latest = str(data.get("tag_name", "")).lstrip("v")
        if latest and latest != VERSION:
            return latest
        return None

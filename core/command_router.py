from __future__ import annotations

import asyncio
import logging
import platform

from rapidfuzz import fuzz

from core.models import SkillResult

logger = logging.getLogger(__name__)


class CommandRouter:
    def __init__(self, skills, threshold: int = 85):
        current = platform.system().lower()
        self.skills = [
            skill for skill in skills
            if current in skill.platforms or "all" in skill.platforms
        ]
        self.threshold = threshold

    async def route(
        self,
        command: str,
        context,
        allowed_skills: set[str] | None = None,
    ) -> SkillResult:
        normalized = " ".join(command.lower().strip().split())
        candidates = []
        skills = [
            skill for skill in self.skills
            if allowed_skills is None or skill.name in allowed_skills
        ]

        for skill in skills:
            score = max(
                (
                    max(
                        fuzz.partial_ratio(normalized, trigger.lower()),
                        fuzz.token_set_ratio(normalized, trigger.lower()),
                    )
                    for trigger in skill.triggers
                ),
                default=0,
            )
            if score >= self.threshold:
                try:
                    can_handle = await asyncio.wait_for(
                        skill.can_handle(normalized),
                        timeout=3.0,
                    )
                except Exception:
                    logger.exception("Skill can_handle failed: %s", skill.name)
                    continue
                if can_handle:
                    candidates.append((score, skill))

        if not candidates:
            for skill in skills:
                try:
                    can_handle = await asyncio.wait_for(
                        skill.can_handle(normalized),
                        timeout=3.0,
                    )
                except Exception:
                    logger.exception("Skill can_handle failed: %s", skill.name)
                    continue
                if can_handle:
                    candidates.append((self.threshold, skill))

        candidates.sort(key=lambda item: item[0], reverse=True)
        for _, skill in candidates:
            try:
                result = await asyncio.wait_for(
                    skill.handle(normalized, context),
                    timeout=120.0,
                )
            except asyncio.TimeoutError:
                logger.error("Skill timed out: %s", skill.name)
                return SkillResult(True, "Час виконання дії минув. Результат невідомий; автоматично не повторюю.",
                                   {"command_type": "skill_timeout", "success": False})
            except Exception:
                logger.exception("Skill failed: %s", skill.name)
                return SkillResult(True, "Під час виконання дії сталася помилка. Перевірте результат; автоматично не повторюю.",
                                   {"command_type": "skill_error", "success": False})
            if result.handled:
                return result
        return SkillResult(False)

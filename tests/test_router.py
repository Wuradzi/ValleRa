import unittest

from core.command_router import CommandRouter
from core.models import SkillResult


class FakeSkill:
    name = "test"
    description = ""
    triggers = ["відкрий браузер"]
    platforms = ["windows", "linux"]

    async def can_handle(self, command):
        return True

    async def handle(self, command, context):
        return SkillResult(True, "ok")


class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_fuzzy_route(self):
        result = await CommandRouter([FakeSkill()], 85).route(
            "відкрий браузер",
            None,
        )
        self.assertTrue(result.handled)
        self.assertEqual(result.response, "ok")


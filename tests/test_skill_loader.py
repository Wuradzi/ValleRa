import json
import tempfile
import unittest
from pathlib import Path

from core.skill_loader import SkillLoader


class SkillLoaderTests(unittest.TestCase):
    def test_invalid_manifest_is_skipped_without_breaking_other_skills(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            invalid = root / "invalid"
            invalid.mkdir()
            (invalid / "manifest.json").write_text("{broken", encoding="utf-8")

            valid = root / "valid"
            valid.mkdir()
            (valid / "manifest.json").write_text(
                json.dumps(
                    {
                        "name": "valid",
                        "triggers": ["тест"],
                        "platforms": ["all"],
                    }
                ),
                encoding="utf-8",
            )
            (valid / "skill.py").write_text(
                "from core.models import SkillResult\n"
                "async def handle(command, context, services):\n"
                "    return SkillResult(True, 'ok')\n",
                encoding="utf-8",
            )

            loaded = SkillLoader(root, {}).load()

        self.assertEqual([skill.name for skill in loaded], ["valid"])


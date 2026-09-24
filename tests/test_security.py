import os
import unittest
from unittest.mock import patch

from core.security import redact_user_text, sanitized_environment


class SecurityTests(unittest.TestCase):
    def test_child_environment_drops_credentials(self):
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "secret",
                "SERVICE_TOKEN": "token",
                "SAFE_SETTING": "visible",
            },
            clear=True,
        ):
            environment = sanitized_environment({"TASK_VALUE": "ok"})

        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("SERVICE_TOKEN", environment)
        self.assertEqual(environment["SAFE_SETTING"], "visible")
        self.assertEqual(environment["TASK_VALUE"], "ok")

    def test_console_redaction_preserves_label_not_value(self):
        redacted = redact_user_text("OPENAI_API_KEY=abc-Secret-123")
        self.assertEqual(redacted, "OPENAI_API_KEY=[REDACTED]")


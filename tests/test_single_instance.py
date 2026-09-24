from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from core.single_instance import SingleInstance


class SingleInstanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.lock_path = Path(self.temp_dir.name) / "valera.lock"

    def test_second_instance_is_rejected(self) -> None:
        first = SingleInstance(self.lock_path)
        second = SingleInstance(self.lock_path)

        self.assertTrue(first.acquire())
        self.addCleanup(first.release)
        self.assertFalse(second.acquire())

    def test_stale_legacy_lock_is_recovered(self) -> None:
        self.lock_path.write_text("999999999", encoding="ascii")
        instance = SingleInstance(self.lock_path)

        self.assertTrue(instance.acquire())
        instance.release()
        self.assertFalse(self.lock_path.exists())

    def test_legacy_lock_for_current_process_is_respected(self) -> None:
        self.lock_path.write_text(str(os.getpid()), encoding="ascii")

        self.assertFalse(SingleInstance(self.lock_path).acquire())

    def test_reused_pid_lock_is_recovered(self) -> None:
        self.lock_path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "created_at": 0,
                    "token": "old-instance",
                }
            ),
            encoding="ascii",
        )
        instance = SingleInstance(self.lock_path)

        self.assertTrue(instance.acquire())
        instance.release()
        self.assertFalse(self.lock_path.exists())

    def test_release_does_not_delete_a_foreign_lock(self) -> None:
        instance = SingleInstance(self.lock_path)
        self.assertTrue(instance.acquire())
        self.lock_path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "created_at": 0,
                    "token": "foreign-instance",
                }
            ),
            encoding="ascii",
        )

        instance.release()

        self.assertTrue(self.lock_path.exists())


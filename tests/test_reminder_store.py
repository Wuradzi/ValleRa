import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from services.storage.reminder_store import ReminderStore


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)


class ReminderStoreTests(unittest.TestCase):
    def test_missed_recurring_reminder_is_rolled_forward(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ReminderStore(Path(temp_dir) / "reminders.json")
            item = store.add(
                "Щоденна перевірка",
                NOW - timedelta(days=3),
                recurrence="daily",
            )

            with patch("services.storage.reminder_store.datetime", _FixedDateTime):
                missed = store.missed()

            saved = store.all()[0]
            self.assertEqual(missed[0]["id"], item["id"])
            self.assertEqual(saved["status"], "pending")
            self.assertGreater(datetime.fromisoformat(saved["due_at"]), NOW)

    def test_missed_one_time_reminder_is_marked_missed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ReminderStore(Path(temp_dir) / "reminders.json")
            store.add("Разова перевірка", NOW - timedelta(minutes=5))

            with patch("services.storage.reminder_store.datetime", _FixedDateTime):
                store.missed()

            self.assertEqual(store.all()[0]["status"], "missed")

    def test_triggered_overdue_interval_moves_to_future(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ReminderStore(Path(temp_dir) / "reminders.json")
            item = store.add(
                "Періодична перевірка",
                NOW - timedelta(minutes=10),
                recurrence="interval",
                interval_seconds=60,
            )

            with patch("services.storage.reminder_store.datetime", _FixedDateTime):
                store.mark_triggered(item["id"])

            saved = store.all()[0]
            self.assertEqual(saved["status"], "pending")
            self.assertGreater(datetime.fromisoformat(saved["due_at"]), NOW)


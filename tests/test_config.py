import json
import unittest
from pathlib import Path

from config import (
    ConfigError,
    ProjectPaths,
    Settings,
    apply_performance_profile,
    merge_config,
    validate_config,
)


class ConfigTests(unittest.TestCase):
    def test_selective_refinement_budgets_and_thresholds(self):
        config = merge_config({})
        self.assertTrue(config['stt']['selective_whisper_enabled'])
        self.assertEqual(config['stt']['whisper_hard_budget_ms'], 6000)
        validate_config(config)
        for changes in ({'whisper_soft_budget_ms': 0}, {'whisper_hard_budget_ms': 3000},
                        {'vosk_chat_confidence': float('nan')}, {'selective_whisper_enabled': 'true'}):
            with self.subTest(changes=changes):
                broken = merge_config({'stt': changes})
                with self.assertRaises(ConfigError):
                    validate_config(broken)

    def test_history_request_default_and_explicit_override(self):
        example = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(Settings(ProjectPaths.from_root(Path("."))).history_limit, 12)
        self.assertEqual(merge_config({})["llm"]["history_limit"], 12)
        self.assertEqual(example["llm"]["history_limit"], 12)
        self.assertEqual(merge_config({"llm": {"history_limit": 30}})["llm"]["history_limit"], 30)

    def test_default_whisper_base_without_hints_matches_settings_and_example(self):
        config = merge_config({})
        settings = Settings(ProjectPaths.from_root(Path(".")))
        example = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(settings.stt_whisper_model, "base")
        self.assertEqual(settings.stt_whisper_prompt, "")
        self.assertEqual(settings.stt_whisper_hotwords, "")
        for source in (config, example):
            with self.subTest(source="default" if source is config else "example"):
                whisper = source["stt"]["whisper"]
                self.assertEqual(whisper["model"], "base")
                self.assertEqual(whisper["prompt"], "")
                self.assertEqual(whisper["hotwords"], "")
                self.assertEqual(whisper["compute_type"], "int8")
                validate_config(source)

    def test_explicit_whisper_settings_are_not_overwritten_by_defaults(self):
        config = merge_config({"stt": {"whisper": {
            "model": "small", "prompt": "Custom", "hotwords": "Custom",
        }}})
        whisper = apply_performance_profile(config, "fast")["stt"]["whisper"]
        self.assertEqual(whisper["model"], "small")
        self.assertEqual(whisper["prompt"], "Custom")
        self.assertEqual(whisper["hotwords"], "Custom")

    def test_default_gemini_model_is_flash_lite(self):
        config = merge_config({})
        self.assertEqual(config["llm"]["models"]["gemini"], "gemini-3.5-flash-lite")
        self.assertEqual(config["llm"]["order"][0], "gemini")
        validate_config(config)

    def test_explicit_gemini_model_selection_is_preserved(self):
        config = merge_config({"llm": {"models": {"gemini": "custom-model"}}})
        self.assertEqual(config["llm"]["models"]["gemini"], "custom-model")

    def test_merge_keeps_defaults_and_custom_value(self):
        config = merge_config({"tts": {"voice_hint": "Custom"}})
        self.assertEqual(config["tts"]["voice_hint"], "Custom")
        self.assertIn("rate", config["tts"])

    def test_invalid_confidence_is_rejected(self):
        config = merge_config({"stt": {"command_confidence_threshold": 1.5}})
        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_unknown_provider_is_rejected(self):
        config = merge_config({"llm": {"order": ["gemini", "unknown"]}})
        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_chat_confidence_defaults_and_validation(self):
        self.assertEqual(merge_config({})["stt"]["chat_confidence_threshold"], 0.5)
        for value in [-0.1, 1.5, float("nan")]:
            with self.subTest(value=value), self.assertRaises(ConfigError):
                validate_config(merge_config({"stt": {"chat_confidence_threshold": value}}))

    def test_fast_profile_uses_greedy_whisper_decode(self):
        config = apply_performance_profile(merge_config({}), "fast")
        self.assertEqual(config["stt"]["whisper"]["beam_size"], 1)
        self.assertTrue(config["stt"]["whisper"]["enabled"])
        self.assertEqual(config["stt"]["whisper"]["model"], "base")
        self.assertEqual(config["stt"]["whisper"]["prompt"], "")
        self.assertEqual(config["stt"]["whisper"]["hotwords"], "")

    def test_cpu_thread_tuning_keeps_explicit_choice_and_rejects_negative(self):
        for threads in (0, 1, 2, 4):
            with self.subTest(threads=threads):
                config = apply_performance_profile(merge_config({"stt": {"whisper": {"cpu_threads": threads}}}), "fast")
                self.assertEqual(config["stt"]["whisper"]["cpu_threads"], threads)
                validate_config(config)
        with self.assertRaises(ConfigError):
            validate_config(merge_config({"stt": {"whisper": {"cpu_threads": -1}}}))

    def test_raspberry_profile_stays_within_low_memory_budget(self):
        config = apply_performance_profile(merge_config({}), "raspberry_pi")
        self.assertFalse(config["stt"]["whisper"]["enabled"])
        self.assertFalse(config["stt"]["whisper"]["preload"])
        self.assertLessEqual(config["security"]["pentest"]["concurrency"], 20)
        self.assertLessEqual(config["security"]["pentest"]["max_hosts"], 64)

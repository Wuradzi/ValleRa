from __future__ import annotations

import asyncio

from core.atomic_json import AtomicJSONFile
from core.models import SkillResult
from services.audio.calibration import calibrate_microphone


def can_handle(command, services):
    return command in {
        "відкалібруй мікрофон",
        "калібруй мікрофон",
        "перевір мікрофон",
    }


async def handle(command, context, services):
    settings = context.settings
    if not await context.confirm("зміну налаштувань мікрофона у config.json"):
        return SkillResult(True, "Калібрування скасовано.")
    threshold = await asyncio.to_thread(
        calibrate_microphone,
        settings.input_device,
        settings.stt_sample_rate,
    )
    config_path = settings.paths.config_file
    config_file = AtomicJSONFile(config_path, {})
    config = config_file.load()
    config["stt"]["noise_threshold"] = threshold
    config_file.save(config)
    settings.noise_threshold = threshold
    return SkillResult(
        True,
        "Мікрофон відкалібровано.",
        {"command_type": "microphone_calibration"},
    )

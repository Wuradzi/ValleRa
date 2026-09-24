"""The only executor for model-proposed actions: validate, preview, confirm."""
from __future__ import annotations

from core.command_intent import InvalidIntent, validate_intent
from core.models import SkillResult
from core.command_catalog import TOOL_SKILLS, command_help
from core.confirmation import confirm_action


async def execute_intent(intent, context):
    # Treat even an adapter's CommandIntent object as untrusted at this boundary.
    try:
        intent = validate_intent({"tool": intent.tool, "arguments": intent.arguments})
    except (InvalidIntent, AttributeError):
        return SkillResult(True, "Не вдалося надійно розібрати команду. Дію не виконано.",
                           {"command_type": "interpretation_invalid"})
    tool, args = intent.tool, intent.arguments
    if tool not in TOOL_SKILLS:
        return SkillResult(True, "Не визначив підтримувану дію. Уточніть запит. Доступні команди: " + command_help(context.services.get("enabled_skills", set())) + ".",
                           {"command_type": "interpretation_unsupported"})
    if TOOL_SKILLS[tool] not in context.services.get("enabled_skills", set()):
        return SkillResult(True, "Потрібну навичку вимкнено або вона недоступна на цій платформі.",
                           {"command_type": "interpretation_unavailable"})
    if tool == "prepare_workplace":
        # The resolved local plan provides the single confirmation, not the model.
        return await context.services["workplace"].run("режим робота", context)
    if tool in {"workplace_status", "workplace_retry"}:
        command = "статус завдання" if tool == "workplace_status" else "повтори невдалий крок"
        return await context.services["workplace"].followup(command, context)
    if tool == "window_control":
        from skills.windows.skill import control_window
        return await control_window(args["name"], args["action"], context)
    if tool == "find_files":
        proposal = f"пошук у дозволених каталогах файлів із назвою, що містить «{args['query']}» (тип: {args['extension'] or 'будь-який'})"
        question = f"Знайти файли «{args['query']}», тип {args['extension'] or 'будь-який'}"
    elif tool == "open_app":
        proposal = f"відкриття встановленої програми «{args['name']}»"
        question = f"Відкрити «{args['name']}»"
    elif tool == "weather":
        period = {"now": "зараз", "today": "сьогодні", "tomorrow": "завтра"}[args["period"]]
        proposal = f"запит погоди для міста «{args['city']}», {period}"
        question = f"Перевірити погоду: {args['city']}, {period}"
    else:
        proposal = f"пошук в інтернеті за запитом «{args['query']}»"
        question = f"Знайти в інтернеті «{args['query']}»"
    cancelled = await confirm_action(context, proposal, question)
    if cancelled is not None:
        return cancelled
    # No shell, eval, dynamic code, arbitrary URLs/paths, or router re-entry.
    if tool == "find_files":
        from skills.files.skill import find_files
        return await find_files(args["query"], args["extension"], context)
    if tool == "open_app":
        from skills.apps.skill import open_application
        return await open_application(args["name"], context, context.services)
    if tool == "weather":
        from skills.web.skill import get_weather
        return await get_weather(args["city"], args["period"], context.services)
    from skills.web.skill import search_web
    return await search_web(args["query"], context.services)

from core.models import SkillResult


COMMANDS = {
    "проведи самодіагностику",
    "запусти самодіагностику",
    "перевір системи валери",
}


def can_handle(command, services):
    return command in COMMANDS


async def handle(command, context, services):
    summary, report = await services["diagnostics"].run()
    return SkillResult(
        True,
        summary,
        {
            "command_type": "diagnostics",
            "diagnostics_ok": report["ok"],
        },
    )

from __future__ import annotations

from core.models import SkillResult


def can_handle(command, services):
    return command in {
        "звіт про роботу",
        "покажи метрики",
        "покажи статистику",
    }


async def handle(command, context, services):
    report = services["metrics"].report()
    response = (
        f"Зафіксовано {report['events']} подій і {report['commands']} команд. "
        f"Успішність {report['success_rate']} відсотка. "
        f"Середній час реакції {report['average_duration_ms']} мілісекунд."
    )
    return SkillResult(True, response, {"command_type": "metrics_report"})

from core.models import SkillResult
from core.command_catalog import command_help


COMMANDS = {
    "список команд",
    "покажи команди",
    "що ти вмієш",
}


def can_handle(command, services):
    return command in COMMANDS


async def handle(command, context, services):
    if command not in COMMANDS:
        return SkillResult(False)
    extra = ""
    if getattr(getattr(context, "settings", None), "command_interpretation_enabled", False):
        extra = (
            " Для явного формату зі словом Команда: "
            "я спробую розібрати підтримуваний намір із каталогу "
            "і попрошу підтвердити трактування. Після списку файлів можна відповісти: другий або скасувати."
        )
    return SkillResult(
        True,
        "Основні команди: " + command_help(services.get("enabled_skills")) + ". "
        + ("Можна просити без слова Команда: LLM запропонує підтримувану дію для підтвердження. Інші точні команди — зі словом Команда. "
           if getattr(getattr(context, "settings", None), "natural_actions_enabled", False) else
           "Перед локальною дією скажіть слово «Команда». ") +
        "Для зупинки кроків робочого сценарію введіть: Команда, скасуй завдання. "
        "Для керування розмовою без цього слова: зачекай, продовжуй, повтори останнє, "
        "коротше, поясни простіше. Пауза розмови не вимикає мікрофон." + extra,
        {"command_type": "help"},
    )

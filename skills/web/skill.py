from __future__ import annotations

import logging
import asyncio
import re

from core.models import SkillResult
from services.web.intents import search_query, weather_request
from services.web.search import WebSearchService, search_failure_reason
from services.web.answers import WebAnswerService

logger = logging.getLogger(__name__)
FOLLOWUP = re.compile(r"^уточни\s+пошук(?:\s*[:,-]?\s+|$)(.*)", re.I)


def can_handle(command, services):
    if FOLLOWUP.fullmatch(command):
        return True
    if re.search(r"\b(?:пароль від|секрет|api[- ]?ключ)\b|[a-z]:[\\/]", command, re.I):
        return False
    if re.match(
        r"^(?:знайди|знайти|пошукай)\s+.*\b(?:файл|pdf|документ)", command, re.I
    ) and not re.search(r"\b(?:інтернеті|браузері|google|гуглі)\b", command, re.I):
        return False
    return weather_request(command) is not None or bool(
        re.match(
            r"^(?:пошук в інтернеті|пошукай|знайди|знайти|що таке|хто такий)(?:\s|$)",
            command,
        )
    )


async def handle(command, context, services):
    if not can_handle(command, services):
        return SkillResult(False)
    raw_command = (getattr(context, "raw_text", "") or command).rstrip("?.!,; ")
    followup = FOLLOWUP.fullmatch(raw_command)
    if followup:
        answer_service = services.get("web_answers")
        if answer_service is None:
            return SkillResult(True, "Контекст пошуку недоступний. Повторіть повну команду пошуку.")
        return await answer_service.followup(followup[1].strip())
    request = weather_request(raw_command)
    if request is not None:
        if request.period == "unsupported":
            return SkillResult(
                True,
                "Можу надати погоду зараз або прогноз на сьогодні чи завтра. Уточніть період.",
                {"command_type": "weather"},
            )
        if not request.city:
            return SkillResult(
                True,
                "Вкажіть місто, наприклад: Команда, погода в місті Луцьк.",
                {"command_type": "weather_missing_city"},
            )
        return await get_weather(request.city, request.period, services)

    query = search_query(raw_command)
    if not query:
        return SkillResult(
            True,
            "Скажіть в одній команді, що саме знайти в інтернеті.",
            {"command_type": "web_search_missing_query"},
        )
    return await search_web(query, services)


async def get_weather(city, period, services):
    try:
        data = await services["weather"].get(city)
        answer = services["weather"].summarize(data, city, period)
    except Exception:
        logger.exception("Weather request failed")
        return SkillResult(True, "Сервіс погоди зараз не відповідає або не знайшов місто. Прогноз не отримано; спробуйте пізніше.",
                           {"command_type": "weather_error"})
    return SkillResult(True, answer, {"command_type": "weather", "city": city})


async def search_web(query, services):
    answer_service = services.get("web_answers")
    if answer_service is not None:
        answer_service.clear()
    if not WebAnswerService.safe_query(query):
        return SkillResult(True, "Уточніть короткий пошуковий запит без секретів або локальних шляхів.",
                           {"command_type": "web_search_rejected"})
    try:
        rows = await asyncio.wait_for(services["web"].search(query), timeout=12)
        results = WebSearchService.clean_results(rows, query=query, limit=5)
    except Exception as exc:
        reason = search_failure_reason(exc)
        logger.warning("Web search failed kind=%s", reason)
        message = {
            "no_results": "Пошуковий сервіс не повернув результатів. Спробуйте коротше сформулювати запит.",
            "timeout": "Пошук не встиг відповісти. Спробуйте пізніше; перезапускати асистента не потрібно.",
            "busy": "Попередній пошуковий запит ще виконується. Дочекайтеся його завершення.",
        }.get(reason, "Не вдалося отримати результати від пошукового сервісу. Спробуйте пізніше.")
        result = SkillResult(
            True,
            message,
            {"command_type": "web_search_error", "search_failure": reason},
        )
        return answer_service.remember_lookup(query, result) if answer_service is not None else result
    # Explicit resource lookup needs links, not an extra generation request.
    resources_only = re.search(r"\b(?:офіційний сайт|посилання на|адресу сайту)\b", query, re.I)
    if answer_service is not None and results and not resources_only:
        return await answer_service.answer(query, results, search=services["web"])
    result = SkillResult(
        True,
        WebSearchService.summarize(results),
        {
            "command_type": "web_search",
            "web_results": results,
            "query": query,
            "resources_only": bool(resources_only),
        },
    )
    return answer_service.remember_lookup(query, result) if answer_service is not None else result

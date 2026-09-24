"""Deterministic web intents; no LLM and no permission-prefix repair."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class WeatherRequest:
    city: str
    period: str = "now"


def weather_request(text: str) -> WeatherRequest | None:
    text = " ".join(text.strip().split())
    if not re.search(r"\bпогод(?:а|у|и|ою)\b", text, re.I):
        return None
    if not re.match(
        r"^(?:яка|покажи|скажи|дізнайся|знайди|пошукай|прогноз|погода|погоду)\b", text, re.I
    ):
        return None
    # Observed ASR errors, not a general fuzzy geocoder.
    text = re.sub(r"\bвлучк(?:у|а)\b", "в Луцьку", text, flags=re.I)
    period = "tomorrow" if re.search(r"\bзавтра\b", text, re.I) else "now"
    if re.search(r"\bсьогодні\b", text, re.I) or "прогноз" in text.casefold():
        period = "today" if period == "now" else period
    if re.search(r"\b(?:тиждень|тижні|післязавтра|місяць|днів)\b", text, re.I):
        period = "unsupported"
    tail = re.split(r"\bпогод(?:а|у|и|ою)\b", text, maxsplit=1, flags=re.I)[1]
    tail = re.sub(r"\b(?:на\s+)?(?:сьогодні|завтра|зараз)\b", "", tail, flags=re.I)
    match = re.search(r"\b(?:у|в)\s+(?:(?:місті|місто|м\.)\s*)?(.+?)\s*[?.!]*$", tail, re.I)
    city = match.group(1).strip(" ,.!?") if match else ""
    if re.fullmatch(r"(?:луцьк(?:у|а)?|лучк(?:у|а)?)", city, re.I):
        city = "Луцьк"
    return WeatherRequest(city, period)


def search_query(text: str) -> str:
    query = re.sub(r"^(?:пошук\s+в\s+інтернеті|пошукай|знайди|знайти)\b[\s,:-]*", "", text, flags=re.I)
    location = r"(?:в|у)\s+(?:інтернеті|браузері|гуглі|google)"
    query = re.sub(rf"^(?:і\s+)?{location}[\s,:;-]*", "", query, flags=re.I)
    query = re.sub(rf"\s+{location}$", "", query, flags=re.I)
    return query.strip()


def query_terms(text: str) -> set[str]:
    """Topic-independent retrieval terms, not an interpretation of user intent."""
    stop = {"знайди", "знайти", "пошукай", "інтернеті", "браузері", "команда", "будь", "ласка",
            "мені", "таке", "такий", "такі", "розкажи", "поясни", "детально", "докладно",
            "подробицях", "часи", "please", "search", "what", "about", "explain"}
    return {word[:6] for word in re.findall(r"\w+", text.casefold())
            if len(word) >= 3 and word not in stop}

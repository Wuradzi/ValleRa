from __future__ import annotations

import httpx
from urllib.parse import quote


class WeatherService:
    async def get(self, city: str) -> dict:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            response = await client.get(
                f"https://wttr.in/{quote(city, safe='')}",
                params={"format": "j1", "lang": "uk"},
                headers={"User-Agent": "ValleRa/0.1"},
            )
            response.raise_for_status()
            return response.json()

    @staticmethod
    def summarize(data: dict, city: str, period: str = "now") -> str:
        if period != "now":
            index = 1 if period == "tomorrow" else 0
            days = data.get("weather", [])
            if len(days) <= index:
                raise ValueError("Прогноз на потрібний день відсутній")
            day = days[index]
            label = "завтра" if index else "сьогодні"
            return (
                f"Прогноз для міста {city} на {label}, {day['date']}: "
                f"від {day['mintempC']} до {day['maxtempC']} градусів. "
                "Джерело: сервіс wttr.in."
            )
        current = data["current_condition"][0]
        description_list = current.get("lang_uk") or current.get("weatherDesc") or [{"value": ""}]
        return (
            f"Погода для міста {city}: зараз {current['temp_C']} градусів, "
            f"відчувається як {current.get('FeelsLikeC', '?')}. "
            f"{description_list[0]['value']}. "
            f"Вологість {current.get('humidity', '?')} відсотків. "
            "Джерело: сервіс wttr.in."
        )

from __future__ import annotations

import re


_REPLACEMENTS = (
    (
        r"\b(?:відкритого|відкриті|відкрити|відкрив|відкрила|відкриє|"
        r"відкрей|відкриють)\b",
        "відкрий",
    ),
    (r"\bпід\s+край\b", "відкрий"),
    (r"\b(?:кілограм|голограм)\b", "телеграм"),
    (r"\bдушу\s+(?:гук|губ)\b", "душогуб"),
    (r"\bпан\s+тест(?:у|ом)?\b", "пентесту"),
    (r"\b(?:замовкли|замовкне)\b", "замовкни"),
    (r"\b(?:знайде|знайдеє)\b", "знайди"),
    (r"\bзавершила\s+роботу\b", "заверши роботу"),
    (r"\bвийде\s+система\b", "заверши роботу"),
)


def repair_voice_text(text: str, mode: str = "chat") -> str:
    repaired = " ".join(text.strip().split())
    has_command_boundary = bool(
        re.match(r"^команда\b", repaired, flags=re.IGNORECASE)
    )
    if mode != "pentest" and not has_command_boundary:
        # Corrections must never manufacture permission for a local action or
        # rewrite an ordinary conversational message.
        return repaired

    # Repair only the search verb/location, never names or modifiers in its payload.
    repaired = re.sub(
        r"^(команда[\s,:;-]+)знайдив\s+(?=інтернеті\b)",
        r"\1знайди в ", repaired, flags=re.I,
    )
    repaired = re.sub(
        r"^(команда[\s,:;-]+)з\s+найди(?:в)?\s+інтернетіа?\b[\s,:;-]*",
        r"\1знайди в інтернеті ", repaired, flags=re.I,
    )
    repaired = re.sub(r"^(команда[\s,:;-]+)(?:знайти|знайде|знайдеє|знайдє)\b",
                      r"\1знайди", repaired, flags=re.I)
    if re.match(r"^команда[\s,:;-]+(?:знайди|пошукай)\b", repaired, re.I):
        return re.sub(
            r"^(команда[\s,:;-]+знайди\s+)походу(?=\s+(?:в|у)\s)",
            r"\1погоду", repaired, flags=re.I,
        )

    for pattern, replacement in _REPLACEMENTS:
        repaired = re.sub(pattern, replacement, repaired, flags=re.IGNORECASE)

    repaired = re.sub(
        r"^(команда[\s,:-]+знайди\s+)походу(?=\s+(?:в|у)\s)",
        r"\1погоду", repaired, flags=re.IGNORECASE,
    )

    # Whisper occasionally transcribes Ukrainian "відкрий" as the English-like
    # pair "від Kray". Repair it only after an explicit command prefix; this
    # cannot create permission for a local action from ordinary conversation.
    repaired = re.sub(
        r"^(команда[\s,:;-]+)від\s+(?:kray|край|крей)[\s,;:-]+"
        r"(?=(?:браузер|хром|телеграм)\b)",
        r"\1відкрий ",
        repaired,
        flags=re.IGNORECASE,
    )

    repaired = re.sub(
        r"^(команда\s+)?від\s+(?=(?:хром|телеграм|браузер)\b)",
        lambda match: f"{match.group(1) or ''}відкрий ",
        repaired,
        flags=re.IGNORECASE,
    )
    repaired = re.sub(
        r"\bвідкрий\s+грам\b",
        "відкрий телеграм",
        repaired,
        flags=re.IGNORECASE,
    )
    if mode == "pentest" and repaired.casefold() == "старова":
        repaired = "статус душогуба"
    return " ".join(repaired.split())

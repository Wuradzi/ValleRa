# Додавання навички

Створіть:

```text
skills/example/
├── manifest.json
└── skill.py
```

`manifest.json`:

```json
{
  "name": "example",
  "description": "Приклад",
  "triggers": ["приклад команди"],
  "platforms": ["windows"],
  "enabled": true
}
```

`skill.py`:

```python
from core.models import SkillResult


def can_handle(command, services):
    return "приклад команди" in command


async def handle(command, context, services):
    return SkillResult(
        handled=True,
        response="Команду виконано.",
        data={"command_type": "example"}
    )
```

Небезпечна дія:

```python
approved = await context.confirm("опис операції")
if not approved:
    return SkillResult(handled=True, response="")
```

Редагувати `processor.py` не потрібно.

Для публічної підказки нової навички додайте `Capability` в
`core/command_catalog.py`: фактичний приклад команди та ім’я навички.
`arguments=None` означає тільки опис/довідку, без LLM-виконання. Каталог
живить довідку й контекст моделі з фільтрацією за завантаженими навичками.
Новий LLM-інструмент потребує також локальної валідації в command_intent.py,
явного виконавця в command_actions.py, перевірки дозволів, підтвердження
та регресій через tester.py; самого запису в каталозі недостатньо.

Для довгого плану можна передати `ConfirmationPrompt(details, spoken)` з
core.confirmation: details містить повний погоджуваний обсяг для UI/журналу,
spoken — короткий, але правдивий опис того самого обсягу. Не опускайте в ньому
небезпечні наслідки. Звичайні рядкові підтвердження працюють як раніше.

"""Read-only common progress contract; cancellation remains executor-owned."""
from dataclasses import asdict, dataclass
from typing import Callable


@dataclass(frozen=True)
class TaskProgress:
    id: str
    kind: str
    title: str
    status: str
    detail: str
    active: bool
    cancel_requested: bool
    cancellation: str
    started_at: float
    steps: tuple[dict, ...] = ()

    def public(self):
        return asdict(self)


class TaskProgressRegistry:
    """One adapter per executor, no copied history, queues, permissions or tasks."""
    def __init__(self):
        self._providers: dict[str, tuple[Callable, Callable]] = {}

    def register(self, kind, snapshot, cancel):
        if kind in self._providers:
            raise ValueError('Duplicate task provider')
        self._providers[kind] = (snapshot, cancel)

    def current(self):
        items = [snapshot() for snapshot, _ in self._providers.values()]
        items = [item for item in items if item is not None]
        return max(items, key=lambda item: (item.active, item.started_at), default=None)

    def cancel(self, kind, task_id):
        provider = self._providers.get(kind) if isinstance(kind, str) else None
        if provider is None or not isinstance(task_id, str):
            return False
        current = provider[0]()
        return bool(current and current.active and current.id == task_id and provider[1](task_id))


def workplace_progress(service):
    raw = service.snapshot()
    if raw is None:
        return None
    status = {'saved': 'completed', 'interrupted': 'cancelled'}.get(raw['status'], raw['status'])
    if raw['active'] and raw['cancel_requested']:
        status = 'cancelling'
    return TaskProgress(raw['id'], 'workplace', raw['title'], status, raw['detail'],
                        raw['active'], raw['cancel_requested'],
                        'Зупиняться наступні кроки. Уже відкриті програми не закриються.',
                        raw.get('started_at', 0), tuple(raw['steps']))


def file_search_progress(service):
    raw = service.search_snapshot()
    if raw is None:
        return None
    status = {'searching': 'running'}.get(raw['stage'], raw['stage'])
    detail = f"Переглянуто файлів у цій порції: {raw['checked']}. Збігів: {raw['matches']}."
    if status == 'partial':
        detail += ' Повторення запиту продовжить обхід.'
    if status == 'cancelled':
        detail += ' Результати цієї порції відкинуто.'
    step_status = 'verified' if status == 'completed' else status
    return TaskProgress(raw['id'], 'file_search', 'Пошук файлів', status, detail,
                        raw['active'], raw['cancel_requested'],
                        'Обхід зупиниться після поточного системного виклику; результати буде відкинуто.',
                        raw.get('started_at', 0),
                        ({'name': 'Обхід доступних каталогів', 'status': step_status, 'detail': detail},))

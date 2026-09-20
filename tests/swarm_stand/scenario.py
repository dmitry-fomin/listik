"""Модель сценария стенда роя: поддельные задачи и их write_scope."""

from __future__ import annotations

from dataclasses import dataclass, field

PROFILES = ("append", "prepend", "rewrite", "scope_break", "crash", "hang", "empty", "red_tests")
EXPENSIVE_ROUTES = frozenset({"xhigh-pipeline"})
DEFAULT_ROUTE = "nano-pipeline"


@dataclass
class Task:
    id: str
    profile: str
    write_scope: list[str]
    deps: list[str] = field(default_factory=list)
    launch_route: str | None = DEFAULT_ROUTE
    foreign: str | None = None
    tree_key: str | None = None


@dataclass
class Scenario:
    tasks: list[Task]
    files: dict[str, str] | None = None
    test_command: list[str] | None = None


def task_by_id(scenario: Scenario, task_id: str) -> Task:
    for task in scenario.tasks:
        if task.id == task_id:
            return task
    raise ValueError(f"неизвестный id задачи: {task_id!r}")


def target_of(task: Task) -> str:
    for entry in task.write_scope:
        if not entry.endswith("/"):
            return entry
    raise ValueError(f"в write_scope задачи {task.id!r} нет записи-файла")


def scope_covers(entry: str, path: str) -> bool:
    if path == entry:
        return True
    return entry.endswith("/") and path.startswith(entry)


def scopes_intersect(a: list[str], b: list[str]) -> bool:
    for x in a:
        for y in b:
            if scope_covers(x, y) or scope_covers(y, x):
                return True
    return False


def _is_safe_relpath(path: str) -> bool:
    if path.startswith("/"):
        return False
    parts = path.split("/")
    return ".." not in parts


def validate(scenario: Scenario) -> None:
    # локальный импорт: избегаем цикла sandbox<->scenario на уровне модуля
    from .sandbox import DEFAULT_FILES

    files = scenario.files if scenario.files is not None else DEFAULT_FILES

    seen_ids: set[str] = set()
    for task in scenario.tasks:
        if task.profile not in PROFILES:
            raise ValueError(f"неизвестный профиль {task.profile!r} у задачи {task.id!r}")
        if task.id in seen_ids:
            raise ValueError(f"повторяющийся id задачи: {task.id!r}")
        seen_ids.add(task.id)

    known_ids = seen_ids
    for task in scenario.tasks:
        for dep in task.deps:
            if dep == task.id:
                raise ValueError(f"задача {task.id!r} зависит сама от себя")
            if dep not in known_ids:
                raise ValueError(f"задача {task.id!r} ссылается на неизвестный dep {dep!r}")

    for task in scenario.tasks:
        if not task.write_scope:
            raise ValueError(f"write_scope задачи {task.id!r} пуст")

        for path in list(task.write_scope) + ([task.foreign] if task.foreign else []):
            if not _is_safe_relpath(path):
                raise ValueError(f"небезопасный путь {path!r} в задаче {task.id!r}")

        if task.profile == "scope_break":
            if not task.foreign:
                raise ValueError(f"у scope_break-задачи {task.id!r} нет foreign")
            if scopes_intersect(task.write_scope, [task.foreign]):
                raise ValueError(
                    f"foreign {task.foreign!r} задачи {task.id!r} покрывается write_scope"
                )

        if task.profile != "empty":
            target = target_of(task)
            if target not in files:
                raise ValueError(
                    f"target_of({task.id!r}) == {target!r} не ключ files"
                )

"""Ядро трекера: создание, правка, переходы этапов, доска, статистика.

Вся запись идёт через эти функции. Если Listik запущен сервером — их вызывает
HTTP-слой; если нет, CLI работает с базой напрямую (WAL выдержит).
"""
from __future__ import annotations

import json
import os
import random
import re
import sqlite3
import string
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import actors as actors_mod
from . import config as config_mod
from . import deps as deps_mod
from . import errors as errors_mod
from . import paths
from . import routes as routes_mod
from . import scope as scope_mod
from . import store_helpers
from . import textutil

OPEN_STATUSES = ("open", "in_progress", "blocked", "review")
FINAL_STATUSES = ("done", "cancelled")
PIPELINE_STAGES = ("s1-spec", "s2-review", "s3-impl", "s4-judge")
#: Сколько часов предложенная связь (`suggested-blocks`) ждёт без замечания `listik lint`.
LINT_SUGGESTED_HOURS = 24.0
STAGE_TITLES = {
    "s1-spec": "1. ТЗ и чек-лист",
    "s2-review": "2. Второе мнение",
    "s3-impl": "3. Реализация",
    "s4-judge": "4. Проверка и коммит",
    "done": "готово",
}
STATUS_TITLES = {
    "open": "открыта",
    "in_progress": "в работе",
    "blocked": "заблокирована",
    "review": "на проверке",
    "done": "готова",
    "cancelled": "отменена",
}
PRIORITY_TITLES = {0: "P0 срочно", 1: "P1 высокий", 2: "P2 обычный", 3: "P3 низкий", 4: "P4 потом"}

# `worktree` хранит либо путь к отдельному рабочему дереву, либо маркер основной
# ветки (`main`/`master`): он значит «работа идёт в основной ветке репозитория
# проекта, отдельного дерева нет» (`listik set <id> worktree=main`, см. docs/API.md).
# Маркер остаётся ключом блокировки дерева — две задачи в основной ветке одного
# проекта не пишут в неё одновременно, как и в общем дереве (см. `worktree_lock_key`).
MAIN_WORKTREE_MARKERS = ("main", "master")

# Ключ «основное дерево проекта» для блокировки, когда у проекта не указан `path`
# (тогда каталога нет, но дерево всё равно одно на проект). Начинается с NUL,
# чтобы не столкнуться ни с одним путём файловой системы.
MAIN_TREE_LOCK_KEY = "\x00main-tree"

_SUFFIX_ALPHABET = string.ascii_lowercase + string.digits

#: Отказ, когда в серверном режиме не представились там, где владелец обязателен.
OWNER_REQUIRED_CREATE = ("серверный режим: укажи владельца задачи "
                         "(--owner, LISTIK_OWNER или [auth] owner)")
OWNER_REQUIRED_TAKE = ("серверный режим: укажи, от чьего имени берёшь задачу "
                       "(--owner, LISTIK_OWNER или [auth] owner)")


def server_cfg() -> dict | None:
    """Конфиг, если хаб в серверном режиме; `None` — локальный режим.

    Читается в момент вызова, а не на импорте: тесты и `listik --local` подменяют
    `paths.CONFIG_PATH`, а сервер перечитывает config.toml на лету. В локальном
    режиме владелец (`owner`/`as_owner`) игнорируется полностью — ни записи, ни
    фильтра, ни отказов, — поэтому все вызывающие проверяют этот `None` первым.
    """
    cfg = config_mod.load()
    return cfg if config_mod.is_server_mode(cfg) else None


def check_task_owner(row, as_owner: str | None, *, task_id: str) -> None:
    """Серверный режим: представился ли берущий и не чужая ли это задача.

    Зовётся сразу после чтения строки задачи и до любой другой проверки и любой
    записи: отказ «чужая задача» должен звучать про владельца, а не про держателя
    или блокеры, и не оставлять следов (события, updated_at). Задачу без
    владельца берёт любой, владельцем она при этом не обзаводится.
    """
    cfg = server_cfg()
    if cfg is None:
        return
    value = config_mod.check_owner(as_owner, cfg)
    if value is None:
        raise errors_mod.BadArgument(OWNER_REQUIRED_TAKE)
    row_owner = (row["owner"] or "").strip() if row is not None else ""
    if row_owner and row_owner != value:
        raise errors_mod.Forbidden(
            f"задача {task_id} принадлежит {row_owner}: чужую задачу брать нельзя")


def main_worktree(value: str | None) -> str:
    """Канонический маркер основной ветки (`main`/`master`) или `''`.

    Регистр не важен: `worktree=MAIN` — тот же маркер, в карточке он хранится
    строчными (см. `update_task`). Пустая строка — значение не маркер.
    """
    marker = (value or "").strip().lower()
    return marker if marker in MAIN_WORKTREE_MARKERS else ""


def is_main_worktree(value: str | None) -> bool:
    """True, если `worktree` — маркер «работа в основной ветке, без дерева»."""
    return bool(main_worktree(value))


def main_worktree_title(value: str | None) -> str:
    """Подпись маркера для CLI и доски: «работа в main» (или `master`)."""
    marker = main_worktree(value)
    return f"работа в {marker}" if marker else ""


def _real_path(value: str) -> str:
    """Канонический путь для ключа блокировки: `~`, симлинки, хвостовой `/`.

    Каталога может и не быть (дерево ещё не создано) — `resolve` в нестрогом
    режиме это переживает; на битый симлинк откатываемся на путь как дали.
    """
    path = Path(value).expanduser()
    try:
        return str(path.resolve())
    except (OSError, RuntimeError):
        return str(path)


def worktree_lock_key(worktree: str | None, project_path: str | None = "") -> str:
    """Канонический ключ дерева записи: одно дерево — один ключ.

    Ключ блокировки — не сырое значение `worktree`, а дерево, в которое задача
    реально пишет:

    * пустой `worktree` и маркеры основной ветки `main`/`master` — это «основное
      дерево проекта», каталог `projects.path` (туда задачу запускает
      `launcher._workdir`). Все три записи дают один ключ, поэтому две пишущие
      задачи «в main» и «без дерева» одного проекта конфликтуют;
    * явный путь приводится к каноническому (`_real_path`) — `/repo`, `/repo/` и
      путь через симлинк считаются одним деревом, а путь, равный каталогу
      проекта, — тем же ключом, что и маркер;
    * если у проекта нет `path`, «основное дерево» остаётся отдельным ключом
      `MAIN_TREE_LOCK_KEY` (один на проект) — как и раньше, когда пустые
      `worktree` конфликтовали между собой.

    Блокировка применяется только к пишущим задачам — это проверяет вызывающий
    (`deps.worktree_conflict`); `s1-spec`/`s2-review` дерево не занимают.
    """
    value = (worktree or "").strip()
    if value and not is_main_worktree(value):
        return _real_path(value)
    path = (project_path or "").strip()
    return _real_path(path) if path else MAIN_TREE_LOCK_KEY


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def hours_since(value: str | None) -> float | None:
    ts = parse_ts(value)
    if not ts:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0


def human_age(value: str | None) -> str:
    h = hours_since(value)
    if h is None:
        return "—"
    if h < 1:
        return f"{int(h * 60)} мин"
    if h < 48:
        return f"{int(h)} ч"
    return f"{int(h / 24)} дн"


# ------------------------------------------------------------------ задачи

def gen_id(conn: sqlite3.Connection, project: str | None, *, prefix: str | None = None) -> str:
    base = (prefix or project or "lk").strip()
    base = re.sub(r"[^0-9a-zA-Zа-яА-Я_\-]+", "-", base).strip("-").lower() or "lk"
    for _ in range(50):
        cand = f"{base}-{''.join(random.choice(_SUFFIX_ALPHABET) for _ in range(4))}"
        if not store_helpers.task_exists(conn, cand):
            return cand
    return f"{base}-{int(datetime.now().timestamp())}"


def event(conn: sqlite3.Connection, task_id: str, kind: str, *, from_value=None, to_value=None,
          actor: str | None = None, harness: str | None = None, note: str | None = None,
          duration_s: int | None = None, ts: str | None = None) -> None:
    conn.execute(
        "INSERT INTO events(task_id, ts, kind, from_value, to_value, actor, harness, note, duration_s) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (task_id, ts or now_iso(), kind, from_value, to_value, actor, harness, note, duration_s),
    )


def _index_task(conn: sqlite3.Connection, task_id: str) -> None:
    row = store_helpers.task_row(conn, task_id, required=False)
    if not row:
        return
    body = "\n\n".join(
        x for x in (
            textutil.clean_markdown(row["description"]),
            textutil.clean_markdown(row["acceptance"]),
            textutil.clean_markdown(row["design"]),
            textutil.clean_markdown(row["notes"]),
            textutil.clean_markdown(row["result"]),
            # Imported records must remain discoverable by their legacy ID.
            textutil.clean_markdown(row["external_ref"]),
        ) if x
    )
    conn.execute("DELETE FROM task_fts WHERE task_id = ?", (task_id,))
    conn.execute(
        "INSERT INTO task_fts(task_id, title, body, labels) VALUES(?,?,?,?)",
        (task_id, row["title"], body, row["labels"] or ""),
    )
    h = textutil.text_hash(row["title"], body, row["labels"] or "")
    conn.execute("UPDATE tasks SET content_hash = ?, indexed_at = ? WHERE id = ?", (h, now_iso(), task_id))


def _index_comment(conn: sqlite3.Connection, comment_id: str) -> None:
    row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
    if not row:
        return
    clean = textutil.clean_markdown(row["text"])
    conn.execute("DELETE FROM comment_fts WHERE comment_id = ?", (comment_id,))
    if clean:
        conn.execute(
            "INSERT INTO comment_fts(comment_id, task_id, body) VALUES(?,?,?)",
            (comment_id, row["task_id"], clean),
        )


def labels_with_route(conn: sqlite3.Connection, labels: list[str] | None,
                      route_key: str | None) -> list[str]:
    """Метки карточки: явные плюс метки маршрута, без дублей (явные идут первыми).

    Одно правило для всех, кто создаёт задачу: CLI (`new --route`), `POST /api/tasks`
    с `route` и MCP `listik_create`. Метки маршрута считает `routes.labels_for`;
    заданную вручную метку (`--label harness:claude`) второй раз не добавляем.
    """
    out = [str(label) for label in (labels or [])]
    for label in routes_mod.labels_for(conn, route_key):
        if label not in out:
            out.append(label)
    return out


def labels_after_route_change(conn: sqlite3.Connection, labels: list[str],
                              route_key: str | None) -> list[str] | None:
    """Метки карточки при смене маршрута; `None` — оставить как есть.

    Старые метки маршрута (`harness:`/`process:`) заменяются метками нового, чужие
    метки задачи остаются. Не трогаем их, когда у нового непустого ключа меток нет:
    маршрута нет в таблице — стирать чужие данные нельзя. Снятие маршрута (пустой
    ключ) метки маршрута убирает.
    """
    fresh = routes_mod.labels_for(conn, route_key)
    if not fresh and (route_key or "").strip():
        return None
    keep = [str(label) for label in labels if not routes_mod.is_route_label(str(label))]
    return keep + [label for label in fresh if label not in keep]


def route_labels_from_row(row: sqlite3.Row) -> list[str]:
    """Метки карточки из строки таблицы (в колонке — JSON-массив)."""
    return [str(label) for label in store_helpers.json_list(row["labels"])]


def create_task(
    conn: sqlite3.Connection,
    *,
    title: str,
    project: str | None = None,
    description: str = "",
    acceptance: str = "",
    design: str = "",
    notes: str = "",
    result: str = "",
    issue_type: str = "task",
    status: str = "open",
    priority: int = 2,
    assignee: str | None = None,
    owner: str | None = None,
    as_owner: str | None = None,
    stage: str | None = None,
    labels: list[str] | None = None,
    spec_path: str | None = None,
    checklist_path: str | None = None,
    review_path: str | None = None,
    decision_path: str | None = None,
    journal_path: str | None = None,
    external_ref: str | None = None,
    source: str = "native",
    task_id: str | None = None,
    created_by: str | None = None,
    created_at: str | None = None,
    needs_owner: bool = False,
    harness: str | None = None,
    autostart: bool = False,
    route: str | None = None,
    parent: str | None = None,
    discovered_from: str | None = None,
    hints: bool = False,
) -> dict:
    """Создать задачу. `autostart`/`route` только сохраняются: процесс запускает
    не эта функция, а `listik/launcher.py` (сервер — сразу после создания, CLI
    в локальном режиме — отказом, потому что сервера нет). `route` вдобавок помечает
    карточку метками маршрута (`labels_with_route`) — как форма на доске.

    `parent` сразу связывает новую карточку с родительской мягкой связью
    `parent-child`: так заводят порции шага — у каждой свой `spec_path`/
    `checklist_path`/`review_path`, а родитель видит их все через `show`/`context`.
    Если `project` не задан, порция наследует проект родителя (иначе она уехала бы
    на другую доску). Без своего непустого `journal_path` порция наследует журнал
    родителя — журнал у шага один на все порции; остальные поля не наследуются.
    Несуществующий родитель — ошибка до вставки: карточка не создаётся.

    `discovered_from` — ID карточки, при работе над которой задачу нашли: сразу
    ставит мягкую связь `discovered-from`, и исходная карточка показывает находку
    в своих связях. Несуществующий источник — ошибка до вставки, как и с `parent`.
    Проект при этом не наследуется: найденное по ходу может относиться к другому
    проекту, а угадывание проекта молча уводило бы карточку на чужую доску.

    `hints=True` добавляет в результат `link_hints` — упоминания чужих карточек
    в тексте, с которыми связи нет (подсказка «поставь `dep link`»). Импортёрам
    это не нужно, поэтому по умолчанию выключено."""
    if not title.strip():
        raise ValueError("title не может быть пустым")
    # Владелец-человек: явный `owner` сильнее того, кто представился (`as_owner`).
    # В локальном режиме поле не пишется вовсе и оба аргумента игнорируются.
    owner_value = None
    cfg = server_cfg()
    if cfg is not None:
        owner_value = (config_mod.check_owner(owner, cfg)
                       or config_mod.check_owner(as_owner, cfg))
        if owner_value is None:
            raise errors_mod.BadArgument(OWNER_REQUIRED_CREATE)
    parent_id = (parent or "").strip() or None
    parent_project = None
    if parent_id is not None:
        parent_row = conn.execute("SELECT project, journal_path FROM tasks WHERE id = ?",
                                  (parent_id,)).fetchone()
        if parent_row is None:
            raise errors_mod.NotFound(f"задача не найдена: {parent_id}")
        parent_project = parent_row["project"]
        if not (journal_path or "").strip():
            journal_path = parent_row["journal_path"]
    source_id = (discovered_from or "").strip() or None
    if source_id is not None and not store_helpers.task_exists(conn, source_id):
        raise KeyError(f"задача не найдена: {source_id}")
    if not project and parent_project:
        project = parent_project
    # Маршрут помечает карточку теми же метками, что и форма «Новая задача» на доске:
    # их считает сервер (routes.labels_for), а не доска и не CLI по отдельности.
    labels = labels_with_route(conn, labels, route)
    tid = task_id or gen_id(conn, project)
    if parent_id is not None and parent_id == tid:
        raise ValueError(f"задача не может быть родителем самой себе: {tid}")
    if source_id is not None and source_id == tid:
        raise ValueError(f"задача не может быть найдена при самой себе: {tid}")
    ts = created_at or now_iso()
    actor_key, kind = actors_mod.resolve(created_by, conn)
    if created_by:
        actors_mod.remember(conn, created_by, actor_key, kind)
    conn.execute(
        """
        INSERT INTO tasks(id, project, title, description, acceptance, design, notes, result, status, stage,
                          stage_at, priority, issue_type, assignee, owner, labels, spec_path,
                          journal_path,
                          source, external_ref, created_at, created_by, updated_at, needs_owner,
                          checklist_path, review_path, decision_path, autostart, launch_route)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title, description=excluded.description, acceptance=excluded.acceptance,
            design=excluded.design, notes=excluded.notes, status=excluded.status,
            priority=excluded.priority, issue_type=excluded.issue_type, labels=excluded.labels,
            updated_at=excluded.updated_at
        """,
        (tid, project, title, description, acceptance, design, notes, result, status, stage,
         ts if stage else None, priority, issue_type, assignee, owner_value,
         json.dumps(labels, ensure_ascii=False), spec_path, journal_path,
         source, external_ref, ts, created_by, ts, 1 if needs_owner else 0,
         checklist_path, review_path, decision_path, 1 if autostart else 0, route),
    )
    event(conn, tid, "created", to_value=status, actor=actor_key, harness=harness,
          note=f"создана: {title[:120]}", ts=ts)
    if stage:
        event(conn, tid, "stage", from_value=None, to_value=stage, actor=actor_key,
              harness=harness, ts=ts)
    _index_task(conn, tid)
    if parent_id is not None:
        # Порция — дочерняя карточка шага (решение listik-9gsh): мягкая связь
        # parent-child, родитель закрывается только после закрытия всех детей.
        add_dep(conn, tid, parent_id, "parent-child", created_by=created_by or actor_key)
    if source_id is not None:
        # «Найдена при»: связь появляется вместе с карточкой, а не отдельным
        # `dep add`, о котором легко забыть (listik-0wpx).
        add_dep(conn, tid, source_id, "discovered-from", created_by=created_by or actor_key)
    if spec_path or journal_path or checklist_path or review_path or decision_path:
        try:
            from . import documents
            documents.index_task_documents(conn, tid)
        except Exception as exc:  # document availability must not break task creation
            event(conn, tid, "document_error", note=str(exc))
    conn.commit()
    task = get_task(conn, tid)
    if hints:
        task["link_hints"] = link_hints(conn, tid)
    return task


def link_hints(conn: sqlite3.Connection, task_id: str, *, limit: int = 5) -> list[dict]:
    """Упоминания чужих карточек в тексте задачи, с которыми нет связи.

    ID в описании — ещё не связь: без `dep add`/`dep link` исходная карточка не
    видит, что из неё что-то выросло (listik-0wpx). Возвращаем короткий список
    для предупреждения; связи не ставим сами — упоминание не всегда означает
    связь. Подсказка не должна ломать уже созданную карточку, поэтому ошибки
    чтения здесь гасим.

    Режим `hints` отсеивает id в файловых путях и кавычках: `docs/specs/<id>.md`
    или `x = "<id>"` — ссылка на файл, а не на карточку, и предупреждение по ней
    было бы ложным (вердикт grok, listik-0wpx)."""
    try:
        from . import deps as deps_mod
        found = deps_mod.mentioned(conn, task_id, limit=limit, mode="hints")
    except Exception:  # noqa: BLE001 — подсказка необязательна
        return []
    return [{"id": x["id"], "title": x["title"], "status": x["status"]} for x in found]


UPDATABLE = {
    "title", "description", "acceptance", "design", "notes", "result", "status", "stage",
    "priority", "issue_type", "assignee", "owner", "holder", "holder_note", "project", "labels",
    "spec_path", "checklist_path", "review_path", "decision_path", "journal_path", "worktree", "branch", "close_reason", "needs_owner",
    "external_ref", "archived",
    # «Тип запуска» задачи. Остальные восемь колонок запуска по-прежнему пишут
    # только `create_task` и `launcher.py`, а маршрут можно сменить правкой
    # карточки — но лишь пока работа не началась (`route_change_denied`).
    "launch_route",
    # Области роя, валидация — `scope.normalize_scope`; `dispatch_id`/`generation`
    # сюда не входят.
    "read_scope", "write_scope",
}

#: Поле карточки с «типом запуска» и его алиас: алиасом маршрут зовут создание
#: задачи (POST /api/tasks) и доска, поэтому PATCH и `listik set` принимают оба имени.
ROUTE_FIELD = "launch_route"
ROUTE_ALIAS = "route"

#: Хвост отказа — один и тот же у проверки до записи и у гонки на самой записи.
ROUTE_LOCKED_TAIL = (". Его меняют, только пока задача заведена — без этапа, "
                     "держателя и запущенного процесса")

#: SQL-условие «задача ещё заведена» — ровно то, что проверяет `route_change_denied`.
#: Стоит в WHERE самого UPDATE: между чтением карточки и записью задачу могли взять
#: в работу (`claim`), тогда UPDATE не заденет ни одной строки и `update_task` откажет.
ROUTE_GUARD_SQL = ("status = 'open' AND trim(ifnull(holder, '')) = '' "
                   "AND trim(ifnull(stage, '')) = '' AND trim(ifnull(launched_by, '')) = ''")

#: Поля, которые могут «начать работу» в том же вызове `update_task`, что и смена
#: маршрута: их новые значения учитывает проверка (см. `route_card_after`).
ROUTE_STARTING_FIELDS = ("status", "stage", "holder")

#: Отказ автостарта: автор вопроса «нужен человек» и начало его текста. Одни и те же
#: константы у `launcher.refuse` (пишет вопрос) и у `update_task` (узнаёт по истории,
#: что флаг поднят именно отказом автостарта, — см. `autostart_reset`).
AUTOSTART_ACTOR = "agent:listik"
AUTOSTART_QUESTION_PREFIX = "автостарт не выполнен"


def route_change_denied(row) -> str | None:
    """Почему у задачи нельзя сменить маршрут; None — можно (работа не началась).

    Маршрут — «тип запуска»: его выбирают при создании и меняют, пока задача
    заведена, то есть у неё нет этапа, держателя и запущенного процесса. Как
    только работа началась, смена запрещена: сервер отказывает понятной ошибкой,
    а доска не даёт выбрать новый маршрут (поле `route_editable` в карточке).

    Условия отказа держим синхронными с `ROUTE_GUARD_SQL` — той же проверкой,
    но уже на самой записи, — иначе смену маршрута можно было бы протащить
    гонкой с `claim`.
    """
    reasons = []
    if (row["status"] or "") != "open":
        reasons.append(f"статус «{STATUS_TITLES.get(row['status'], row['status'])}»")
    if (row["stage"] or "").strip():
        reasons.append(f"этап {row['stage']}")
    if (row["holder"] or "").strip():
        reasons.append(f"держит {row['holder']}")
    if (row["launched_by"] or "").strip():
        reasons.append("процесс уже запускали")
    if not reasons:
        return None
    return "маршрут нельзя менять: " + ", ".join(reasons) + ROUTE_LOCKED_TAIL


def route_card_after(row, fields: dict) -> dict:
    """Карточка такой, какой она станет после этого же вызова `update_task`.

    Один запрос может начать работу (`status`/`stage`/`holder`) и заодно сменить
    маршрут: проверять смену только по старой строке нельзя — после запроса задача
    уже не заведена, и менять маршрут поздно. `fields` — уже разобранные поля вызова.

    Это дополнение к проверке старой строки, а не замена: условный UPDATE
    (`ROUTE_GUARD_SQL`) видит состояние до запроса, поэтому «снять этап и сменить
    маршрут одним вызовом» тоже отказ — задача должна быть заведена уже сейчас.
    """
    effective = dict(row)
    for key in ROUTE_STARTING_FIELDS:
        value = fields.get(key)
        if value is None:
            continue
        # Не строка (например, список) до записи доходит как JSON — приведём так же,
        # чтобы проверка не падала, а видела «работа началась».
        effective[key] = value if isinstance(value, str) else str(value)
    return effective


def clear_route_on_route_removed(conn: sqlite3.Connection, route_key: str) -> int:
    """Снять `launch_route` и его метки со всех задач — маршрут сам удаляется.

    Для `DELETE /api/routes/<key>` (шаг listik-8jgz, порция c): это не смена
    маршрута задачи автором, а удаление справочной записи, на которую эти
    задачи ссылались, — поэтому `route_change_denied` тут не действует, в
    отличие от обычного пути `update_task`: задачу в любом статусе и на любом
    этапе всё равно нужно избавить от ссылки на маршрут, которого больше нет.
    Трогает только `launch_route` и метки (`labels_after_route_change(labels,
    "")` — то же правило, что снимает маршрут при обычной смене); статус, этап,
    держатель и любые другие поля задачи не меняются, `updated_at` не двигается.
    Возвращает число затронутых задач.
    """
    rows = conn.execute("SELECT id, labels FROM tasks WHERE launch_route = ?",
                        (route_key,)).fetchall()
    for row in rows:
        labels = route_labels_from_row(row)
        cleared = labels_after_route_change(conn, labels, "") or []
        conn.execute("UPDATE tasks SET launch_route = NULL, labels = ? WHERE id = ?",
                     (json.dumps(cleared, ensure_ascii=False), row["id"]))
        _index_task(conn, row["id"])
    conn.commit()
    return len(rows)


def autostart_flag_raised(conn: sqlite3.Connection, task_id: str) -> bool:
    """Флаг «нужен человек» поднят отказом автостарта, а не вопросом человека.

    Ошибка запуска (`launch_error`) и флаг ставятся одной транзакцией
    (`launcher.refuse` → `set_needs_owner`), поэтому смотрим последний
    вопрос/ответ в истории: если это вопрос от `agent:listik` с текстом
    «автостарт не выполнен: …» — флаг принадлежит отказу. Вопрос, заданный
    человеком уже после отказа, — чужой: смена маршрута его не отменяет.

    Истории вопросов нет вовсе (задача поднята из фикстуры/импорта), но
    `launch_error` стоит — считаем, что флаг пришёл вместе с ошибкой.
    """
    last = conn.execute(
        "SELECT kind, actor, note FROM events WHERE task_id = ? "
        "AND kind IN ('question', 'answer') ORDER BY rowid DESC LIMIT 1",
        (task_id,)).fetchone()
    if last is None:
        return True
    return bool(last["kind"] == "question"
                and (last["actor"] or "") == AUTOSTART_ACTOR
                and (last["note"] or "").startswith(AUTOSTART_QUESTION_PREFIX))


def autostart_reset(conn: sqlite3.Connection, task_id: str, row) -> tuple[list[str], list[str]]:
    """Что снимает смена маршрута: присваивания для UPDATE и пояснения к событию.

    Маршрут — это «тип запуска»: прежний отказ автостарта к новому маршруту не
    относится, поэтому `launch_error` снимается при любой смене (и при снятии
    маршрута пустой строкой). Флаг «нужен человек» — только если его поднял сам
    отказ автостарта (см. `autostart_flag_raised`): чужой вопрос смена маршрута
    не отменяет. Присваивания уходят в тот же условный UPDATE, что и `launch_route`,
    поэтому гонка с `claim` откатывает и их.
    """
    error = (row["launch_error"] or "").strip()
    if not error:
        return [], []
    sets = ["launch_error = NULL"]
    notes = [f"снята ошибка автостарта: {error}"]
    if row["needs_owner"] and autostart_flag_raised(conn, task_id):
        sets.append("needs_owner = 0")
        notes.append("снят флаг «нужен человек»")
    return sets, notes


def delete_task(conn: sqlite3.Connection, task_id: str) -> None:
    """Remove a task and every derived row: FTS entries, embeddings and indexed
    documents/chunks. ``documents``/``document_chunks`` also cascade via the foreign
    key on ``task_id``, but they are cleared explicitly here so removal does not
    depend on ``PRAGMA foreign_keys`` staying on for a given connection."""
    conn.execute("DELETE FROM task_fts WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM comment_fts WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM document_chunk_fts WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM embeddings WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM documents WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM comments WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()


def update_task(conn: sqlite3.Connection, task_id: str, *, actor: str | None = None,
                harness: str | None = None, note: str | None = None,
                as_owner: str | None = None, **fields) -> dict:
    row = store_helpers.task_row(conn, task_id)
    # Владелец: в локальном режиме поле молча выбрасываем (карточка по нему не
    # меняется, события нет), в серверном — проверяем по `server.users`. Чужую
    # задачу нельзя править, но сменить или снять у неё владельца можно: иначе
    # задачу, оставленную уехавшим человеком, никто бы не подобрал.
    owner_cfg = server_cfg()
    if owner_cfg is None:
        fields.pop("owner", None)
    else:
        if "owner" in fields:
            fields["owner"] = config_mod.check_owner(fields["owner"], owner_cfg) or ""
        as_owner_value = config_mod.check_owner(as_owner, owner_cfg)
        row_owner = (row["owner"] or "").strip()
        other_fields = [key for key, value in fields.items()
                        if key != "owner" and value is not None]
        if as_owner_value and row_owner and row_owner != as_owner_value and other_fields:
            raise errors_mod.Forbidden(
                f"задача {task_id} принадлежит {row_owner}: чужую задачу править нельзя")
    # Маршрут принимаем и под именем создания задачи (`route`): доска и `listik set`
    # шлют его так же, как POST /api/tasks. Каноническое имя — колонка launch_route.
    if ROUTE_ALIAS in fields and ROUTE_FIELD not in fields:
        fields[ROUTE_FIELD] = fields.pop(ROUTE_ALIAS)
    if ROUTE_FIELD in fields and fields[ROUTE_FIELD] is not None:
        fields[ROUTE_FIELD] = store_helpers.normalize_route(fields[ROUTE_FIELD])
    # Вместе с маршрутом сервер сам переписывает его метки (`harness:`/`process:`) —
    # ровно так же, как при создании задачи: старые снимаются, метки нового встают на
    # их место, чужие метки остаются. Клиент про них больше не думает.
    if ROUTE_FIELD in fields and fields[ROUTE_FIELD] != (row[ROUTE_FIELD] or ""):
        base = fields.get("labels")
        if base is None:
            base = route_labels_from_row(row)
        if isinstance(base, list):
            merged = labels_after_route_change(conn, base, fields[ROUTE_FIELD])
            if merged is not None:
                fields["labels"] = merged
    # Закрытие снимает держателя (как `stage --to done`, listik-rku8): у закрытой
    # карточки его нет, переданный `holder` не в счёт. Держателя не было — поле
    # выбрасываем, иначе `None` против `""` дал бы ложный `release`.
    if fields.get("status") in FINAL_STATUSES and fields["status"] != row["status"]:
        if (row["holder"] or "").strip():
            fields["holder"] = ""
        else:
            fields.pop("holder", None)
    # Смену маршрута проверяем по карточке, какой она станет после этого вызова:
    # в тех же полях может прийти начало работы (`status`/`stage`/`holder`).
    effective = route_card_after(row, fields)
    sets, params = [], []
    changes: list[tuple[str, object, object]] = []
    # Пояснения к событию `route`: смена маршрута снимает прошлый отказ автостарта.
    reset_notes: list[str] = []
    for key, value in fields.items():
        if key not in UPDATABLE or value is None:
            continue
        if key in scope_mod.FIELDS:
            value = json.dumps(scope_mod.normalize_scope(value, field=key),
                                ensure_ascii=False)
        elif isinstance(value, list):
            value = json.dumps(value, ensure_ascii=False)
        if key == "needs_owner":
            value = 1 if value else 0
        elif key == "owner":
            # Пустая строка снимает владельца (None до полей не доходит — см. выше).
            value = value or None
        elif key == "worktree" and isinstance(value, str):
            # Маркер основной ветки храним канонически (`MAIN` → `main`), путь —
            # как дали, только без крайних пробелов (маркер `main` иначе не
            # отличить от имени каталога, см. `main_worktree`).
            value = main_worktree(value) or value.strip()
        if key == ROUTE_FIELD:
            # Пустая строка снимает маршрут совсем; None в поля не доходит (см. выше),
            # поэтому «стереть» можно только пустой строкой.
            value = value or None
            if (row[ROUTE_FIELD] or None) != value:
                # Отказ, если задача не заведена уже сейчас (ровно это проверит и
                # WHERE UPDATE) или перестанет быть заведённой после этого же вызова.
                denied = route_change_denied(row) or route_change_denied(effective)
                if denied:
                    raise ValueError(denied)
                # Новый «тип запуска» отменяет прошлую ошибку автостарта и поднятый
                # ею флаг — одним UPDATE с самим маршрутом (см. `autostart_reset`).
                # Явный `needs_owner` в том же вызове сильнее: его оставляем как есть.
                reset_sets, reset_notes = autostart_reset(conn, task_id, row)
                if "needs_owner" in fields:
                    reset_sets = [s for s in reset_sets if not s.startswith("needs_owner")]
                sets.extend(reset_sets)
        old = row[key]
        if str(old) == str(value):
            continue
        sets.append(f"{key} = ?")
        params.append(value)
        changes.append((key, old, value))

    actor_key, kind = actors_mod.resolve(actor, conn)
    ts = now_iso()

    if not changes:
        ename = actors_mod.display(actor_key) if actor_key else "—"
        # Карточка, а не сырая строка таблицы: вызывающий (CLI, доска) читает те же
        # поля, что и после обычной правки, — иначе no-op `set` на задаче с
        # держателем падал на отсутствующем holder_title.
        return {**get_task(conn, task_id), "unchanged": True, "requested_by": ename}

    for key, old, new in changes:
        if key == "stage":
            stage_old = row["stage"]
            dur = None
            if row["stage_at"]:
                h = hours_since(row["stage_at"])
                dur = int(h * 3600) if h else None
            sets.append("stage_at = ?")
            params.append(ts)
            event(conn, task_id, "stage", from_value=stage_old, to_value=new,
                  actor=actor_key, harness=harness, note=note, duration_s=dur)
            if new in PIPELINE_STAGES:
                # задача в конвейере — держателя не сбрасываем
                pass
        elif key == "status":
            event(conn, task_id, "status", from_value=old, to_value=new,
                  actor=actor_key, harness=harness, note=note)
            if new == "in_progress" and not row["started_at"]:
                sets.append("started_at = ?")
                params.append(ts)
            if new in FINAL_STATUSES and not row["closed_at"]:
                sets.append("closed_at = ?")
                params.append(ts)
                if new == "done" and not row["started_at"]:
                    sets.append("started_at = ?")
                    params.append(ts)
            if new not in FINAL_STATUSES and row["closed_at"]:
                sets.append("closed_at = NULL")
                sets.append("close_reason = NULL")
        elif key == "holder":
            event(conn, task_id, "release" if not new else "claim",
                  from_value=old, to_value=new, actor=actor_key, harness=harness, note=note)
            if new:
                sets.append("holder_at = ?")
                params.append(ts)
            # «Что делает» принадлежит прежнему держателю: смена держателя (release,
            # claim другим, handoff) её сбрасывает, если заметку не передали явно.
            if "holder_note" not in fields and row["holder_note"]:
                sets.append("holder_note = NULL")
        elif key == "needs_owner":
            event(conn, task_id, "question" if new else "answer",
                  from_value=old, to_value=new, actor=actor_key, harness=harness, note=note)
        elif key == "assignee":
            event(conn, task_id, "note", from_value=old, to_value=new, actor=actor_key,
                  harness=harness, note=f"исполнитель: {note or ''}".strip())
        elif key == ROUTE_FIELD:
            event(conn, task_id, "route", from_value=old, to_value=new, actor=actor_key,
                  harness=harness, note=" · ".join(x for x in [note, *reset_notes] if x) or None)

    sets.append("updated_at = ?")
    params.append(ts)
    where = "id = ?"
    params.append(task_id)
    # Смена маршрута пишется условным UPDATE: «ещё заведена» проверяется на самой
    # записи. Между чтением карточки выше и этим UPDATE задачу мог взять `claim`
    # на другом соединении — тогда условие не выполнится, строка не изменится.
    route_guard = any(key == ROUTE_FIELD for key, _old, _new in changes)
    if route_guard:
        where += f" AND ({ROUTE_GUARD_SQL})"
    cur = conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE {where}", params)
    if route_guard and cur.rowcount == 0:
        # Гонку выиграл `claim` (или запуск процесса): отказываем тем же текстом,
        # что и обычная проверка, — по свежей строке, а не по устаревшей. Правки
        # и события этого вызова откатываем целиком.
        if conn.in_transaction:
            conn.rollback()
        fresh = store_helpers.task_row(conn, task_id, required=False)
        if fresh is None:
            raise errors_mod.NotFound(f"задача не найдена: {task_id}")
        raise ValueError(route_change_denied(fresh) or (
            "маршрут нельзя менять: задачу взяли в работу, пока он менялся" + ROUTE_LOCKED_TAIL))
    # статус изменился — пересчитываем флаг блокировки у этой задачи и её ждущих
    deps_mod.refresh_task(conn, task_id)
    _index_task(conn, task_id)
    if any(k in fields for k in ("spec_path", "checklist_path", "review_path", "decision_path", "journal_path")):
        try:
            from . import documents
            documents.index_task_documents(conn, task_id)
        except Exception as exc:
            event(conn, task_id, "document_error", note=str(exc))
    conn.commit()
    if any(key == "status" and new in FINAL_STATUSES for key, _old, new in changes):
        # Порция нарезки закрылась — возможно, это была последняя: родитель-рой
        # закрывается сам (`stage_launch.close_swarm_parent`, listik-2gry), а при
        # отмене последней живой порции на родителе ставится вопрос человеку
        # (`stage_launch.note_portions_cancelled`). Ленивый импорт: `stage_launch`
        # уже импортирует `store`, цикл наверху нельзя.
        try:
            from . import stage_launch
            stage_launch.close_swarm_parent(conn, task_id)
            stage_launch.note_portions_cancelled(conn, task_id)
        except Exception as exc:  # noqa: BLE001 — закрытие порции не роняем
            event(conn, task_id, "swarm_parent_error", note=str(exc))
            conn.commit()
    return get_task(conn, task_id)


def set_needs_owner(conn: sqlite3.Connection, task_id: str, *, value: bool,
                    text: str | None = None, actor: str | None = None,
                    harness: str | None = None) -> dict:
    """Поднять/снять флаг «нужен человек», всегда фиксируя вопрос/ответ в истории.

    В отличие от `update_task(..., needs_owner=...)`, пишет комментарий и событие
    при каждом вызове — даже если флаг уже стоит в нужном значении, чтобы второй
    вопрос с другим текстом не терялся.
    """
    row = store_helpers.task_row(conn, task_id)
    old_value = bool(row["needs_owner"])
    new_value = bool(value)

    actor_key, _kind = actors_mod.resolve(actor, conn)
    ts = now_iso()

    clean_text = (text or "").strip()
    if clean_text:
        add_comment(conn, task_id, clean_text, author=actor,
                    kind="question" if new_value else "answer", harness=harness)

    event(conn, task_id, "question" if new_value else "answer",
          from_value=old_value, to_value=new_value, actor=actor_key, harness=harness,
          note=text, ts=ts)

    conn.execute("UPDATE tasks SET needs_owner = ?, updated_at = ? WHERE id = ?",
                 (1 if new_value else 0, ts, task_id))
    deps_mod.refresh_task(conn, task_id)
    _index_task(conn, task_id)
    conn.commit()
    return get_task(conn, task_id)


def claim(conn: sqlite3.Connection, task_id: str, *, holder: str, harness: str | None = None,
          note: str | None = None, force: bool = False, actor: str | None = None,
          as_owner: str | None = None) -> dict:
    """Агент берёт задачу: держатель, heartbeat, статус в работе.

    Заблокированную задачу взять нельзя: сначала надо закрыть блокеры.
    Обойти можно только явным force (и это останется в истории). Лок рабочего
    дерева распространяется только на пишущие задачи (`s3-impl`/`s4-judge`/без
    этапа) — держатель на `s1-spec`/`s2-review` дерево не занимает.

    `actor` — кто именно берёт (`agent:dsh`): событие `claim` с автором и есть
    доказательство, что агент запустился. Повторный claim тем же держателем
    идемпотентен, но если держателя до этого поставил оркестратор (`stage
    --holder`), первый claim агента пишет событие — «выдана» становится «взята».
    """
    row = store_helpers.task_row(conn, task_id)
    # Чужую задачу не берут: проверка идёт раньше харнесса, статуса, держателя и
    # блокеров — и `force` её не обходит (он про блокеры, а не про владельца).
    check_task_owner(row, as_owner, task_id=task_id)
    if row["status"] in FINAL_STATUSES:
        raise ValueError(f"задача {task_id} уже {row['status']}")
    # A silent holder past the red-verdict return window loses the task before we
    # even look at who holds it — otherwise this claim would just bounce off "уже
    # удерживается" instead of taking over.  Re-read the row: expiry commits its own
    # UPDATE, which the row fetched above cannot see.
    deps_mod.expire_return_handoffs(conn, task_id=task_id)
    row = store_helpers.task_row(conn, task_id)
    # Claim is idempotent for the current holder, but must never replace another
    # holder.  This also makes a repeated claim in the same worktree safe.
    current_holder = (row["holder"] or "").strip()
    if current_holder:
        # Тот же держатель — это тот же актор, а не та же строка: `claim --holder
        # agent:dsh` на карточке с `holder='dsh'` идемпотентен, а не «уже
        # удерживается». Хранимое написание при этом не переписывается — колонка
        # держит то, что дал первый claim/выдача.
        if actors_mod.same_actor(current_holder, holder, conn):
            # Refresh holder_at only: this both answers the repeated claim and,
            # crucially, extends the red-verdict return window (see
            # deps.expire_return_handoffs) so an agent that resumes with `claim`
            # rather than `heartbeat` is not treated as having gone silent.
            state = holder_claim_state(conn, task_id, current_holder)
            ts = now_iso()
            conn.execute("UPDATE tasks SET holder_at = ?, updated_at = ? WHERE id = ?",
                        (ts, ts, task_id))
            if not state["taken"]:
                # Держателя поставил кто-то другой (`stage --holder <кому>`):
                # первый claim самого агента — то самое доказательство запуска,
                # по которому доска отличает «выдана» от «взята». Повторные claim
                # того же держателя события не плодят.
                actor_key, a_kind = actors_mod.resolve(actor, conn)
                if actor:
                    actors_mod.remember(conn, actor, actor_key, a_kind)
                event(conn, task_id, "claim", from_value=current_holder,
                      to_value=current_holder, actor=actor_key, harness=harness,
                      note=note or "взял задачу, которую выдали")
            conn.commit()
            return get_task(conn, task_id)
        cur_task = row_to_task(conn, row)
        stale_note = ", молчит — брошена?" if cur_task["stale"] else ""
        raise ValueError(
            f"задача {task_id} уже удерживается {cur_task['holder_title']} "
            f"({cur_task['holder_age']}{stale_note}). Варианты: дождаться, "
            f"listik release {task_id} (если держатель мёртв), или взять другую задачу из listik ready"
        )
    # A repository/worktree is a write lock, but only for writing tasks (s3-impl,
    # s4-judge, or no stage at all — a direct claim -> code -> done task).
    conflict = deps_mod.worktree_conflict(conn, row, holder=holder)
    if conflict is not None:
        conflict_task = row_to_task(conn, conflict)
        wt = (row["worktree"] or "").strip()
        # Дерево одно и то же, но задано оно могло быть по-разному (пусто, маркер,
        # путь) — подпись берём с обеих сторон: «работа в main» красноречивее
        # пустого поля, из-за которого конфликт и возник.
        wt_label = (main_worktree_title(wt)
                    or main_worktree_title((conflict["worktree"] or "").strip())
                    or wt or "основное")
        stale_note = ", молчит — брошена?" if conflict_task["stale"] else ""
        raise ValueError(
            f"рабочее дерево {wt_label} проекта {row['project'] or '—'} занято задачей "
            f"{conflict['id']} ({conflict_task['title'][:80]}), держит {conflict_task['holder_title']} "
            f"{conflict_task['holder_age']}{stale_note}. Варианты: дождаться или listik release "
            f"{conflict['id']}, указать этой задаче другое дерево (listik set {task_id} worktree=/путь), "
            "или взять другую задачу"
        )
    state = deps_mod.ready(conn, task_id)
    if state["blocked_by"] and not force:
        names = "; ".join(f"{b['id']} — {b['title'][:80]} [{b['status']}"
                          + (f", держит {b['holder_title']} {b['idle_age']}" if b["holder"] else "")
                          + "]" for b in state["blocked_by"][:5])
        raise ValueError(
            f"задача {task_id} заблокирована и брать её нельзя.\n"
            f"Ждём завершения: {names}\n"
            "Варианты: взять сам блокер, поставить блокеру needs-owner, "
            "или осознанно обойти запрет: claim --force (останется в истории)"
        )
    if state["blocked_by"] and force:
        event(conn, task_id, "note", actor=actors_mod.resolve(actor, conn)[0] or holder,
              harness=harness,
              note=f"ЗАПУСК БЕЗ РАЗРЕШЕНИЯ БЛОКЕРОВ: {', '.join(b['id'] for b in state['blocked_by'])}")
    extra = {}
    if row["status"] == "open":
        extra["status"] = "in_progress"
    # Идентичность берущего пишем в событие даже без явного `--harness`: сам вызов
    # `claim` означает «беру я», поэтому держатель и есть автор. Без этого события
    # старых клиентов выглядели бы как «выдана, но не взята».
    out = update_task(conn, task_id, actor=actor, holder=holder,
                      assignee=row["assignee"] or holder,
                      harness=harness or holder,
                      note=note or f"взял в работу: {holder}", **extra)
    return out


def heartbeat(conn: sqlite3.Connection, task_id: str, *, holder: str, note: str | None = None,
              harness: str | None = None, actor: str | None = None,
              min_interval_min: int = 10, as_owner: str | None = None) -> dict:
    row = conn.execute("SELECT holder, holder_at, holder_note, owner FROM tasks WHERE id = ?",
                       (task_id,)).fetchone()
    if not row:
        raise errors_mod.NotFound(f"задача не найдена: {task_id}")
    check_task_owner(row, as_owner, task_id=task_id)
    ts = now_iso()
    actor_key, a_kind = actors_mod.resolve(actor, conn)
    if actor:
        actors_mod.remember(conn, actor, actor_key, a_kind)
    # «Что делает» принадлежит тому, кто её написал: heartbeat, сменивший держателя
    # без claim, не наследует чужую заметку — остаётся только переданная явно.
    # Смена держателя — это смена актора, а не написания: heartbeat того же агента
    # под другим именем (`claude` → `agent:claude`) держателя не меняет, заметку не
    # сбрасывает и хранимое написание не переписывает.
    current_holder = (row["holder"] or "").strip()
    holder_changed = not actors_mod.same_actor(current_holder, holder, conn)
    stored_holder = holder if holder_changed else current_holder
    holder_note = note or (row["holder_note"] if not holder_changed else None)
    conn.execute("UPDATE tasks SET holder = ?, holder_at = ?, holder_note = ?, updated_at = ? "
                 "WHERE id = ?", (stored_holder, ts, holder_note, ts, task_id))
    last = parse_ts(row["holder_at"])
    # Смену держателя пишем в историю всегда, даже если 10 минут ещё не прошли:
    # иначе перехват чужой задачи остался бы незаметным. Карточка «выдана, но не
    # взята» — тот же случай: heartbeat её держателя и есть доказательство, что
    # прогон запустился, поэтому первый удар не теряется в троттлинге. Уже взятая
    # карточка троттлится как раньше (listik-udop).
    taken = True if holder_changed else holder_claim_state(conn, task_id, stored_holder)["taken"]
    if (holder_changed or not taken or not last
            or (datetime.now(timezone.utc) - last) > timedelta(minutes=min_interval_min)):
        event(conn, task_id, "heartbeat", from_value=row["holder"] if holder_changed else None,
              to_value=stored_holder, actor=actor_key, note=note, harness=harness)
    conn.commit()
    return get_task(conn, task_id)


def add_comment(conn: sqlite3.Connection, task_id: str, text: str, *, author: str | None = None,
                kind: str = "comment", harness: str | None = None,
                created_at: str | None = None) -> dict:
    if not store_helpers.task_exists(conn, task_id):
        raise errors_mod.NotFound(f"задача не найдена: {task_id}")
    requested_kind = kind
    verdict_accepted = None
    verdict_message = None
    actor_key, actor_kind = actors_mod.resolve(author, conn)
    is_agent = actor_kind == "agent" or (author or "").strip().lower().startswith("agent:")
    if kind == "verdict":
        task_row = conn.execute("SELECT stage, status, holder FROM tasks WHERE id = ?",
                                (task_id,)).fetchone()
        stage = task_row["stage"]
        if stage != "s4-judge" and is_agent:
            kind = "comment"
            verdict_accepted = False
            verdict_message = (
                f"вердикт не принят: карточка не на этапе s4-judge "
                f"и автор не человек (текущий этап: {stage or 'не задан'}); "
                "текст сохранён как обычный комментарий")
        else:
            verdict_accepted = True
    failed = parse_verdict(text) if kind == "verdict" else False
    if author:
        actors_mod.remember(conn, author, actor_key, actor_kind)
    ts = created_at or now_iso()
    # Two comments in the same millisecond collide on the 3-digit suffix: retry with a new one.
    for attempt in range(10):
        cid = f"{task_id}:{int(datetime.now().timestamp() * 1000)}:{random.randint(100, 999)}"
        try:
            conn.execute(
                "INSERT INTO comments(id, task_id, author, kind, text, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (cid, task_id, author, kind, text, ts),
            )
            break
        except sqlite3.IntegrityError as exc:
            if "comments.id" not in str(exc) or attempt == 9:
                raise
    conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (ts, task_id))
    event(conn, task_id, "comment", to_value=kind, actor=actor_key, harness=harness,
          note=text[:200], ts=ts)
    # Red verdict at s4 returns work to the implementer (sticky) within a configured window.
    if failed:
        task_row = conn.execute("SELECT stage, project FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task_row and task_row["stage"] == "s4-judge":
            next_stage(conn, task_id, to_stage="s3-impl", actor=author, harness=harness,
                      note="возврат после красного verdict")
    _index_comment(conn, cid)
    conn.commit()
    out = {"id": cid, "task_id": task_id, "author": author, "kind": kind, "text": text,
           "created_at": ts}
    if requested_kind == "verdict":
        out["verdict_accepted"] = verdict_accepted
        if verdict_message:
            out["message"] = verdict_message
    return out


VERDICT_FORMAT = ('first line must be exactly "VERDICT: PASS" or "VERDICT: FAIL"; '
                  'after FAIL list the required fixes on the next lines')


def parse_verdict(text: str | None) -> bool:
    """Return True for "VERDICT: FAIL", False for "VERDICT: PASS"; raise ValueError otherwise.
    Only the first line decides; a FAIL must carry the list of fixes below it."""
    head, _, body = (text or "").strip().partition("\n")
    head = head.strip()
    if head == "VERDICT: PASS":
        return False
    if head == "VERDICT: FAIL":
        if not body.strip():
            raise ValueError(f"verdict FAIL without fixes: {VERDICT_FORMAT}")
        return True
    raise ValueError(f"bad verdict format: {VERDICT_FORMAT}")


def stage_unchanged(conn: sqlite3.Connection, task_id: str, *, stage: str | None,
                    note: str | None = None, harness: str | None = None,
                    actor: str | None = None, holder: str | None = None) -> dict:
    """`stage --to <текущий этап>`: перехода нет, карточка остаётся как была.

    Событие `stage` с одинаковыми from/to соврало бы про смену этапа и обнулило
    `stage_at`, а handoff по умолчанию снял бы держателя у задачи, которая никуда
    не поехала. Поэтому этап не меняем — но заметку сохраняем в истории, чтобы
    вызов не пропал зря, и возвращаем карточку с флагом `stage_unchanged`, по
    которому CLI печатает понятную строку (listik-xut1).

    Явный `holder` — исключение: это повторная выдача (круг после красного
    вердикта и `release`). Этап и `stage_at` по-прежнему не трогаем, но держателя
    ставим и пишем событие-назначение `claim` (автор — выдающий), причём даже
    когда держатель тот же самый: `holder_claim_state` отсчитывает «взята» от
    последнего назначения, и без нового события старый claim того же харнесса
    с прошлого круга выглядел бы как взятие (listik-udop).
    """
    row = conn.execute("SELECT holder FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise errors_mod.NotFound(f"задача не найдена: {task_id}")
    old_holder = (row["holder"] or "").strip()
    target = (holder or "").strip()
    actor_key, a_kind = actors_mod.resolve(actor, conn)
    if actor:
        actors_mod.remember(conn, actor, actor_key, a_kind)
    ts = now_iso()
    if note:
        event(conn, task_id, "note", actor=actor_key, harness=harness, note=note)
    if target:
        sets = ["holder = ?", "holder_at = ?", "updated_at = ?"]
        params: list = [target, ts, ts]
        if not actors_mod.same_actor(target, old_holder, conn):
            # «Что делает» принадлежит прежнему держателю: назначение нового её
            # сбрасывает — ровно как смена держателя в `update_task`. Тот же актор
            # под другим написанием новым держателем не считается (`same_actor`).
            sets.append("holder_note = NULL")
        params.append(task_id)
        conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
        event(conn, task_id, "claim", from_value=old_holder, to_value=target,
              actor=actor_key, harness=harness,
              note=note or f"выдал задачу исполнителю: {target}")
    if note or target:
        conn.commit()
    out = get_task(conn, task_id)
    out["unchanged"] = True
    out["stage_unchanged"] = True
    if target:
        # Не врём про держателя: он как раз сменился (или выдан заново). «Взята»
        # он станет только после claim/heartbeat самого харнесса.
        state = "выдана, но не взята" if out.get("not_taken") else "взята"
        out["message"] = (f"этап не менялся: задача уже на {stage} — перехода нет; "
                          f"держатель {target} — {state}"
                          + ("; заметка записана в историю" if note else ""))
    else:
        out["message"] = (f"этап не менялся: задача уже на {stage} — перехода нет; "
                          "держатель тоже не менялся"
                          + ("; заметка записана в историю" if note else ""))
    return out


def next_stage(conn: sqlite3.Connection, task_id: str, *, holder: str | None = None,
               note: str | None = None, harness: str | None = None,
               to_stage: str | None = None, actor: str | None = None,
               as_owner: str | None = None) -> dict:
    row = conn.execute("SELECT stage, project, owner FROM tasks WHERE id = ?",
                       (task_id,)).fetchone()
    if not row:
        raise errors_mod.NotFound(f"задача не найдена: {task_id}")
    # Владельца спрашиваем только при выдаче карточки (`--holder`): перевод этапа
    # без держателя владельца не требует и не проверяет вовсе (решение автора).
    if (holder or "").strip():
        check_task_owner(row, as_owner, task_id=task_id)
    cur = row["stage"]
    if to_stage is not None:
        if to_stage != "done" and to_stage not in PIPELINE_STAGES:
            raise ValueError(f"неизвестный этап: {to_stage}")
        nxt = to_stage
    elif cur in PIPELINE_STAGES:
        idx = PIPELINE_STAGES.index(cur)
        nxt = PIPELINE_STAGES[idx + 1] if idx + 1 < len(PIPELINE_STAGES) else "done"
    else:
        nxt = PIPELINE_STAGES[0]
    if to_stage is not None and nxt == cur:
        # Явно попросили этап, на котором задача уже стоит (частый случай —
        # `stage <id> --to <текущий> --note "…"` после release): это не переход.
        # Явный holder при этом — повторная выдача, её обрабатывает stage_unchanged.
        return stage_unchanged(conn, task_id, stage=cur, note=note,
                               harness=harness, actor=actor, holder=holder)
    fields = {"stage": nxt}
    if nxt == "done":
        # Этап done — это закрытие, как `listik done`: статус, closed_at и снятый
        # держатель; иначе карточка висела «в работе» на этапе done (listik-rku8).
        return update_task(conn, task_id, actor=actor, harness=harness,
                           note=note or f"этап -> done (закрыта из {cur or '—'})",
                           stage="done", status="done", holder="")
    transition = config_mod.transition_kind(row["project"], cur, nxt, conn=conn)
    # Handoff intentionally releases the previous writer so the next harness
    # must claim the stage.  Sticky transitions keep/optionally refresh holder.
    if transition == "handoff":
        # Явный holder на handoff — это выдача карточки следующему харнессу:
        # держателя ставим сразу (событие-назначение `claim` пишет update_task,
        # автор — выдающий), и до собственного claim харнесса карточка «выдана,
        # но не взята». Без holder — как раньше, снимаем держателя.
        fields["holder"] = holder or ""
    elif holder:
        fields["holder"] = holder
    return update_task(conn, task_id, actor=actor, harness=harness,
                       note=note or f"этап -> {nxt} ({transition})", **fields)


# ------------------------------------------------------------------ чтение

def get_task(conn: sqlite3.Connection, task_id: str, *, with_details: bool = True,
            with_rejected: bool = False) -> dict:
    row = store_helpers.task_row(conn, task_id)
    out = row_to_task(conn, row)
    try:
        from . import deps as deps_mod
        out["deps_state"] = deps_mod.ready(conn, task_id)
    except Exception:  # noqa: BLE001 — срез зависимостей не должен ломать карточку
        out["deps_state"] = None
    if with_details or with_rejected:
        from . import fence as fence_mod
    if with_details:
        out["documents"] = task_documents(conn, task_id)
        # Все порции шага, включая закрытые: холодный старт родителя должен видеть
        # каждую дочернюю карточку с её spec/checklist/review.
        out["children"] = child_cards(conn, task_id)
        out["comments"] = store_helpers.dict_rows(conn.execute(
            "SELECT id, author, kind, text, created_at FROM comments WHERE task_id = ? "
            "ORDER BY created_at", (task_id,)))
        out["dependencies"] = store_helpers.dict_rows(conn.execute(
            "SELECT depends_on, dep_type, created_at FROM deps WHERE issue_id = ?", (task_id,)))
        out["dependents"] = store_helpers.dict_rows(conn.execute(
            "SELECT issue_id, dep_type FROM deps WHERE depends_on = ?", (task_id,)))
        # Карантин (события `rejected`) сюда не попадает: агент не должен видеть
        # отвергнутые записи зомби нигде, кроме явного `with_rejected`/`?rejected=1`.
        out["events"] = store_helpers.dict_rows(conn.execute(
            "SELECT ts, kind, from_value, to_value, actor, harness, note, duration_s "
            "FROM events WHERE task_id = ? AND kind != ? ORDER BY ts DESC LIMIT 100",
            (task_id, fence_mod.REJECTED_KIND)))
    if with_rejected:
        out["rejected"] = fence_mod.list_rejected(conn, task_id)
    return out


def select_task_fields(task: dict, fields) -> dict:
    """Оставить в карточке только запрошенные поля (`--fields a,b` / `?fields=a,b`).

    `fields` — список токенов или одна строка через запятую: и CLI, и query-
    параметр могут задавать поля и списком, и повторением флага. Фильтр делает
    та сторона, которая собрала карточку (сервер, `local_call`, MCP), — агенту
    не нужно тянуть полную карточку ради одного `launch_route`.

    Неизвестное поле — `BadArgument` с перечнем доступных полей: молчаливый
    `None` (как было в клиентском прототипе `--field`) агент не отличил бы от
    пустого значения, а ошибка с кодом `bad_argument` ведёт к исправлению запроса.
    Порядок ключей в ответе — как в запросе; без `fields` карточка возвращается
    как есть.
    """
    names: list[str] = []
    for chunk in ([fields] if isinstance(fields, str) else (fields or [])):
        names.extend(t.strip() for t in str(chunk).split(",") if t.strip())
    if not names:
        return task
    unknown = sorted({n for n in names if n not in task})
    if unknown:
        raise errors_mod.BadArgument(
            "неизвестное поле карточки: " + ", ".join(unknown)
            + " (доступные поля: " + ", ".join(sorted(task)) + ")")
    return {n: task[n] for n in names}


def holder_claim_state(conn: sqlite3.Connection, task_id: str, holder: str | None) -> dict:
    """Взял ли держатель задачу сам или её только выдали.

    `claim` и `stage --holder <кому>` пишут одно и то же событие `claim`, поэтому
    доска различает «выдана» и «взята» по автору события: взята — если после
    назначения сам держатель записал `claim` или `heartbeat` (`--actor agent:<holder>`
    или `--harness <holder>`). Пока такого события нет, карточка «выдана, но не
    взята»: оркестратор назначил исполнителя, а тот не запустился.

    Возраст считается от события-назначения, а не от `holder_at`: heartbeat за
    исполнителя (чужой рукой) его не сбрасывает. Держателя нет — состояние пустое.

    Держатель события и держатель карточки сравниваются как акторы
    (`actors.same_actor`), а не как строки: `claude`, `agent:claude` и
    `sonnet-judge` — один и тот же держатель, поэтому claim под одним написанием
    и heartbeat под другим больше не выглядят как два разных агента. Сравнить по
    актору в SQL нельзя, поэтому события задачи выбираются по `kind` и
    фильтруются в Python — их на карточке заведомо немного.
    """
    out = {"assigned_by": None, "assigned_at": None, "assigned_hours": None,
           "taken": False, "taken_at": None}
    h = (holder or "").strip()
    if not h:
        return out
    rows = conn.execute(
        "SELECT id, ts, kind, from_value, to_value, actor, harness FROM events "
        "WHERE task_id = ? AND kind IN ('claim','heartbeat') ORDER BY ts DESC, id DESC",
        (task_id,)).fetchall()
    mine = [r for r in rows if actors_mod.same_actor(r["to_value"], h, conn)]

    def _is_assignment(r: sqlite3.Row) -> bool:
        if r["kind"] == "claim":
            return True
        # Heartbeat-перехват самим держателем — тоже назначение: держатель забрал
        # карточку у другого актора своей рукой (`docs/API.md`: heartbeat от самого
        # держателя подтверждает, что карточка взята). Обычный heartbeat держателя
        # (пустой `from_value`) назначением не является — иначе он подменял бы
        # «кто выдал» на самого держателя.
        if not (r["from_value"] or "").strip():
            return False
        if actors_mod.same_actor(r["from_value"], r["to_value"], conn):
            return False
        return actors_mod.same_actor(r["actor"] or r["harness"], h, conn)

    assign = next((r for r in mine if _is_assignment(r)), None)
    if assign is None:
        return out
    by = assign["actor"] or assign["harness"]
    out["assigned_by"] = actors_mod.resolve(by, conn)[0] if by else None
    out["assigned_at"] = assign["ts"]
    out["assigned_hours"] = hours_since(assign["ts"])
    for r in sorted((r for r in mine if r["id"] >= assign["id"]), key=lambda r: r["id"]):
        if actors_mod.same_actor(r["actor"] or r["harness"], h, conn):
            out["taken"] = True
            out["taken_at"] = r["ts"]
            break
    return out


def worked_by_actors(conn: sqlite3.Connection, task_id: str) -> list[str]:
    """Кто подтвердил работу по карточке своим `claim`/`heartbeat`.

    У закрытой карточки не видно, кто её вёл: закрытие (`listik done`, `stage --to
    done`) снимает держателя, а `assignee` помнит только первый claim. Поле
    считается по событиям: событие «своё», если его автор (`actor`, иначе
    `harness`) тождествен держателю события (`to_value`) как актор
    (`actors.same_actor`). Выдача оркестратором (`stage --holder кому`) и
    heartbeat чужой рукой автору не тождественны и в список не идут; события
    `stage`/`release`/`comment`/`status`/`note`, `assignee` и текущий держатель
    без своего события — тоже.

    Порядок — по первому своему событию каждого актора, написание — всегда
    канонический ключ (`agent:claude`), даже если событие было от голого
    `claude`. Один запрос без `LIMIT`: среза `events[]` из `get_task` (последние
    100) длинной карточке не хватает — claim там может быть старше.
    """
    rows = conn.execute(
        "SELECT to_value, actor, harness FROM events "
        "WHERE task_id = ? AND kind IN ('claim','heartbeat') ORDER BY ts ASC, id ASC",
        (task_id,)).fetchall()
    out: list[str] = []
    for r in rows:
        author = (r["actor"] or r["harness"] or "").strip()
        target = (r["to_value"] or "").strip()
        if not author or not target:
            continue
        if not actors_mod.same_actor(author, target, conn):
            continue
        key = actors_mod.resolve(author, conn)[0]
        if key and key not in out:
            out.append(key)
    return out


def _open_children_activity(conn: sqlite3.Connection, task_id: str) -> tuple[datetime | None, str | None]:
    """Самая свежая метка простоя среди открытых прямых детей задачи.

    Ребёнок — строка `deps(dep_type='parent-child', depends_on=task_id)`, его
    метка — `holder_at`, иначе `started_at`; ребёнок без обеих меток ничего не
    даёт. Смотрятся только прямые дети (один нерекурсивный запрос; внуки и
    другие типы связей не важны), статус ребёнка — из `OPEN_STATUSES`.
    Возвращает метку и как `datetime` в UTC, и как исходную строку.
    """
    placeholders = ", ".join("?" for _ in OPEN_STATUSES)
    rows = conn.execute(
        "SELECT t.holder_at, t.started_at FROM deps d JOIN tasks t ON t.id = d.issue_id "
        f"WHERE d.dep_type = 'parent-child' AND d.depends_on = ? "
        f"AND t.status IN ({placeholders})",
        (task_id, *OPEN_STATUSES)).fetchall()
    latest_ts: datetime | None = None
    latest_label: str | None = None
    for r in rows:
        label = r["holder_at"] or r["started_at"]
        ts = parse_ts(label)
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if latest_ts is None or ts > latest_ts:
            latest_ts, latest_label = ts, label
    return latest_ts, latest_label


def _last_release_ts(conn: sqlite3.Connection, task_id: str) -> str | None:
    """Метка последнего события `release` задачи — начало льготного окна.

    `release` пишут все штатные снятия держателя: handoff без `--holder`,
    `listik release` и истечение окна возврата (`deps.expire_return_handoffs`).
    Возвращается сырая строка `ts` (даже если её не разобрать) или `None`, если
    держателя у карточки никогда не снимали (импорт, ручной `set status`).
    """
    r = conn.execute(
        "SELECT ts FROM events WHERE task_id = ? AND kind = 'release' "
        "ORDER BY ts DESC, id DESC LIMIT 1", (task_id,)).fetchone()
    return r["ts"] if r else None


def _portion_flags(conn: sqlite3.Connection, task_id: str,
                   stage: str | None) -> tuple[bool, bool]:
    """`(has_portions, portions_cancelled_only)` — вычисляемые поля нарезки.

    `has_portions` — есть хотя бы один не отменённый ребёнок `parent-child`
    (закрытые `done` тоже считаются: до закрытия родителя они ещё часть
    нарезки). `portions_cancelled_only` — дети есть, каждый `cancelled`, и
    этап родителя ещё пустой или `s1-spec` (docs/specs/swarm-stage-launch.md).
    """
    try:
        statuses = [r["status"] for r in conn.execute(
            "SELECT t.status FROM deps d JOIN tasks t ON t.id = d.issue_id "
            "WHERE d.depends_on = ? AND d.dep_type IN ('parent-child','parent')",
            (task_id,)).fetchall()]
    except sqlite3.OperationalError:
        return False, False
    has = any(status != "cancelled" for status in statuses)
    cancelled_only = (bool(statuses)
                      and all(status == "cancelled" for status in statuses)
                      and (stage or "").strip() in ("", "s1-spec"))
    return has, cancelled_only


def row_to_task(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    cfg = config_mod.load()
    warn = float((cfg.get("board") or {}).get("wip_warn_hours", 8))
    stale_h = float((cfg.get("board") or {}).get("stale_hours", 24))
    assign_warn_min = float((cfg.get("board") or {}).get("assign_warn_minutes", 15))
    labels = store_helpers.json_list(row["labels"])
    blockers = store_helpers.json_list(row["blocked_by"])
    stage_hours = hours_since(row["stage_at"]) if row["stage_at"] else None
    holder_hours = hours_since(row["holder_at"]) if row["holder_at"] else None
    open_now = row["status"] in OPEN_STATUSES
    # Задача считается идущей, если статус «в работе» или «на проверке».
    # Открытая (никем не взятая) и заблокированная — это очередь, а не движение,
    # поэтому в «брошенные» они не попадают: иначе весь бэклог висит в линии «нужен ты».
    running = row["status"] in ("in_progress", "review")
    # «в работе», но держателя нет — типичный след брошенной задачи (и всех
    # импортированных: там статус ставили руками и не снимали).
    orphan = bool(running and not row["holder"])
    missing_heartbeat = bool(running and row["holder"] and holder_hours is None)
    idle_hours = holder_hours if holder_hours is not None else (
        hours_since(row["started_at"]) if running else None)
    idle_age = human_age(row["holder_at"] or row["started_at"] or row["updated_at"])
    # Открытая карточка «молчит» только пока молчат её открытые прямые дети:
    # свежая метка ребёнка сдвигает её простой вперёд. Детей слушают лишь тогда,
    # когда у самой карточки есть своя метка простоя (`idle_hours is not None`),
    # — закрытая или открытая без метки считается ровно как раньше (docs/API.md).
    if open_now and idle_hours is not None:
        own_ts = parse_ts(row["holder_at"]) or (parse_ts(row["started_at"]) if running else None)
        if own_ts is not None and own_ts.tzinfo is None:
            own_ts = own_ts.replace(tzinfo=timezone.utc)
        child_ts, child_label = _open_children_activity(conn, row["id"])
        if own_ts is not None and child_ts is not None and child_ts > own_ts:
            idle_hours = hours_since(child_label)
            idle_age = human_age(child_label)
    stale = bool(running and not orphan and idle_hours is not None and idle_hours > stale_h)
    # Держателя снимают и штатно: handoff без `--holder`, `release`, истечение окна
    # возврата. Такой карточке дают ту же фору `board.assign_warn_minutes`, что и
    # выданной, но не взятой, — пока фора идёт, карточка не брошена. Событие ищем
    # только у карточки без держателя: `row_to_task` зовут на каждую карточку доски.
    released_at = _last_release_ts(conn, row["id"]) if orphan else None
    released_hours = hours_since(released_at) if released_at else None
    # Метку не разобрать — окно считается истёкшим, а не сравнивается None с числом.
    in_release_grace = bool(released_at and released_hours is not None
                            and released_hours <= assign_warn_min / 60.0)
    abandoned = (orphan and not in_release_grace) or missing_heartbeat
    has_portions, portions_cancelled_only = _portion_flags(
        conn, row["id"], row["stage"])
    # «Выдана, но не взята»: держателя поставил оркестратор (`stage --holder`), а
    # сам агент ещё не записал ни claim, ни heartbeat от своего имени. Порог
    # `board.assign_warn_minutes` — когда его прошли, карточка идёт в «нужен ты»:
    # так брошенный прогон видно, не дожидаясь `stale_hours`.
    claim_state = holder_claim_state(conn, row["id"], row["holder"])
    assigned_hours = claim_state["assigned_hours"]
    not_taken = bool((row["holder"] or "").strip()) and not claim_state["taken"]
    # «Кто выполнял»: акторы, подтвердившие работу своим claim/heartbeat. Считается
    # у карточки любого статуса, но нужнее всего закрытой — у неё держателя может
    # уже не быть (`stage --to done` его снимает).
    worked_by = worked_by_actors(conn, row["id"])
    return {
        "id": row["id"],
        "project": row["project"],
        "title": row["title"],
        "description": row["description"],
        "acceptance": row["acceptance"],
        "design": row["design"],
        "notes": row["notes"],
        "result": row["result"],
        "status": row["status"],
        "status_title": STATUS_TITLES.get(row["status"], row["status"]),
        "stage": row["stage"],
        "stage_title": STAGE_TITLES.get(row["stage"] or "", row["stage"]),
        "priority": row["priority"],
        "priority_title": PRIORITY_TITLES.get(row["priority"], str(row["priority"])),
        "issue_type": row["issue_type"],
        "assignee": row["assignee"],
        "assignee_title": actors_mod.display(row["assignee"]),
        # Владелец-человек: в локальном режиме всегда null — там его не пишут.
        "owner": row["owner"],
        "holder": row["holder"],
        "holder_title": actors_mod.display(row["holder"]),
        "holder_note": row["holder_note"],
        "holder_at": row["holder_at"],
        "holder_age": human_age(row["holder_at"]),
        "holder_hours": holder_hours,
        # Кто поставил держателя и подтвердил ли он работу сам: доска показывает
        # «выдана, не взята N» вместо «держит N», пока агент не сделал claim.
        "holder_taken": claim_state["taken"],
        "holder_assigned_by": claim_state["assigned_by"],
        "holder_assigned_by_title": (actors_mod.display(claim_state["assigned_by"])
                                     if claim_state["assigned_by"] else None),
        "assigned_at": claim_state["assigned_at"],
        "assigned_age": human_age(claim_state["assigned_at"]),
        "assigned_hours": assigned_hours,
        # Кто подтвердил работу сам (см. `worked_by_actors`): у закрытой карточки
        # доска и `show` показывают это вместо «без держателя».
        "worked_by": worked_by,
        "worked_by_title": ", ".join(actors_mod.display(k) for k in worked_by),
        "not_taken": not_taken,
        "not_taken_warn": bool(not_taken and assigned_hours is not None
                               and assigned_hours > assign_warn_min / 60.0),
        # сколько задача стоит без движения: от последнего heartbeat, иначе —
        # от начала работы; у открытой карточки — от свежайшей метки её открытых
        # прямых детей, если она новее собственной
        "idle_hours": idle_hours,
        "idle_age": idle_age,
        "stage_at": row["stage_at"],
        "stage_age": human_age(row["stage_at"]),
        "stage_hours": stage_hours,
        "stage_warn": bool(stage_hours is not None and stage_hours > warn),
        "needs_owner": bool(row["needs_owner"]),
        "labels": labels,
        "spec_path": row["spec_path"],
        "checklist_path": row["checklist_path"] if "checklist_path" in row.keys() else None,
        "review_path": row["review_path"] if "review_path" in row.keys() else None,
        "decision_path": row["decision_path"] if "decision_path" in row.keys() else None,
        "journal_path": row["journal_path"],
        "worktree": row["worktree"],
        "branch": row["branch"],
        # Автостарт: восемь колонок запуска пишут только create_task и launcher,
        # PATCH их не меняет; маршрут (`launch_route`) — исключение, но лишь пока
        # работа не началась, и это видно доске по `route_editable`.
        "autostart": bool(row["autostart"]),
        "launch_route": row["launch_route"],
        "route_editable": route_change_denied(row) is None,
        "launched_by": row["launched_by"],
        "launch_pid": row["launch_pid"],
        "launched_at": row["launched_at"],
        "launch_log": row["launch_log"],
        "launch_exit_code": row["launch_exit_code"],
        "launch_finished_at": row["launch_finished_at"],
        "launch_error": row["launch_error"],
        "launch_driver": (row["launch_driver"]
                          if "launch_driver" in row.keys() else None),
        "has_portions": has_portions,
        "portions_cancelled_only": portions_cancelled_only,
        # Рой (listik-s520): области файлов и ограждение запуска.
        "read_scope": store_helpers.json_list(row["read_scope"]),
        "write_scope": store_helpers.json_list(row["write_scope"]),
        "dispatch_id": row["dispatch_id"],
        "generation": int(row["generation"] or 0),
        "blocked_by": blockers,
        "source": row["source"],
        "external_ref": row["external_ref"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "updated_at": row["updated_at"],
        "updated_age": human_age(row["updated_at"]),
        "started_at": row["started_at"],
        "closed_at": row["closed_at"],
        "close_reason": row["close_reason"],
        "archived": bool(row["archived"]),
        "stale": stale,
        "abandoned": abandoned,
        # метка, от которой идёт льготное окно «в работе без держателя»;
        # у карточки с держателем и у закрытой — null
        "released_at": released_at,
    }


# ------------------------------------------------------------------ карточки-порции

# Поля соседней карточки, которых достаточно для холодного старта без её `show`:
# что это за карточка, кто её держит и где её документы.
_CARD_LINK_KEYS = ("id", "project", "title", "status", "status_title", "stage", "stage_title",
                   "priority", "priority_title", "holder", "holder_title",
                   "spec_path", "checklist_path", "review_path", "decision_path",
                   "created_at", "updated_at")


def task_documents(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    """Метаданные индексированных документов задачи (без чанков) — то же, что `show`."""
    try:
        return store_helpers.dict_rows(conn.execute(
            "SELECT id, kind, path, revision, content_hash, title, updated_at, status, error, source, "
            "(SELECT count(*) FROM document_chunks WHERE document_chunks.document_id = documents.id) "
            "AS chunk_count FROM documents WHERE task_id=? ORDER BY kind, path",
            (task_id,)))
    except sqlite3.OperationalError:
        return []


def card_link(conn: sqlite3.Connection, task_id: str) -> dict | None:
    """Короткая справка о карточке вместе с её документами.

    Возрастных полей (`_age`/`_hours`) здесь нет намеренно: `card_link` попадает
    в ответ `context`, который обязан быть побайтно стабильным (см.
    `documents.context`)."""
    row = store_helpers.task_row(conn, task_id, required=False)
    if row is None:
        return None
    task = row_to_task(conn, row)
    out = {k: task[k] for k in _CARD_LINK_KEYS}
    out["documents"] = task_documents(conn, task_id)
    return out


def child_cards(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    """Все дочерние карточки (`parent-child`), включая закрытые, по порядку создания.

    Именно так родитель-шаг видит все свои порции: `deps_state.children_open`
    перечисляет только незакрытых детей и нужен для `can_finish`."""
    try:
        rows = conn.execute(
            "SELECT d.issue_id AS id FROM deps d JOIN tasks t ON t.id = d.issue_id "
            "WHERE d.depends_on = ? AND d.dep_type = 'parent-child' "
            "ORDER BY t.created_at, t.rowid", (task_id,)).fetchall()
    except sqlite3.OperationalError:
        # База старой версии/битая: карточка всё равно должна открыться.
        return []
    out = []
    for r in rows:
        link = card_link(conn, r["id"])
        if link is not None:
            out.append(link)
    return out


def parent_card(conn: sqlite3.Connection, task_id: str) -> dict | None:
    """Родительская карточка (связь `parent-child`, либо старое имя `parent`)."""
    try:
        row = conn.execute(
            "SELECT depends_on FROM deps WHERE issue_id = ? "
            "AND dep_type IN ('parent-child','parent') ORDER BY depends_on LIMIT 1",
            (task_id,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return card_link(conn, row["depends_on"]) if row else None


def list_tasks(conn: sqlite3.Connection, *, project: str | None = None, status: str | None = None,
               stage: str | None = None, assignee: str | None = None, holder: str | None = None,
               needs_owner: bool = False, issue_type: str | None = None, label: str | None = None,
               text: str | None = None, include_closed: bool = False, include_archived: bool = False,
               limit: int = 200, offset: int = 0, order: str = "updated",
               as_owner: str | None = None) -> dict:
    where, params = [], []
    owner_cfg = server_cfg()
    if owner_cfg is not None:
        # Свои задачи и общий пул: без этого карточки без владельца (все
        # существующие) пропали бы у каждого, кто представился.
        owner_value = config_mod.check_owner(as_owner, owner_cfg)
        if owner_value:
            where.append("(owner = ? OR owner IS NULL)")
            params.append(owner_value)
    if project:
        where.append("project = ?")
        params.append(project)
    if status:
        where.append("status = ?")
        params.append(status)
    elif not include_closed:
        where.append("status IN ('open','in_progress','blocked','review')")
    if stage:
        where.append("stage = ?")
        params.append(stage)
    if assignee:
        where.append("assignee = ?")
        params.append(assignee)
    if holder:
        where.append("holder = ?")
        params.append(holder)
    if needs_owner:
        where.append("needs_owner = 1")
    if issue_type:
        where.append("issue_type = ?")
        params.append(issue_type)
    if label:
        where.append("labels LIKE ?")
        params.append(f'%"{label}"%')
    if text:
        where.append("(title LIKE ? OR description LIKE ?)")
        params.extend([f"%{text}%", f"%{text}%"])
    if not include_archived:
        where.append("archived = 0")
    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    order_sql = {
        "updated": "updated_at DESC",
        "created": "created_at DESC",
        "priority": "priority ASC, updated_at DESC",
        "stage": "stage_at ASC",
    }.get(order, "updated_at DESC")
    total = conn.execute(f"SELECT count(*) FROM tasks {sql_where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM tasks {sql_where} ORDER BY {order_sql} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return {"total": total, "limit": limit, "offset": offset,
            "tasks": [row_to_task(conn, r) for r in rows]}


def task_timeline(conn: sqlite3.Connection, limit: int = 100, *,
                  project: str | None = None) -> list[dict]:
    from . import fence as fence_mod
    sql = """SELECT e.ts, e.kind, e.from_value, e.to_value, e.actor, e.harness, e.note,
                    e.duration_s, e.task_id, t.title, t.project, t.stage, t.status
             FROM events e LEFT JOIN tasks t ON t.id = e.task_id
             WHERE e.kind != ?"""
    params: list = [fence_mod.REJECTED_KIND]
    if project:
        # Фильтр по проекту задачи: события без задачи (LEFT JOIN → NULL) тоже
        # отсекаются. Срез по limit идёт после фильтра, а не до.
        sql += " AND t.project = ?"
        params.append(project)
    sql += " ORDER BY e.ts DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["actor_title"] = actors_mod.display(r["actor"])
        d["age"] = human_age(r["ts"])
        out.append(d)
    return out


def board(conn: sqlite3.Connection, *, group_by: str = "status", project: str | None = None,
          include_closed: bool = False, limit_per_column: int = 300,
          ready_limit: int = 15, as_owner: str | None = None) -> dict:
    """Данные для канбан-доски: колонки с задачами.

    С ``project`` доска зовёт ``lint`` (порог — дефолт): у каждой карточки поле
    ``lint`` — отсортированные коды её находок, в корне ``lint{count, items}``;
    карточка с непустым ``lint`` попадает в ``needs_you`` (в конец ленты). Без
    ``project`` (доска по всем проектам) lint не вызывается: ``lint`` пуст везде.
    Ошибка lint доску не роняет — тогда тоже пусто.
    """
    where, params = [], []
    owner_cfg = server_cfg()
    if owner_cfg is not None:
        owner_value = config_mod.check_owner(as_owner, owner_cfg)
        if owner_value:
            where.append("(owner = ? OR owner IS NULL)")
            params.append(owner_value)
    if project:
        where.append("project = ?")
        params.append(project)
    if not include_closed:
        where.append("status IN ('open','in_progress','blocked','review')")
    where.append("archived = 0")
    sql_where = "WHERE " + " AND ".join(where)
    rows = conn.execute(
        f"SELECT * FROM tasks {sql_where} ORDER BY priority ASC, updated_at DESC", params
    ).fetchall()
    tasks = [row_to_task(conn, r) for r in rows]

    lint_result: dict = {"count": 0, "items": []}
    if project:
        try:
            found = lint(conn, project)
            lint_result = {"count": found["count"], "items": found["items"]}
        except Exception:  # noqa: BLE001 — доска не должна падать из-за lint
            lint_result = {"count": 0, "items": []}
    lint_by_id: dict[str, list[str]] = {}
    for item in lint_result["items"]:
        lint_by_id.setdefault(item["id"], []).append(item["rule"])
    for t in tasks:
        t["lint"] = sorted(lint_by_id.get(t["id"], []))

    columns: dict[str, dict] = {}
    if group_by == "stage":
        keys = [(s, STAGE_TITLES.get(s, s)) for s in PIPELINE_STAGES]
        keys.append(("none", "без этапа"))
        keys.append(("done", "завершённые"))
        for key, title in keys:
            columns[key] = {"key": key, "title": title, "tasks": []}
        for t in tasks:
            key = t["stage"] if t["stage"] in PIPELINE_STAGES else (
                "done" if t["status"] in FINAL_STATUSES else "none")
            columns[key]["tasks"].append(t)
    elif group_by == "project":
        for t in tasks:
            key = t["project"] or "без проекта"
            col = columns.setdefault(key, {"key": key, "title": key, "tasks": []})
            col["tasks"].append(t)
    elif group_by == "holder":
        for t in tasks:
            key = t["holder"] or "—"
            title = actors_mod.display(t["holder"]) if t["holder"] else "никто не держит"
            col = columns.setdefault(key, {"key": key, "title": title, "tasks": []})
            col["tasks"].append(t)
    else:
        order = ["in_progress", "review", "open", "blocked"]
        if include_closed:
            order += list(FINAL_STATUSES)
        for key in order:
            columns[key] = {"key": key, "title": STATUS_TITLES.get(key, key), "tasks": []}
        for t in tasks:
            key = t["status"] if t["status"] in columns else "open"
            columns[key]["tasks"].append(t)

    for col in columns.values():
        if col["key"] == "done":
            # «Готово» — не активная работа: тут важна свежесть закрытия, а не
            # приоритет, иначе после среза limit_per_column в колонке остаются
            # старые высокоприоритетные задачи вместо недавно закрытых (доска
            # и рельса «Готово · 7 дн» тогда расходятся в числах).
            col["tasks"].sort(key=lambda t: t["updated_at"] or "", reverse=True)
        else:
            col["tasks"].sort(key=lambda t: (not t["needs_owner"], t["priority"], t["updated_at"] or ""))
        col["count"] = len(col["tasks"])
        col["wip"] = sum(1 for t in col["tasks"] if t["status"] == "in_progress")
        col["needs_owner"] = sum(1 for t in col["tasks"] if t["needs_owner"])
        col["stale"] = sum(1 for t in col["tasks"] if t["stale"])
        col["not_taken"] = sum(1 for t in col["tasks"] if t["not_taken_warn"])
        col["tasks"] = col["tasks"][:limit_per_column]

    # «Выдана, но не взята» дольше порога — тот же сигнал «нужен ты», что и
    # брошенная: оркестратор выдал работу, а агент не запустился.
    def _urgent(t: dict) -> bool:
        return bool(t["needs_owner"] or t["stale"] or t["abandoned"] or t["not_taken_warn"])

    needs_you = [t for t in tasks if _urgent(t) or t["lint"]]
    # Самое запущенное — наверх: сначала ждущие человека, потом по времени без движения;
    # попавшие в ленту только из-за lint — в самом конце.
    needs_you.sort(key=lambda t: (not t["needs_owner"], not _urgent(t),
                                  -(t.get("idle_hours") or t.get("assigned_hours") or 0)))

    # Что можно взять прямо сейчас: без незакрытых блокеров и без держателя.
    # Считается по графу зависимостей, поэтому ограничено сверху.
    ready_list: list[dict] = []
    blocked_count = 0
    if ready_limit:
        try:
            ready_list = deps_mod.ready_tasks(conn, project=project, limit=ready_limit,
                                              as_owner=as_owner)
        except Exception:  # noqa: BLE001 — доска не должна падать из-за графа
            ready_list = []
    try:
        statuses = {t["id"]: t["status"] for t in tasks}
        blocked_ids = set()
        for r in conn.execute(
                "SELECT issue_id, depends_on FROM deps WHERE dep_type IN "
                "('blocks','blocked-by','waits-for','conditional-blocks')"):
            if statuses.get(r["depends_on"], "open") not in FINAL_STATUSES:
                blocked_ids.add(r["issue_id"])
        blocked_count = len(blocked_ids & set(statuses))
    except Exception:  # noqa: BLE001
        blocked_count = 0

    return {
        "group_by": group_by,
        "columns": list(columns.values()),
        "total": len(tasks),
        "needs_you": needs_you[:100],
        "ready": ready_list,
        "blocked_count": blocked_count,
        "cycles": deps_mod.cycles(conn) if ready_limit else [],
        "lint": lint_result,
        "generated_at": now_iso(),
    }


def stats(conn: sqlite3.Connection, project: str | None = None) -> dict:
    where = "WHERE archived = 0"
    params: list = []
    if project:
        where += " AND project = ?"
        params.append(project)
    by_status = {r["status"]: r["n"] for r in conn.execute(
        f"SELECT status, count(*) n FROM tasks {where} GROUP BY status", params)}
    by_stage = {r["stage"] or "none": r["n"] for r in conn.execute(
        f"SELECT stage, count(*) n FROM tasks {where} AND status IN "
        "('open','in_progress','blocked','review') GROUP BY stage", params)}
    by_project_rows = list(conn.execute(
        f"SELECT coalesce(project,'—') project, count(*) n, "
        f"sum(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) wip, "
        f"sum(CASE WHEN needs_owner=1 THEN 1 ELSE 0 END) waiting "
        f"FROM tasks {where} AND status IN ('open','in_progress','blocked','review') "
        "GROUP BY project ORDER BY n DESC", params))
    by_holder = list(conn.execute(
        f"SELECT coalesce(holder,'—') holder, count(*) n FROM tasks {where} AND "
        "status IN ('open','in_progress','blocked','review') GROUP BY holder ORDER BY n DESC",
        params))
    by_actor = list(conn.execute(
        f"SELECT coalesce(assignee,'—') actor, count(*) n FROM tasks {where} AND "
        "status IN ('open','in_progress','blocked','review') GROUP BY assignee ORDER BY n DESC",
        params))
    stale = conn.execute(
        f"SELECT count(*) FROM tasks {where} AND status IN ('in_progress','review') "
        "AND ((holder IS NULL AND (started_at IS NULL OR "
        "      julianday('now') - julianday(started_at) > 1)) "
        "  OR (holder IS NOT NULL AND holder_at IS NOT NULL "
        "      AND julianday('now') - julianday(holder_at) > 1))", params).fetchone()[0]
    closed_7d = conn.execute(
        f"SELECT count(*) FROM tasks {where} AND status = 'done' AND closed_at IS NOT NULL "
        "AND julianday('now') - julianday(closed_at) <= 7", params).fetchone()[0]
    closed_prev_7d = conn.execute(
        f"SELECT count(*) FROM tasks {where} AND status = 'done' AND closed_at IS NOT NULL "
        "AND julianday('now') - julianday(closed_at) > 7 "
        "AND julianday('now') - julianday(closed_at) <= 14", params).fetchone()[0]
    # Закрытия по дням за 14 суток (старые → свежие) — спарклайн «Закрыто за 7 дней».
    closed_days = {r["d"]: r["n"] for r in conn.execute(
        f"SELECT date(closed_at) d, count(*) n FROM tasks {where} AND status = 'done' "
        "AND closed_at IS NOT NULL AND date(closed_at) > date('now', '-14 days') "
        "GROUP BY d", params)}
    today = datetime.now(timezone.utc).date()
    closed_by_day = [
        {"date": (d := (today - timedelta(days=offset)).isoformat()), "count": closed_days.get(d, 0)}
        for offset in range(13, -1, -1)]
    long_stage = conn.execute(
        f"SELECT count(*) FROM tasks {where} AND status IN ('in_progress','review') "
        "AND stage_at IS NOT NULL AND julianday('now') - julianday(stage_at) > 0.33",
        params).fetchone()[0]
    needs_owner = conn.execute(
        f"SELECT count(*) FROM tasks {where} AND needs_owner = 1", params).fetchone()[0]
    running = list(conn.execute(
        f"SELECT * FROM tasks {where} AND status = 'in_progress' "
        "ORDER BY stage_at ASC", params))
    return {
        "by_status": by_status,
        "by_stage": by_stage,
        "by_project": [
            {"project": r["project"], "total": r["n"], "in_progress": r["wip"],
             "waiting": r["waiting"]} for r in by_project_rows],
        "by_holder": [{"holder": r["holder"], "title": actors_mod.display(
            None if r["holder"] == "—" else r["holder"]), "count": r["n"]} for r in by_holder],
        "by_actor": [{"actor": r["actor"], "title": actors_mod.display(
            None if r["actor"] == "—" else r["actor"]), "count": r["n"]} for r in by_actor],
        "stale": stale,
        "needs_owner": needs_owner,
        "closed_7d": closed_7d,
        "closed_prev_7d": closed_prev_7d,
        "closed_delta": closed_7d - closed_prev_7d,
        "closed_by_day": closed_by_day,
        "long_stage": long_stage,
        "running": [row_to_task(conn, r) for r in running],
        "generated_at": now_iso(),
    }


# ------------------------------------------------------------------ проекты, акторы, связи

def existing_slug(conn: sqlite3.Connection, slug: str) -> str | None:
    """Slug существующего проекта, совпадающий с `slug` без учёта регистра.

    Slug не приводится к нижнему регистру (есть `Zoloto585/orders`), но проекты,
    отличающиеся только регистром, — дубли: файловая система macOS регистр не различает.
    """
    exact = conn.execute("SELECT slug FROM projects WHERE slug = ?", (slug,)).fetchone()
    if exact:
        return exact[0]
    key = slug.casefold()
    for (s,) in conn.execute("SELECT slug FROM projects"):
        if s.casefold() == key:
            return s
    return None


def project_path(conn: sqlite3.Connection, slug: str | None) -> str:
    """`projects.path` проекта или `''` — если пути (или самого проекта) нет."""
    if not slug:
        return ""
    row = conn.execute("SELECT path FROM projects WHERE slug = ?", (slug,)).fetchone()
    return str((row["path"] if row else "") or "").strip()


def upsert_project(conn: sqlite3.Connection, slug: str, **fields) -> dict:
    allowed = {"title", "kind", "path", "git_remote", "git_branch", "color", "archived",
               "imported_at", "import_note", "routing"}
    slug = existing_slug(conn, slug) or slug
    conn.execute(
        "INSERT INTO projects(slug, title, kind) VALUES(?,?,?) "
        "ON CONFLICT(slug) DO UPDATE SET title=coalesce(excluded.title, projects.title)",
        (slug, fields.get("title") or slug, fields.get("kind", "native")),
    )
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k} = ?")
            params.append(v)
    if sets:
        params.append(slug)
        conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE slug = ?", params)
    conn.commit()
    return dict(conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone())


def _project_with_routing(conn: sqlite3.Connection, row: dict) -> dict:
    """Дописать к строке проекта действующую маршрутизацию: `routing` (переопределение
    из базы, распарсенное, или None), `routing_effective` (слитый словарь: то, чем
    реально пользуется `transition_kind`) и `routing_source` —
    откуда взято переопределение (`default`/`config`/`db`/`config+db`).

    Устаревшие ключи (`config.LEGACY_ROUTING_KEYS`) не показываем и за
    переопределение не считаем: проект, у которого сохранён только такой ключ,
    равнозначен проекту без настроек."""
    slug = row.get("slug")
    raw = row.get("routing")
    parsed = None
    if raw:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            parsed = None
    out = dict(row)
    cleaned = config_mod.without_legacy_routing(parsed) if isinstance(parsed, dict) else None
    out["routing"] = cleaned or None
    out["routing_effective"] = config_mod.routing(slug, conn=conn)
    has_db = bool(out["routing"])
    has_config = False
    try:
        # config.load() уже вычистил устаревшие ключи (см. config.LEGACY_ROUTING_KEYS):
        # переопределение, в котором остался только такой ключ, сюда приходит пустым.
        cfg = config_mod.load()
        projects_cfg = (cfg.get("routing") or {}).get("projects") or {}
        has_config = bool(slug and isinstance(projects_cfg, dict) and projects_cfg.get(slug))
    except Exception:  # noqa: BLE001 — конфиг не должен ронять показ проекта
        has_config = False
    if has_config and has_db:
        out["routing_source"] = "config+db"
    elif has_db:
        out["routing_source"] = "db"
    elif has_config:
        out["routing_source"] = "config"
    else:
        out["routing_source"] = "default"
    return out


def list_projects(conn: sqlite3.Connection, include_archived: bool = False) -> list[dict]:
    where = "" if include_archived else "WHERE archived = 0"
    rows = conn.execute(
        f"""SELECT p.*,
              (SELECT count(*) FROM tasks t WHERE t.project = p.slug AND t.archived = 0) n_tasks,
              (SELECT count(*) FROM tasks t WHERE t.project = p.slug AND t.archived = 0
                 AND t.status IN ('open','in_progress','blocked','review')) n_open,
              (SELECT count(*) FROM tasks t WHERE t.project = p.slug AND t.archived = 0
                 AND t.status = 'in_progress') n_wip
            FROM projects p {where} ORDER BY n_open DESC, p.slug""").fetchall()
    return [_project_with_routing(conn, dict(r)) for r in rows]


def list_all_projects(conn: sqlite3.Connection) -> list[dict]:
    """Все проекты (включая скрытые) со счётчиками и признаком «каталог ещё жив».

    Для настроек доски: там нужно видеть и скрытые проекты, чтобы вернуть их обратно,
    и понимать, какие каталоги исчезли с диска.
    """
    rows = list_projects(conn, include_archived=True)
    for row in rows:
        row["path_exists"] = bool(row.get("path")) and Path(row["path"]).is_dir()
    return rows


def norm_slug(value: str) -> str:
    """Slug проекта из произвольной строки.

    Буквы (в том числе кириллица) и «/» сохраняются: проекты лежат категориями
    (`Zoloto585/zoloto585-search`), и такие slug'и уже есть в базе с импорта.
    """
    slug = re.sub(r"[^0-9a-zA-Zа-яА-Я_\-/]+", "-", str(value).strip()).strip("-/")
    return re.sub(r"/{2,}", "/", slug)


def _git_value(path: Path, *args: str) -> str | None:
    """Значение из git, если каталог — репозиторий. Ошибки не пробрасываются."""
    try:
        out = subprocess.run(["git", "--no-optional-locks", "-C", str(path), *args],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    value = (out.stdout or "").strip()
    return value or None


def repo_info(path: str | Path) -> dict:
    """Что можно узнать о каталоге-репозитории: абсолютный путь, git remote и ветка.

    Каталог может быть и не git-репозиторием — это не ошибка: путь проекту нужен
    в основном для `init-projects` (прописать правила Listik в AGENTS.md).
    """
    resolved = Path(path).expanduser()
    try:
        resolved = resolved.resolve()
    except OSError:
        pass
    info = {"path": str(resolved), "exists": resolved.is_dir(), "git": False,
            "git_remote": None, "git_branch": None}
    if not info["exists"]:
        return info
    if (resolved / ".git").exists() or _git_value(resolved, "rev-parse", "--git-dir"):
        info["git"] = True
        info["git_remote"] = _git_value(resolved, "remote", "get-url", "origin")
        info["git_branch"] = _git_value(resolved, "rev-parse", "--abbrev-ref", "HEAD")
    return info


def resolve_project_path(path: str | None) -> str | None:
    """Путь проекта от API: `~` раскрывается, относительный — от корня проектов.

    Никогда не от `os.getcwd()`: сервер могли запустить из любого каталога
    (listik-i23u).
    """
    raw = str(path).strip() if path is not None else ""
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path(paths.PROJECTS_ROOT).expanduser() / p
    return str(p)


def project_row(conn: sqlite3.Connection, slug: str) -> dict:
    row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
    if not row:
        raise errors_mod.NotFound(f"проект не найден: {slug}")
    return dict(row)


def add_project(conn: sqlite3.Connection, *, path: str | None = None, slug: str | None = None,
                title: str | None = None, kind: str = "native") -> dict:
    """Добавить репозиторий (каталог) на доску.

    Slug по умолчанию — имя каталога; можно передать свой, чтобы лечь в категорию
    (`Zoloto585/my-repo`). Если проект с таким slug уже есть — он возвращается
    на доску и обновляется, а не падает с ошибкой.

    `path` — абсолютный, от `~` или относительный. Относительный разворачивается
    от корня проектов (`paths.PROJECTS_ROOT`), а не от cwd процесса: у сервера
    cwd случаен (listik-mo3a, listik-i23u). CLI относительный путь разрешает сам,
    в cwd пользователя, до HTTP-запроса.
    """
    path = resolve_project_path(path)
    info = (repo_info(path) if path
            else {"path": None, "exists": False, "git": False,
                  "git_remote": None, "git_branch": None})
    if path and not info["exists"]:
        raise ValueError(f"каталога нет: {info['path']}")
    adjusted_from = None
    if info["git"]:
        top = _git_value(Path(info["path"]), "rev-parse", "--show-toplevel")
        if top:
            top_resolved = str(Path(top).resolve())
            if top_resolved != info["path"]:
                adjusted_from = info["path"]
                info["path"] = top_resolved
    base = slug or (Path(info["path"]).name if info["path"] else "")
    clean = norm_slug(base)
    if not clean:
        raise ValueError("нужен slug проекта или путь к каталогу")
    found = existing_slug(conn, clean)
    existed = found is not None
    clean = found or clean
    project = upsert_project(conn, clean, title=title or Path(clean).name, kind=kind,
                             path=info["path"], git_remote=info["git_remote"],
                             git_branch=info["git_branch"], archived=0)
    project["created"] = not existed
    project["git"] = info["git"]
    project["path_exists"] = info["exists"]
    project["path_adjusted_from"] = adjusted_from
    return project


def update_project(conn: sqlite3.Connection, slug: str, **fields) -> dict:
    """Правка проекта: название, путь, цвет, скрытие с доски (`archived`), маршрутизация.

    `routing`: словарь — валидируется (`config.validate_routing`) и пишется как JSON;
    пустой словарь сбрасывает переопределение (колонка становится NULL); строка
    разбирается как JSON и дальше обрабатывается как словарь (невалидный JSON —
    ``ValueError``).
    """
    project_row(conn, slug)
    if fields.get("path"):
        fields["path"] = resolve_project_path(fields["path"])
    routing_value = fields.pop("routing", None)
    changes: dict[str, object] = {
        k: (int(v) if k == "archived" else v) for k, v in fields.items()
        if v is not None and k in ("title", "path", "git_remote", "git_branch",
                                    "color", "archived", "kind")
    }
    if routing_value is not None:
        parsed = routing_value
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError as exc:
                raise ValueError(f"routing: невалидный JSON: {exc}") from exc
        validated = config_mod.validate_routing(parsed)
        changes["routing"] = json.dumps(validated, ensure_ascii=False) if validated else None
    if changes:
        sets = ", ".join(f"{k} = ?" for k in changes)
        conn.execute(f"UPDATE projects SET {sets} WHERE slug = ?", (*changes.values(), slug))
        conn.commit()
    out = _project_with_routing(conn, project_row(conn, slug))
    out["path_exists"] = bool(out.get("path")) and Path(out["path"]).is_dir()
    return out


def remove_project(conn: sqlite3.Connection, slug: str, *, force: bool = False) -> dict:
    """Убрать проект из Listik.

    Проект с задачами по умолчанию не удаляется: задачи — это история, по которой
    потом ищут. Такой проект скрывают с доски (`archived`), а настоящее удаление —
    только с `force`, и оно уносит задачи вместе с проектом.
    """
    project_row(conn, slug)
    n_tasks = conn.execute("SELECT count(*) FROM tasks WHERE project = ?", (slug,)).fetchone()[0]
    if n_tasks and not force:
        raise ValueError(f"у проекта {slug} {n_tasks} задач — сначала скройте его с доски, "
                         f"либо удалите вместе с задачами (force)")
    removed_tasks = 0
    if n_tasks:
        ids = [r[0] for r in conn.execute("SELECT id FROM tasks WHERE project = ?", (slug,))]
        for task_id in ids:
            conn.execute("DELETE FROM task_fts WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM comment_fts WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM document_chunk_fts WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM embeddings WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM documents WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM comments WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM deps WHERE issue_id = ? OR depends_on = ?",
                         (task_id, task_id))
            conn.execute("DELETE FROM events WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            removed_tasks += 1
    conn.execute("DELETE FROM projects WHERE slug = ?", (slug,))
    conn.commit()
    return {"slug": slug, "removed": True, "removed_tasks": removed_tasks}


def list_actors(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT a.*, (SELECT count(*) FROM tasks t WHERE t.assignee = a.key
                        AND t.status IN ('open','in_progress','blocked','review')) n_tasks,
                         (SELECT count(*) FROM tasks t WHERE t.holder = a.key
                        AND t.status IN ('open','in_progress','blocked','review')) n_held
           FROM actors a ORDER BY n_tasks DESC""").fetchall()
    return store_helpers.dict_rows(rows)


def facet_values(conn: sqlite3.Connection) -> dict:
    out: dict[str, list] = {}
    for name, sql in {
        "projects": "SELECT DISTINCT coalesce(project,'—') v FROM tasks WHERE archived=0 ORDER BY v",
        "assignees": "SELECT DISTINCT coalesce(assignee,'—') v FROM tasks WHERE archived=0 ORDER BY v",
        "holders": "SELECT DISTINCT coalesce(holder,'—') v FROM tasks WHERE archived=0 ORDER BY v",
        "statuses": "SELECT DISTINCT status v FROM tasks ORDER BY v",
        "stages": "SELECT DISTINCT coalesce(stage,'—') v FROM tasks ORDER BY v",
        "types": "SELECT DISTINCT issue_type v FROM tasks ORDER BY v",
    }.items():
        out[name] = [r["v"] for r in conn.execute(sql)]
    out["actors"] = {"key": "actors", "values": store_helpers.dict_rows(conn.execute(
        "SELECT key, title, kind FROM actors ORDER BY key"))}
    return out


def add_dep(conn: sqlite3.Connection, issue_id: str, depends_on: str, dep_type: str = "blocks",
            created_by: str | None = None, confirm: bool = False) -> dict:
    for tid in (issue_id, depends_on):
        if not store_helpers.task_exists(conn, tid):
            raise errors_mod.NotFound(f"задача не найдена: {tid}")
    if issue_id == depends_on:
        raise ValueError(f"связь задачи с самой собой: {issue_id}")
    if dep_type == deps_mod.RESOURCE_BLOCK:
        raise errors_mod.BadArgument(
            "ресурсный блокер ставит только планировщик: тип resource-blocks через dep add не "
            "ставится, смысловую зависимость ставь типом blocks")
    actor_key, actor_kind = actors_mod.resolve(created_by, conn)
    if created_by:
        actors_mod.remember(conn, created_by, actor_key, actor_kind)
    # Правило префикса надёжнее actors.resolve: незнакомое имя вида agent:mcp
    # тот вернёт как kind human, а вызов всё равно от агента.
    is_agent = actor_kind == "agent" or (created_by or "").strip().lower().startswith("agent:")

    requested_dep_type = dep_type
    suggested = False
    confirmed = False
    promoted = False
    created = False

    if dep_type in deps_mod.HARD_BLOCKERS:
        if is_agent and not confirm:
            # Агент может записать гипотезу, но не может сам сделать её жёсткой:
            # это меняло бы готовность задач без участия человека.
            requested_dep_type = dep_type
            actual_type = "suggested-blocks"
            existing_hard = conn.execute(
                "SELECT dep_type FROM deps WHERE issue_id=? AND depends_on=? AND dep_type IN (%s)"
                % ",".join("?" * len(deps_mod.SEMANTIC_HARD)),
                (issue_id, depends_on, *deps_mod.SEMANTIC_HARD),
            ).fetchone()
            if existing_hard:
                # Жёсткая связь важнее предложения — уже подтверждено, ничего не пишем.
                actual_type = existing_hard["dep_type"]
                confirmed = True
            else:
                suggested = True
                exists = conn.execute(
                    "SELECT 1 FROM deps WHERE issue_id=? AND depends_on=? AND dep_type='suggested-blocks'",
                    (issue_id, depends_on),
                ).fetchone()
                created = not exists
                conn.execute(
                    "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) VALUES(?,?,?,?) "
                    "ON CONFLICT(issue_id, depends_on, dep_type) DO NOTHING",
                    (issue_id, depends_on, "suggested-blocks", actor_key or created_by),
                )
            deps_mod.refresh_task(conn, issue_id)
            deps_mod.refresh_task(conn, depends_on)
            conn.commit()
            return {"issue_id": issue_id, "depends_on": depends_on, "dep_type": actual_type,
                    "requested_dep_type": requested_dep_type, "suggested": suggested,
                    "confirmed": confirmed, "promoted": False, "created": created,
                    "created_by": actor_key or created_by}

        # Жёсткая связь: человек, unknown, агент с --confirm.
        already_hard = conn.execute(
            "SELECT dep_type FROM deps WHERE issue_id=? AND depends_on=? AND dep_type IN (%s)"
            % ",".join("?" * len(deps_mod.SEMANTIC_HARD)),
            (issue_id, depends_on, *deps_mod.SEMANTIC_HARD),
        ).fetchone()
        had_suggestion = conn.execute(
            "SELECT 1 FROM deps WHERE issue_id=? AND depends_on=? AND dep_type='suggested-blocks'",
            (issue_id, depends_on),
        ).fetchone()
        if not already_hard:
            # Проверка цикла: есть ли путь от depends_on обратно к issue_id по жёстким рёбрам.
            parents: dict[str, str | None] = {depends_on: None}
            frontier = [depends_on]
            cycle_path: list[str] | None = None
            marks = ",".join("?" * len(deps_mod.SEMANTIC_HARD))
            while frontier and cycle_path is None:
                cur = frontier.pop()
                for r in conn.execute(
                    f"SELECT depends_on FROM deps WHERE issue_id = ? AND dep_type IN ({marks})",
                    (cur, *deps_mod.SEMANTIC_HARD),
                ):
                    nxt = r["depends_on"]
                    if nxt == issue_id:
                        # cur замыкается на issue_id — восстанавливаем путь от
                        # depends_on до cur, добавляем issue_id с обеих сторон.
                        chain = [cur]
                        node = cur
                        while parents.get(node) is not None:
                            node = parents[node]
                            chain.append(node)
                        chain.reverse()
                        cycle_path = [issue_id] + chain + [issue_id]
                        break
                    if nxt not in parents:
                        parents[nxt] = cur
                        frontier.append(nxt)
            if cycle_path is not None:
                raise ValueError(
                    "жёсткая зависимость создаёт цикл: " + " → ".join(cycle_path))
            conn.execute(
                "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) VALUES(?,?,?,?) "
                "ON CONFLICT(issue_id, depends_on, dep_type) DO NOTHING",
                (issue_id, depends_on, dep_type, actor_key or created_by),
            )
            created = True
            promoted = bool(had_suggestion)
            if had_suggestion:
                conn.execute(
                    "DELETE FROM deps WHERE issue_id=? AND depends_on=? AND dep_type='suggested-blocks'",
                    (issue_id, depends_on),
                )
            actual_type = dep_type
        else:
            actual_type = already_hard["dep_type"]
            created = False
            if had_suggestion:
                conn.execute(
                    "DELETE FROM deps WHERE issue_id=? AND depends_on=? AND dep_type='suggested-blocks'",
                    (issue_id, depends_on),
                )
        confirmed = True
        deps_mod.refresh_task(conn, issue_id)
        deps_mod.refresh_task(conn, depends_on)
        conn.commit()
        return {"issue_id": issue_id, "depends_on": depends_on, "dep_type": actual_type,
                "requested_dep_type": requested_dep_type, "suggested": False,
                "confirmed": confirmed, "promoted": promoted, "created": created,
                "created_by": actor_key or created_by}

    # Мягкие типы: пишутся как раньше, без проверок предложений/циклов.
    exists = conn.execute(
        "SELECT 1 FROM deps WHERE issue_id=? AND depends_on=? AND dep_type=?",
        (issue_id, depends_on, dep_type),
    ).fetchone()
    conn.execute(
        "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) VALUES(?,?,?,?) "
        "ON CONFLICT(issue_id, depends_on, dep_type) DO NOTHING",
        (issue_id, depends_on, dep_type, actor_key or created_by),
    )
    deps_mod.refresh_task(conn, issue_id)
    deps_mod.refresh_task(conn, depends_on)
    conn.commit()
    return {"issue_id": issue_id, "depends_on": depends_on, "dep_type": dep_type,
            "requested_dep_type": dep_type, "suggested": False, "confirmed": False,
            "promoted": False, "created": not exists, "created_by": actor_key or created_by}


def remove_dep(conn: sqlite3.Connection, issue_id: str, depends_on: str,
               dep_type: str | None = None) -> dict:
    if dep_type is None:
        types = ("blocks", "suggested-blocks")
        rows = conn.execute(
            "SELECT dep_type FROM deps WHERE issue_id=? AND depends_on=? AND dep_type IN (?,?)",
            (issue_id, depends_on, *types),
        ).fetchall()
        removed_types = [r["dep_type"] for r in rows]
        conn.execute(
            "DELETE FROM deps WHERE issue_id=? AND depends_on=? AND dep_type IN (?,?)",
            (issue_id, depends_on, *types),
        )
    else:
        rows = conn.execute(
            "SELECT dep_type FROM deps WHERE issue_id=? AND depends_on=? AND dep_type=?",
            (issue_id, depends_on, dep_type),
        ).fetchall()
        removed_types = [r["dep_type"] for r in rows]
        conn.execute("DELETE FROM deps WHERE issue_id=? AND depends_on=? AND dep_type=?",
                     (issue_id, depends_on, dep_type))
    deps_mod.refresh_task(conn, issue_id)
    deps_mod.refresh_task(conn, depends_on)
    conn.commit()
    return {"removed": len(removed_types), "dep_types": removed_types}


def recompute_blocked(conn: sqlite3.Connection) -> int:
    """Обновляет денормализованный blocked_by. Статусы задач не переписывает:
    «заблокирована кем-то» — вычисляемое состояние (см. deps.ready)."""
    changed = deps_mod.refresh_blocked_column(conn)
    conn.commit()
    return changed


# ------------------------------------------------------------------ память

def remember(conn: sqlite3.Connection, text: str, *, key: str | None = None,
             project: str | None = None) -> dict:
    """Записать заметку в долговременную память.

    Ключ по умолчанию — `note/<unix-время>`, проект — `personal`. Повторная запись
    с тем же ключом перезаписывает тело заметки и её строку в полнотекстовом индексе.
    """
    if not text or not text.strip():
        raise ValueError("пустой текст заметки")
    key = key or f"note/{int(time.time())}"
    project = project or "personal"
    conn.execute(
        "INSERT INTO memories(key, project, body, source) VALUES(?,?,?, 'native') "
        "ON CONFLICT(key) DO UPDATE SET body=excluded.body, updated_at=?",
        (key, project, text, now_iso()))
    conn.execute("DELETE FROM memory_fts WHERE memory_key = ?", (key,))
    conn.execute("INSERT INTO memory_fts(memory_key, body) VALUES(?,?)", (key, text))
    conn.commit()
    return {"key": key, "project": project}


def _lint_steps_files(cache: dict, steps_dir: str | None) -> list[str]:
    """Имена файлов каталога шагов, один `listdir` на каталог за вызов lint."""
    if not steps_dir:
        return []
    if steps_dir not in cache:
        try:
            cache[steps_dir] = sorted(os.listdir(steps_dir))
        except OSError:
            cache[steps_dir] = []
    return cache[steps_dir]


def lint(conn: sqlite3.Connection, project: str | None, *,
         suggested_hours: float = LINT_SUGGESTED_HOURS) -> dict:
    """Несостыковки карточек проекта (только чтение): держатели, документы, порции, связи."""
    project = (project or "").strip()
    if not project:
        raise errors_mod.BadArgument("укажи проект: --project <slug>")
    try:
        limit_hours = float(suggested_hours)
    except (TypeError, ValueError):
        raise errors_mod.BadArgument("suggested_hours: ожидается число") from None
    if not limit_hours > 0:
        raise errors_mod.BadArgument("suggested_hours: ожидается число больше 0")

    tasks = conn.execute(
        "SELECT id, title, stage, status, holder, spec_path, journal_path FROM tasks "
        "WHERE project = ? AND archived = 0", (project,)).fetchall()
    by_id = {t["id"]: t for t in tasks}
    prow = conn.execute("SELECT path FROM projects WHERE slug = ?", (project,)).fetchone()
    project_steps = (os.path.join(prow["path"], "docs", "specs", "steps")
                     if prow and (prow["path"] or "").strip() else None)
    listing: dict[str, list[str]] = {}
    items: list[dict] = []

    def add(t, rule: str, message: str, details: dict) -> None:
        items.append({"rule": rule, "id": t["id"], "title": t["title"], "stage": t["stage"],
                      "status": t["status"], "message": message, "details": details})

    for t in tasks:
        holder = (t["holder"] or "").strip()
        is_open = t["status"] not in FINAL_STATUSES
        if t["status"] == "in_progress" and not holder:
            add(t, "in_progress_no_holder", "в работе без держателя",
                {"released_at": _last_release_ts(conn, t["id"])})
        if not is_open and holder:
            add(t, "done_with_holder", f"закрыта, но держатель не снят: {t['holder']}",
                {"holder": t["holder"]})
        if not is_open:
            continue
        if t["stage"] in ("s2-review", "s3-impl", "s4-judge"):
            missing = [k for k in ("spec_path", "journal_path") if not (t[k] or "").strip()]
            if missing:
                add(t, "stage_without_docs", f"этап {t['stage']} без {', '.join(missing)}",
                    {"missing": missing})
        children = conn.execute(
            "SELECT t.id, t.status, t.stage FROM deps d JOIN tasks t ON t.id = d.issue_id "
            "WHERE d.depends_on = ? AND d.dep_type = 'parent-child' AND t.archived = 0",
            (t["id"],)).fetchall()
        if not children:
            spec = (t["spec_path"] or "").strip()
            steps_dir = os.path.dirname(spec) if spec else project_steps
            pattern = re.compile(rf"^{re.escape(t['id'])}\.([a-z])\.md$")
            files = [f for f in _lint_steps_files(listing, steps_dir) if pattern.match(f)]
            if files:
                add(t, "portion_files_without_cards",
                    f"файлы порций без карточек: {', '.join(files)}",
                    {"files": files, "steps_dir": steps_dir})
            continue
        parent_stage = t["stage"]
        parent_idx = (PIPELINE_STAGES.index(parent_stage)
                      if parent_stage in PIPELINE_STAGES else -1)
        open_idx = [PIPELINE_STAGES.index(c["stage"]) for c in children
                    if c["status"] not in FINAL_STATUSES and c["stage"] in PIPELINE_STAGES]
        if open_idx and max(open_idx) > parent_idx:
            max_stage = PIPELINE_STAGES[max(open_idx)]
            add(t, "stage_behind_portions",
                f"этап шага {parent_stage or '—'} позади порций ({max_stage})",
                {"parent_stage": parent_stage, "max_child_stage": max_stage})
        elif (all(c["status"] in FINAL_STATUSES for c in children)
              and parent_stage in ("s3-impl", "s4-judge")):
            add(t, "stage_behind_portions", f"все порции закрыты, шаг ещё на {parent_stage}",
                {"parent_stage": parent_stage, "children_done": True})

    for s in deps_mod.suggested(conn, project=project, limit=0):
        t = by_id.get(s["issue_id"])
        hours = hours_since(s["created_at"])
        if t is None or hours is None or not hours > limit_hours:
            continue
        add(t, "suggested_dep_stale",
            f"предложенная связь с {s['depends_on']} ждёт подтверждения {round(hours, 1)} ч",
            {"depends_on": s["depends_on"], "hours": round(hours, 1)})

    items.sort(key=lambda i: (i["rule"], i["id"]))
    return {"project": project, "generated_at": now_iso(), "count": len(items), "items": items}

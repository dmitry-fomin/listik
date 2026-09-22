"""Этапный запуск роя (`driver="swarm"`): расклад этап↔роль, разбор первой
строки вывода харнесса, переходы и порции. Протокол и контракт —
`docs/specs/swarm-stage-launch.md`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import errors as errors_mod
from . import harnesses_store
from . import store

#: Этап → роль в раскладе маршрута. Порядок списка — порядок прохода карточки.
STAGE_ROLES: tuple[tuple[str, str], ...] = (
    ("s1-spec", "spec"),
    ("s2-review", "critic"),
    ("s3-impl", "impl"),
    ("s4-judge", "judge"),
)
STAGE_TO_ROLE = dict(STAGE_ROLES)

#: Первые строки, которые принимает этап. Чужая строка — как пустой ответ.
#: `вопрос`/`не смог` годятся на любом этапе, включая приёмку; `зелёный`/`красный`
#: — только у судьи, `готово` — только не у него.
ANSWERS: dict[str, frozenset[str]] = {
    "spec": frozenset({"готово", "вопрос", "не смог"}),
    "critic": frozenset({"готово", "вопрос", "не смог"}),
    "impl": frozenset({"готово", "вопрос", "не смог"}),
    "judge": frozenset({"вопрос", "не смог", "зелёный", "красный"}),
}

#: Сколько читаем из `.out` (первая строка + хвост вопроса/правок).
OUT_READ_LIMIT = 64 * 1024
#: Хвост stdout в текст вопроса человеку и в тело `VERDICT: FAIL`.
TAIL_LIMIT = 4000
#: Хвост, который можно прицепить второй строкой к «не сдал работу».
FAIL_TAIL_LIMIT = 1000

SWARM_ACTOR = "agent:listik"


# ------------------------------------------------------------------ расклад

def is_swarm(record: dict | None) -> bool:
    """Маршрут едет роем: `driver=swarm` (у `kind=swarm` он такой всегда)."""
    return bool(record) and record.get("driver") == "swarm"


def role_of_stage(stage: str | None) -> str | None:
    """Роль этапа; пустой этап незапущенной карточки — это `s1-spec`."""
    return STAGE_TO_ROLE.get((stage or "").strip() or "s1-spec")


def _cell_argv(cell: dict, harness_record: dict | None) -> list | None:
    """argv роли: `argv` ячейки `kind=swarm`, `command` ячейки конвейера с
    `driver=swarm`, иначе argv харнесса по умолчанию."""
    argv = cell.get("argv") or cell.get("command")
    if not argv and harness_record:
        argv = harness_record.get("argv")
    return argv or None


def _cell_prompt(cell: dict) -> str:
    """Промпт роли — последний аргумент argv: свой `prompt` ячейки, иначе
    `SWARM_PROMPT` (протокол первой строки). `prompt` харнесса не наследуем:
    это текст прямой выдачи — он велит самому делать claim/stage/done, чего
    харнесс роя делать не должен (docs/specs/swarm-stage-launch.md)."""
    prompt = cell.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt
    return harnesses_store.SWARM_PROMPT


def resolve_role(conn: sqlite3.Connection, record: dict, role: str | None) -> dict | None:
    """Роль этапа в виде `{role, harness, argv, prompt}`; None — роли нет.

    Роль есть, когда у ячейки задан `harness` и находится команда: свой argv
    (`argv` у `kind=swarm`, `command` у конвейера с `driver=swarm`) или argv
    харнесса по умолчанию. Ячейка без харнесса и харнесс без команды —
    «нет роли с командой», это не сбой маршрута: этап пропускается.
    """
    if not role:
        return None
    roles = record.get("roles") or {}
    cell = roles.get(role)
    if not isinstance(cell, dict):
        return None
    harness = (cell.get("harness") or "").strip() if isinstance(cell.get("harness"), str) else ""
    if not harness:
        return None
    harness_record = harnesses_store.by_key(conn, harness)
    argv = _cell_argv(cell, harness_record)
    if not argv:
        return None
    return {"role": role, "harness": harness, "argv": list(argv),
            "prompt": _cell_prompt(cell)}


def next_stage_with_role(conn: sqlite3.Connection, record: dict,
                         stage: str | None) -> str | None:
    """Ближайший этап после `stage`, у которого есть роль с командой."""
    current = (stage or "").strip() or "s1-spec"
    stages = [s for s, _ in STAGE_ROLES]
    try:
        start = stages.index(current) + 1
    except ValueError:
        start = 0
    for next_stage, role in STAGE_ROLES[start:]:
        if resolve_role(conn, record, role) is not None:
            return next_stage
    return None


def has_roles(conn: sqlite3.Connection, record: dict) -> bool:
    """Есть ли у маршрута хоть одна роль с командой."""
    return any(resolve_role(conn, record, role) is not None for _, role in STAGE_ROLES)


# ------------------------------------------------------------------ порции

def portions(conn: sqlite3.Connection, task_id: str) -> list:
    """Не отменённые дети по `parent-child` — порции нарезки `s1-spec`."""
    try:
        return conn.execute(
            "SELECT t.id, t.status, t.stage, t.holder, t.launched_by, t.launch_route "
            "FROM deps d JOIN tasks t ON t.id = d.issue_id "
            "WHERE d.depends_on = ? AND d.dep_type IN ('parent-child','parent') "
            "AND t.status != 'cancelled' ORDER BY t.created_at, t.rowid",
            (task_id,)).fetchall()
    except sqlite3.OperationalError:
        return []


def has_portions(conn: sqlite3.Connection, task_id: str) -> bool:
    return bool(portions(conn, task_id))


def portion_statuses(conn: sqlite3.Connection, task_id: str) -> list:
    """Статусы всех детей `parent-child`, включая отменённых."""
    try:
        return [row["status"] for row in conn.execute(
            "SELECT t.status FROM deps d JOIN tasks t ON t.id = d.issue_id "
            "WHERE d.depends_on = ? AND d.dep_type IN ('parent-child','parent')",
            (task_id,)).fetchall()]
    except sqlite3.OperationalError:
        return []


def portions_cancelled_only(conn: sqlite3.Connection, row) -> bool:
    """Нарезка была, но каждая порция `cancelled` и ни одной `done`
    (docs/specs/swarm-stage-launch.md): такого родителя рой не запускает —
    на нём вопрос человеку. Признак гаснет, как только родитель ушёл с
    `s1-spec` (этап позже — значит, нарезку уже разобрали)."""
    statuses = portion_statuses(conn, row["id"])
    if not statuses or not all(status == "cancelled" for status in statuses):
        return False
    return (row["stage"] or "").strip() in ("", "s1-spec")


def _slice_parent(conn: sqlite3.Connection, task_id: str, record: dict,
                  children: list) -> None:
    """`готово` от `spec`, и порции уже заведены: родитель дальше не идёт.

    Родитель остаётся на `s1-spec` без держателя; каждой ещё не начатой порции —
    маршрут родителя (если своего нет), `launch_driver='swarm'` и первый этап
    после `s1-spec`, у которого есть роль. `suggested-blocks` между порциями
    этого родителя подтверждаются в жёсткие `blocks`.
    """
    ids = ", ".join(row["id"] for row in children)
    store.add_comment(conn, task_id,
                      f"рой: нарезано на {ids}, родитель дальше по ролям не идёт",
                      author=SWARM_ACTOR, kind="journal")
    first = next_stage_with_role(conn, record, "s1-spec")
    if first is None:
        store.set_needs_owner(
            conn, task_id, value=True, actor=SWARM_ACTOR,
            text="рой: порции заведены, но после s1-spec роли нет")
        return
    portion_ids = {row["id"] for row in children}
    for row in children:
        # Уже начатую порцию не переписываем. Неначатая — статус open без
        # держателя и запуска; этап пустой или ровно `s1-spec` (такую тоже
        # переводим — спека её уже прошла, docs/specs/swarm-stage-launch.md).
        child_stage = (row["stage"] or "").strip()
        if row["status"] != "open" or child_stage not in ("", "s1-spec") \
                or (row["holder"] or "").strip() or (row["launched_by"] or "").strip():
            continue
        # Маршрут меняется, только пока карточка «просто заведена» (без этапа,
        # держателя и запуска) — поэтому до записи этапа и отдельной правкой.
        if not (row["launch_route"] or "").strip():
            store.update_task(conn, row["id"], actor=SWARM_ACTOR,
                              launch_route=record["key"],
                              note=f"рой: порция нарезки {task_id}, "
                                   f"маршрут {record['key']}")
        store.update_task(conn, row["id"], actor=SWARM_ACTOR,
                          stage=first, holder="",
                          note=f"рой: порция нарезки {task_id}, этап {first}")
        # `launch_driver` не в UPDATABLE — её пишет только лаунчер/нарезка.
        # Порция ещё не начата, но снимок у неё мог остаться от прошлого
        # захвата — переписываем на `swarm` (docs/specs/swarm-stage-launch.md).
        conn.execute("UPDATE tasks SET launch_driver = 'swarm' WHERE id = ?",
                     (row["id"],))
    # Порядок порций: предложения агента между порциями этого родителя
    # подтверждаем жёсткими `blocks`; чужие рёбра и рёбра наружу не трогаем.
    marks = ",".join("?" * len(portion_ids))
    links = conn.execute(
        f"SELECT issue_id, depends_on FROM deps WHERE dep_type = 'suggested-blocks' "
        f"AND issue_id IN ({marks}) AND depends_on IN ({marks})",
        (*portion_ids, *portion_ids)).fetchall()
    for link in links:
        store.add_dep(conn, link["issue_id"], link["depends_on"], "blocks",
                      created_by=SWARM_ACTOR, confirm=True)


def is_swarm_task(conn: sqlite3.Connection, row) -> bool:
    """Карточка едет роем: снимок `launch_driver`, а до первого запуска — живой
    `driver` её маршрута."""
    snapshot = row["launch_driver"] if "launch_driver" in row.keys() else None
    if snapshot:
        return snapshot == "swarm"
    route_key = (row["launch_route"] or "").strip()
    if not route_key:
        return False
    try:
        from . import routes_store
        return is_swarm(routes_store.get_route(conn, route_key))
    except Exception:  # noqa: BLE001 — маршрут могли удалить: считаем не-роем
        return False


def close_swarm_parent(conn: sqlite3.Connection, task_id: str) -> None:
    """Если `task_id` — порция и все порции её родителя-роя закрыты — закрыть его.

    Условие (docs/specs/swarm-stage-launch.md): у родителя режим роя, есть хотя
    бы один ребёнок `done`, каждый ребёнок `parent-child` — `done` или
    `cancelled`. Закрытие — `status=done`, `stage=done`, держатель снят,
    `close_reason` «порции закрыты», журнал. Вердикт родителю не пишется.
    Повторный вызов и родитель не-роем — no-op.
    """
    row = conn.execute(
        "SELECT depends_on FROM deps WHERE issue_id = ? "
        "AND dep_type IN ('parent-child','parent') LIMIT 1", (task_id,)).fetchone()
    if row is None:
        return
    parent_id = row["depends_on"]
    parent = conn.execute("SELECT * FROM tasks WHERE id = ?", (parent_id,)).fetchone()
    if parent is None or parent["status"] in store.FINAL_STATUSES:
        return
    children = portions(conn, parent_id)
    if not children:
        return
    statuses = [child["status"] for child in children]
    if "done" not in statuses:
        return
    if any(status not in ("done", "cancelled") for status in statuses):
        return
    if not is_swarm_task(conn, parent):
        return
    store.update_task(conn, parent_id, actor=SWARM_ACTOR,
                      status="done", stage="done", holder="",
                      close_reason="порции закрыты",
                      note="рой: все порции закрыты, родитель закрыт")
    store.add_comment(conn, parent_id,
                      "рой: все порции закрыты, родитель закрыт",
                      author=SWARM_ACTOR, kind="journal")


def note_portions_cancelled(conn: sqlite3.Connection, task_id: str) -> None:
    """Последняя живая порция отменена — вопрос человеку на родителе-рое.

    Вызывается из `update_task`, когда порция становится `cancelled`
    (docs/specs/swarm-stage-launch.md): все дети `cancelled` и ни одного
    `done` — родителя не закрывать и `s1-spec` заново не запускать. Родитель
    не в режиме роя, уже закрыт или уже с вопросом — no-op.
    """
    row = conn.execute(
        "SELECT depends_on FROM deps WHERE issue_id = ? "
        "AND dep_type IN ('parent-child','parent') LIMIT 1", (task_id,)).fetchone()
    if row is None:
        return
    parent = conn.execute("SELECT * FROM tasks WHERE id = ?",
                          (row["depends_on"],)).fetchone()
    if parent is None or parent["status"] in store.FINAL_STATUSES:
        return
    if parent["needs_owner"] or not is_swarm_task(conn, parent):
        return
    if not portions_cancelled_only(conn, parent):
        return
    store.set_needs_owner(conn, parent["id"], value=True, actor=SWARM_ACTOR,
                          text="рой: все порции отменены, родитель не закрыт")


# ------------------------------------------------------------------ первая строка

def out_path_of(launch_log: str | None) -> Path | None:
    """Файл stdout рядом с `launch_log`: суффикс `.out` вместо `.log`."""
    if not launch_log:
        return None
    path = Path(launch_log)
    if path.suffix == ".log":
        return path.with_suffix(".out")
    return Path(str(path) + ".out")


def read_first_line(path: Path | None) -> tuple[str, str]:
    """Первая строка stdout и хвост после неё.

    UTF-8 с заменой ошибок, один ведущий BOM снят, не больше 64 КиБ. Пустой
    файл, нет файла или пустая первая строка — `("", "")`: следующие строки
    пустую первую не подменяют.
    """
    if path is None:
        return "", ""
    try:
        with path.open("rb") as fh:
            raw = fh.read(OUT_READ_LIMIT + 1)
    except OSError:
        return "", ""
    window = raw[:OUT_READ_LIMIT]
    # Строка, которая внутри окна не кончается, — обрезанный префикс, за ответ
    # не считается (docs/specs/swarm-stage-launch.md).
    if len(raw) > OUT_READ_LIMIT and b"\n" not in window and b"\r" not in window:
        return "", ""
    text = window.decode("utf-8", errors="replace")
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.splitlines()
    if not lines:
        return "", ""
    return lines[0].strip(), "\n".join(lines[1:])


def _release_capture(conn: sqlite3.Connection, task_id: str, dispatch_id) -> None:
    """Снять `launched_by`/`dispatch_id` — исход роя разобран, запуск кончился."""
    conn.execute("UPDATE tasks SET launched_by = NULL, dispatch_id = NULL "
                 "WHERE id = ? AND dispatch_id IS ?", (task_id, dispatch_id))


def _clear_holder(conn: sqlite3.Connection, task_id: str, note: str) -> None:
    row = conn.execute("SELECT holder FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is not None and (row["holder"] or "").strip():
        store.update_task(conn, task_id, actor=SWARM_ACTOR, holder="", note=note)


def apply_outcome(conn: sqlite3.Connection, task_id: str, *, notify=None) -> None:
    """Разобрать первую строку `.out` завершившегося процесса и повести карточку.

    Вызывается из `_track`/`recover`/опросчика после записи кода выхода —
    только когда `dispatch_id` ещё наш (ограждение делает вызывающий). Любой
    исход заканчивается снятием `launched_by`/`dispatch_id`, чтобы рой не видел
    завершённый запуск не-`done` как сбой режима скила.
    """
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return
    dispatch_id = row["dispatch_id"]
    launch_log = row["launch_log"]
    stage = (row["stage"] or "").strip() or "s1-spec"
    role = STAGE_TO_ROLE.get(stage)
    first, tail = read_first_line(out_path_of(launch_log))
    tail = tail[:TAIL_LIMIT]
    route_key = (row["launch_route"] or "").strip()
    record = None
    if route_key:
        try:
            from . import routes_store
            record = routes_store.get_route(conn, route_key)
        except Exception:  # noqa: BLE001 — маршрут могли удалить
            record = None
    try:
        if role is None:
            # Этап без роли (done и т.п.) — разбирать нечего.
            store.add_comment(conn, task_id,
                              f"рой: этап {stage or '—'} без роли — ответ процесса "
                              f"не применяю (первая строка: {first or 'пусто'})",
                              author=SWARM_ACTOR, kind="journal")
            return
        allowed = ANSWERS.get(role, frozenset())
        if first == "готово" and first in allowed:
            children = portions(conn, task_id)
            if stage == "s1-spec" and role == "spec" and children:
                _clear_holder(conn, task_id, "рой: нарезка, держатель снят")
                _slice_parent(conn, task_id, record or {"key": route_key,
                                                      "roles": {}}, children)
                return
            nxt = next_stage_with_role(conn, record or {}, stage) if record else None
            if nxt is not None:
                # Одним next_stage: handoff снимает держателя сам, а sticky
                # (s3-impl→s4-judge) оставит — тогда снимаем отдельно.
                store.next_stage(conn, task_id, to_stage=nxt, actor=SWARM_ACTOR,
                                 note=f"рой: ответ «готово», этап {stage} → {nxt}")
                _clear_holder(conn, task_id,
                              "рой: ответ «готово», держатель снят")
                store.add_comment(
                    conn, task_id,
                    f"рой: ответ «готово», этап {stage} → {nxt}, держатель снят",
                    author=SWARM_ACTOR, kind="journal")
            else:
                _clear_holder(conn, task_id, "рой: роли кончились, держатель снят")
                store.set_needs_owner(
                    conn, task_id, value=True, actor=SWARM_ACTOR,
                    text=f"рой: после {stage} роли нет, карточку не закрываю. "
                         "Сними флаг — запущу тот же этап снова.")
        elif first == "зелёный" and role == "judge":
            store.add_comment(conn, task_id, "VERDICT: PASS", author=SWARM_ACTOR,
                              kind="verdict")
            store.update_task(conn, task_id, actor=SWARM_ACTOR,
                              stage="done", status="done", holder="",
                              note="рой: ответ «зелёный», карточка закрыта")
            store.add_comment(conn, task_id,
                              "рой: ответ «зелёный», карточка закрыта",
                              author=SWARM_ACTOR, kind="journal")
        elif first == "красный" and role == "judge":
            body = tail.strip() or (
                f"1. приёмка ответила «красный» без списка правок — "
                f"лог {launch_log} — разбери лог и поправь")
            store.add_comment(conn, task_id, f"VERDICT: FAIL\n{body}",
                              author=SWARM_ACTOR, kind="verdict")
            _clear_holder(conn, task_id, "рой: красный вердикт, держатель снят")
            store.add_comment(conn, task_id,
                              "рой: ответ «красный», возврат на s3-impl, держатель снят",
                              author=SWARM_ACTOR, kind="journal")
        elif first == "вопрос" and first in allowed:
            text = tail.strip() or (
                f"рой: этап {stage} ({role}) ответил «вопрос» без текста. "
                f"Лог: {launch_log}")
            _clear_holder(conn, task_id, "рой: вопрос человеку, держатель снят")
            store.set_needs_owner(conn, task_id, value=True, text=text,
                                  actor=SWARM_ACTOR)
        else:
            # `не смог`, пустой вывод и любая чужая строка — один исход.
            shown = first or "пусто"
            text = (f"рой: этап {stage} ({role}) не сдал работу "
                    f"(первая строка: {shown}). Лог: {launch_log}")
            rest = tail.strip()[:FAIL_TAIL_LIMIT]
            if rest:
                text += f"\n{rest}"
            _clear_holder(conn, task_id, "рой: работа не сдана, держатель снят")
            store.set_needs_owner(conn, task_id, value=True, text=text,
                                  actor=SWARM_ACTOR)
    except Exception as exc:  # noqa: BLE001 — исход не потерять: вопрос человеку
        try:
            store.set_needs_owner(
                conn, task_id, value=True, actor=SWARM_ACTOR,
                text=f"рой: не разобрал исход этапа: {errors_mod.message_of(exc)}")
        except Exception:  # noqa: BLE001 — соединение могло умереть вместе с исходом
            pass
    finally:
        _release_capture(conn, task_id, dispatch_id)
        conn.commit()

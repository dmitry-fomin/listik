"""Автостарт задач: запуск процесса по маршруту и слежение за ним.

Задачу с `autostart=1` и ключом маршрута (`launch_route`) запускает сам сервер:
маршрут даёт argv (`command` в `routes.json`), `listik/launcher.py` подставляет в него
значения, стартует процесс без shell и записывает в карточку всё, что о нём знает
(`launched_by`, `launch_pid`, `launched_at`, `launch_log`, `launch_exit_code`,
`launch_finished_at`, `launch_error`).

Модуль намеренно не импортирует `listik.server`: это был бы цикл импортов. События
доски приходят сюда колбэком `notify(kind, payload)` — сервер передаёт `server.publish`,
локальный фолбэк CLI и тесты передают свой колбэк или `None`.

Поток слежения за процессом (daemon) ждёт `wait()`, пишет код выхода и комментарий
`journal` в собственном соединении с той же базой. Для тестов поток доступен через
`tracker(task_id)` — `threading.Thread`, на котором можно сделать `join(timeout)`;
поток остаётся в реестре и после завершения, чтобы `join` не гонялся с его остановкой.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import db as db_mod
from . import paths
from . import routes as routes_mod
from . import store

# Подстановки в элементах команды: ровно те, что разрешает routes.py. Замена
# однопроходная — re.sub с функцией не пересканирует то, что подставил, поэтому
# `{task_id}` внутри title остаётся как есть.
_SUBST_RE = re.compile(r"\{(" + "|".join(routes_mod.PLACEHOLDERS) + r")\}")

ALREADY_STARTED = "уже запущена Listik"

# Реестр потоков слежения: {task_id: Thread}. Нужен тестам для join(timeout).
_trackers: dict[str, threading.Thread] = {}
_trackers_lock = threading.Lock()


def tracker(task_id: str) -> threading.Thread | None:
    """Поток слежения за процессом задачи (None, если задачу не запускали)."""
    with _trackers_lock:
        return _trackers.get(task_id)


def _notify(notify, task_id: str) -> None:
    if notify is not None:
        notify("task", {"id": task_id, "action": "launch"})


def _db_path(conn):
    """Путь к файлу основной базы: потоку нужно собственное соединение с той же базой.

    Пустая строка — база в памяти (у неё файла нет): тогда поток работает на том же
    соединении (`check_same_thread=False` это позволяет).
    """
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except Exception:  # noqa: BLE001 — путь не критичен, есть запасной вариант
        return None
    for row in rows:
        if row[1] == "main" and row[2]:
            return Path(row[2])
    return None


def _release(conn, task_id: str) -> None:
    """Снять захват задачи, поставленный в начале `start`."""
    conn.execute("UPDATE tasks SET launched_by = NULL, launched_at = NULL WHERE id = ?",
                 (task_id,))
    conn.commit()


def refuse(conn, task_id: str, reason: str, notify=None) -> str:
    """Отказ от запуска: `launch_error`, флаг «нужен человек», строка в stderr и notify.

    Всё пишется в одной транзакции: `set_needs_owner` коммитит и `launch_error`,
    выставленный перед ним. Возвращает причину, чтобы вызывающий вернул её наружу.

    Текст вопроса — из `store.AUTOSTART_QUESTION_PREFIX`: по нему смена маршрута
    узнаёт, что флаг «нужен человек» поднят именно отказом автостарта, и снимает
    его вместе с `launch_error` (см. `store.autostart_reset`).
    """
    ts = store.now_iso()
    conn.execute("UPDATE tasks SET launch_error = ?, updated_at = ? WHERE id = ?",
                 (reason, ts, task_id))
    store.set_needs_owner(conn, task_id, value=True,
                          text=f"{store.AUTOSTART_QUESTION_PREFIX}: {reason} — нужен ты",
                          actor=store.AUTOSTART_ACTOR)
    print(f"autostart {task_id}: {reason}", file=sys.stderr, flush=True)
    _notify(notify, task_id)
    return reason


def _fail(conn, task_id: str, reason: str, notify) -> str:
    """Отказ после захвата: сначала снять захват, потом `refuse`."""
    _release(conn, task_id)
    return refuse(conn, task_id, reason, notify)


def _workdir(conn, row) -> Path | None:
    """Каталог запуска: `worktree` задачи, иначе `path` её проекта.

    None — если оба пусты или выбранного каталога нет на диске.
    """
    worktree = (row["worktree"] or "").strip()
    if worktree:
        chosen = Path(worktree)
    else:
        project_path = ""
        if row["project"]:
            prow = conn.execute("SELECT path FROM projects WHERE slug = ?",
                                (row["project"],)).fetchone()
            project_path = ((prow["path"] if prow else None) or "").strip()
        if not project_path:
            return None
        chosen = Path(project_path)
    return chosen if chosen.is_dir() else None


def _substitute(element: str, values: dict) -> str:
    return _SUBST_RE.sub(lambda m: values[m.group(1)], element)


def _start_tracker(conn, task_id: str, pid: int, proc: subprocess.Popen, notify):
    """Поток-демон, который дождётся процесса и запишет его код выхода."""
    thread = threading.Thread(
        target=_track, args=(conn, _db_path(conn), task_id, pid, proc, notify),
        name=f"listik-launch-{task_id}", daemon=True)
    with _trackers_lock:
        _trackers[task_id] = thread
    thread.start()
    return thread


def _track(conn, db_path, task_id: str, pid: int, proc: subprocess.Popen, notify) -> None:
    code = proc.wait()
    own = db_mod.connect(db_path) if db_path else None
    target = own or conn
    try:
        ts = store.now_iso()
        target.execute("UPDATE tasks SET launch_exit_code = ?, launch_finished_at = ?, "
                       "updated_at = ? WHERE id = ?", (code, ts, ts, task_id))
        # add_comment коммитит и UPDATE выше — завершение пишется одной транзакцией.
        # Этап, держателя и статус слежение не трогает: запуск не делает claim за агента.
        store.add_comment(target, task_id,
                          f"автостарт: процесс {pid} завершился с кодом {code}",
                          author="agent:listik", kind="journal")
    except Exception as exc:  # noqa: BLE001 — падать в демоне нельзя, скажем в stderr
        print(f"autostart {task_id}: не записал завершение процесса {pid}: {exc}",
              file=sys.stderr, flush=True)
        return
    finally:
        if own is not None:
            own.close()
    _notify(notify, task_id)


def start(conn, task_id: str, notify=None, *, log_dir=None) -> str | None:
    """Запустить процесс задачи по её маршруту.

    Возвращает None, если процесс запущен, или текст причины отказа. Проверки идут
    строго по порядку: захват задачи условным UPDATE (`launched_by IS NULL`) —
    уже запущенная задача не трогается вовсе; проверка routes.json, наличия маршрута
    и `command`; рабочий каталог; наконец `Popen`. Любой отказ снимает захват и
    уходит в `refuse` (launch_error + needs_owner), поэтому «уже запущена» —
    единственный отказ, который состояние задачи не меняет.

    `log_dir` — только для тестов, по умолчанию `logs/` в корне репозитория.
    Поток слежения доступен через `tracker(task_id)`.
    """
    ts = store.now_iso()
    captured = conn.execute(
        "UPDATE tasks SET launched_by = 'listik', launched_at = ? "
        "WHERE id = ? AND launched_by IS NULL", (ts, task_id))
    conn.commit()
    if captured.rowcount == 0:
        # Задача уже запущена этим или параллельным вызовом: ни launch_error, ни
        # needs_owner, ни комментариев, ни события — работающая задача остаётся как есть.
        print(f"autostart {task_id}: {ALREADY_STARTED}", file=sys.stderr, flush=True)
        return ALREADY_STARTED

    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:  # задачу удалили между захватом и чтением
        print(f"autostart {task_id}: задача не найдена", file=sys.stderr, flush=True)
        return "задача не найдена"

    state = routes_mod.current()
    if not state.ok:
        return _fail(conn, task_id, f"routes.json с ошибкой: {state.error}", notify)

    key = row["launch_route"] or ""
    record = state.by_key.get(key)
    if record is None:
        return _fail(conn, task_id, f"маршрута {key} нет в routes.json", notify)

    command = record.get("command")
    if not command:
        return _fail(conn, task_id, f"у маршрута {key} нет command в routes.json", notify)

    cwd = _workdir(conn, row)
    if cwd is None:
        project = row["project"] or "—"
        return _fail(conn, task_id,
                     f"нет рабочего каталога (worktree или path проекта {project})", notify)

    values = {"task_id": task_id, "project": row["project"] or "", "route": key,
              "cwd": str(cwd), "title": row["title"] or ""}
    argv = [_substitute(element, values) for element in command]

    log_dir = Path(log_dir) if log_dir is not None else paths.ROOT_DIR / "logs"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = log_dir / f"launch-{task_id}-{stamp}.log"
    env = os.environ | {"LISTIK_TASK_ID": task_id, "LISTIK_ROUTE": key,
                        "LISTIK_LAUNCHED_BY": "listik"}
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_path, "wb") as log:
            # Без shell: argv уходит процессу как есть, ничего из задачи не расширяется.
            proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True, env=env)
    except OSError as exc:
        return _fail(conn, task_id, f"не удалось запустить: {exc}", notify)

    pid = proc.pid
    ts = store.now_iso()
    conn.execute("UPDATE tasks SET launch_pid = ?, launch_log = ?, launch_error = NULL, "
                 "updated_at = ? WHERE id = ?", (pid, str(log_path), ts, task_id))
    # add_comment коммитит и UPDATE выше — запуск пишется одной транзакцией.
    store.add_comment(conn, task_id,
                      f"автостарт: маршрут {key}, pid {pid}, лог {log_path}",
                      author="agent:listik", kind="journal")
    _notify(notify, task_id)
    _start_tracker(conn, task_id, pid, proc, notify)
    return None


def recover(conn, notify=None) -> list[str]:
    """После перезапуска сервера: пометить задачи, чьё слежение потеряно.

    Для задач с `launched_by='listik'`, непустым `launch_pid` и пустым
    `launch_finished_at` проверяется, жив ли процесс (`os.kill(pid, 0)`).
    `ProcessLookupError` — процесс умер, пока сервер лежал: пишем
    `launch_finished_at`, комментарий и событие; код выхода остаётся NULL, потому что
    узнать его уже негде. Живой процесс и `PermissionError` (чужой живой процесс)
    не трогаются — слежение за живым процессом не возобновляется. Возвращает id
    задач, у которых слежение потеряно. Принятый риск: переиспользованный PID
    считается живым, это не лечим.
    """
    rows = conn.execute(
        "SELECT id, launch_pid FROM tasks WHERE launched_by = 'listik' "
        "AND launch_pid IS NOT NULL "
        "AND (launch_finished_at IS NULL OR launch_finished_at = '')").fetchall()
    lost: list[str] = []
    for row in rows:
        pid = row["launch_pid"]
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            ts = store.now_iso()
            conn.execute("UPDATE tasks SET launch_finished_at = ?, updated_at = ? "
                         "WHERE id = ?", (ts, ts, row["id"]))
            store.add_comment(conn, row["id"],
                              "автостарт: отслеживание потеряно при перезапуске сервера",
                              author="agent:listik", kind="journal")
            _notify(notify, row["id"])
            lost.append(row["id"])
        except PermissionError:
            continue
        except OSError as exc:  # прочая ошибка проверки — считаем процесс живым
            print(f"autostart {row['id']}: проверка pid {pid}: {exc}",
                  file=sys.stderr, flush=True)
    return lost

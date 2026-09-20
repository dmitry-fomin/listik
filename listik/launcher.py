"""Автостарт задач: запуск процесса по маршруту и слежение за ним.

Задачу с `autostart=1` и ключом маршрута (`launch_route`) запускает сам сервер:
маршрут даёт argv (`command` в таблице `routes`), `listik/launcher.py` подставляет в него
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
import signal
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import actors as actors_mod
from . import db as db_mod
from . import errors as errors_mod
from . import paths
from . import routes as routes_mod
from . import store

# Подстановки в элементах команды: ровно те, что разрешает routes.py. Замена
# однопроходная — re.sub с функцией не пересканирует то, что подставил, поэтому
# `{task_id}` внутри значения остаётся как есть.
_SUBST_RE = re.compile(r"\{(" + "|".join(routes_mod.PLACEHOLDERS) + r")\}")

ALREADY_STARTED = "уже запущена Listik"

# Реестр потоков слежения: {task_id: Thread}. Нужен тестам для join(timeout).
_trackers: dict[str, threading.Thread] = {}
_trackers_lock = threading.Lock()

# Реестр процессов текущего запуска сервера: {task_id: Popen}. Заполняет `start`
# под `_trackers_lock`; `revoke` использует его, чтобы дожидаться смерти через
# `proc.poll()`, когда собственный дочерний процесс ещё виден. После `recover`
# (другой процесс сервера) записи нет — `revoke` дожидается через `_alive`/`waitpid`.
_procs: dict[str, subprocess.Popen] = {}

# Период опроса живого pid, за которым слежение потеряно при перезапуске сервера.
POLL_INTERVAL = 5.0

# Сколько ждать смерти процесса после сигнала (revoke), прежде чем эскалировать
# SIGTERM → SIGKILL. Тесты подменяют на меньшее значение.
KILL_GRACE = 5.0

#: Как часто опрашивать `_alive(pid)`/`proc.poll()` при ожидании смерти в `revoke`.
_KILL_POLL_INTERVAL = 0.05


def tracker(task_id: str) -> threading.Thread | None:
    """Поток слежения за процессом задачи (None, если задачу не запускали)."""
    with _trackers_lock:
        return _trackers.get(task_id)


def _notify(notify, task_id: str, action: str = "launch") -> None:
    if notify is not None:
        notify("task", {"id": task_id, "action": action})


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
    """Снять захват задачи, поставленный в начале `start`.

    `generation` остаётся поднятым: монотонность важнее «красивых» номеров, а
    процесса с этим поколением не существует — токен с ним никто не получит.
    `dispatch_id` снимается — это уже не действующий запуск.
    """
    conn.execute("UPDATE tasks SET launched_by = NULL, launched_at = NULL, "
                 "dispatch_id = NULL WHERE id = ?", (task_id,))
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

    `worktree=main`/`master` — маркер «работа в основной ветке без отдельного
    дерева» (`store.main_worktree`), а не каталог: запускаем в каталоге проекта.

    None — если оба пусты или выбранного каталога нет на диске.
    """
    worktree = (row["worktree"] or "").strip()
    if worktree and not store.is_main_worktree(worktree):
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


def _start_tracker(conn, task_id: str, pid: int, proc: subprocess.Popen, notify,
                    *, dispatch_id: str | None, generation: int):
    """Поток-демон, который дождётся процесса и запишет его код выхода."""
    thread = threading.Thread(
        target=_track,
        args=(conn, _db_path(conn), task_id, pid, proc, notify, dispatch_id, generation),
        name=f"listik-launch-{task_id}", daemon=True)
    with _trackers_lock:
        _trackers[task_id] = thread
    thread.start()
    return thread


def _track(conn, db_path, task_id: str, pid: int, proc: subprocess.Popen, notify,
           dispatch_id: str | None, generation: int) -> None:
    code = proc.wait()
    own = db_mod.connect(db_path) if db_path else None
    target = own or conn
    try:
        ts = store.now_iso()
        # `IS`, не `=`: сверяем со «своим» запуском, включая случай dispatch_id IS NULL
        # (запуски до поколений). `rowcount == 0` — задачу отозвали/перезапустили, пока
        # процесс жил (порция c): колонки чужого запуска не трогаем.
        cur = target.execute(
            "UPDATE tasks SET launch_exit_code = ?, launch_finished_at = ?, "
            "updated_at = ? WHERE id = ? AND dispatch_id IS ?",
            (code, ts, ts, task_id, dispatch_id))
        if cur.rowcount == 0:
            store.add_comment(
                target, task_id,
                f"автостарт: процесс {pid} поколения {generation} завершился с кодом "
                f"{code} после отзыва — карточка не менялась",
                author="agent:listik", kind="journal")
        else:
            # add_comment коммитит и UPDATE выше — завершение пишется одной транзакцией.
            # Этап, держателя и статус слежение не трогает: не делает claim за агента.
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
    уже запущенная задача не трогается вовсе; проверка маршрутов в базе, наличия
    маршрута и `command`; рабочий каталог; наконец `Popen`. Любой отказ снимает захват и
    уходит в `refuse` (launch_error + needs_owner), поэтому «уже запущена» —
    единственный отказ, который состояние задачи не меняет.

    `log_dir` — только для тестов, по умолчанию `logs/` в корне репозитория.
    Поток слежения доступен через `tracker(task_id)`.
    """
    ts = store.now_iso()
    dispatch_id = uuid.uuid4().hex
    captured = conn.execute(
        "UPDATE tasks SET launched_by = 'listik', launched_at = ?, "
        "generation = generation + 1, dispatch_id = ? "
        "WHERE id = ? AND launched_by IS NULL", (ts, dispatch_id, task_id))
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

    state = routes_mod.state(conn)
    if not state.ok:
        return _fail(conn, task_id, f"маршруты в базе недоступны: {state.error}", notify)

    key = row["launch_route"] or ""
    record = state.by_key.get(key)
    if record is None:
        return _fail(conn, task_id, f"маршрута {key} нет в базе", notify)

    command = record.get("command")
    if not command:
        return _fail(conn, task_id, f"у маршрута {key} нет command в базе", notify)

    cwd = _workdir(conn, row)
    if cwd is None:
        project = row["project"] or "—"
        return _fail(conn, task_id,
                     f"нет рабочего каталога (worktree или path проекта {project})", notify)

    # Прямой маршрут: харнесс работает сам, оркестратора нет. Карточку выдаём ему до
    # Popen — этап «ТЗ» (s1-spec, если этапа ещё нет) и держатель-харнесс, — чтобы её
    # не взял никто другой, пока агент читает код. Это выдача, а не claim за агента:
    # «взята» карточка станет только после его собственного claim. «Разработку»
    # (s3-impl) агент ставит сам перед первой правкой кода (listik-tyxn).
    issued = False
    if record.get("kind") == "direct" and not (row["holder"] or "").strip():
        fields = {"holder": record["harness"]}
        if not (row["stage"] or "").strip():
            fields["stage"] = "s1-spec"
        store.update_task(conn, task_id, actor="agent:listik",
                          note=f"автостарт: выдана {record['harness']}", **fields)
        issued = True

    # `{worktree}` — колонка `tasks.worktree`, но пустое значение и маркер основной
    # ветки (`main`/`master`) указывают не на дерево, а на каталог проекта: подставляем
    # `cwd`, чтобы значение всегда указывало на реальное дерево. `{branch}` пуст — пустая
    # строка. Замена однопроходная (см. `_SUBST_RE`).
    worktree = (row["worktree"] or "").strip()
    if not worktree or store.is_main_worktree(worktree):
        worktree = str(cwd)
    values = {"task_id": task_id, "project": row["project"] or "", "route": key,
              "cwd": str(cwd), "worktree": worktree, "branch": row["branch"] or ""}
    argv = [_substitute(element, values) for element in command]

    log_dir = Path(log_dir) if log_dir is not None else paths.LOGS_DIR
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = log_dir / f"launch-{task_id}-{stamp}.log"
    generation = int(row["generation"] or 0)
    env = os.environ | {"LISTIK_TASK_ID": task_id, "LISTIK_ROUTE": key,
                        "LISTIK_LAUNCHED_BY": "listik",
                        "LISTIK_GENERATION": str(generation),
                        "LISTIK_DISPATCH_ID": row["dispatch_id"] or ""}
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_path, "wb") as log:
            # Без shell: argv уходит процессу как есть, ничего из задачи не расширяется.
            proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True, env=env)
    except OSError as exc:
        if issued:  # процесса нет — выдача никому: держателя снимаем, этап остаётся
            store.update_task(conn, task_id, actor="agent:listik", holder="",
                              note="автостарт не выполнен: выдача снята")
        return _fail(conn, task_id, f"не удалось запустить: {exc}", notify)

    pid = proc.pid
    # `revoke` (порция c) дожидается смерти через `proc.poll()`, пока сервер тот же
    # процесс, что запустил Popen; ключ — task_id, повторный `start` затирает
    # запись прежнего запуска (реестр не индексирован по dispatch_id).
    with _trackers_lock:
        _procs[task_id] = proc
    ts = store.now_iso()
    dispatch_id = row["dispatch_id"]
    conn.execute("UPDATE tasks SET launch_pid = ?, launch_log = ?, launch_error = NULL, "
                 "launch_exit_code = NULL, launch_finished_at = NULL, updated_at = ? "
                 "WHERE id = ?", (pid, str(log_path), ts, task_id))
    # add_comment коммитит и UPDATE выше — запуск пишется одной транзакцией.
    store.add_comment(conn, task_id,
                      f"автостарт: маршрут {key}, pid {pid}, лог {log_path}, "
                      f"поколение {generation}, запуск {dispatch_id}",
                      author="agent:listik", kind="journal")
    _notify(notify, task_id)
    _start_tracker(conn, task_id, pid, proc, notify,
                   dispatch_id=dispatch_id, generation=generation)
    return None


def recover(conn, notify=None) -> list[str]:
    """После перезапуска сервера: пометить задачи, чьё слежение потеряно.

    Для задач с `launched_by='listik'`, непустым `launch_pid` и пустым
    `launch_finished_at` проверяется, жив ли процесс (`os.kill(pid, 0)`).
    `ProcessLookupError` — процесс умер, пока сервер лежал: пишем
    `launch_finished_at`, комментарий и событие; код выхода остаётся NULL, потому что
    узнать его уже негде. Живой процесс и `PermissionError` (чужой живой процесс)
    не трогаются сразу: для них стартует поток-опросчик (`_poll`), который раз в
    `POLL_INTERVAL` сек проверяет pid и по его исчезновению пишет
    `launch_finished_at` и комментарий «код неизвестен» (listik-3a4m). Возвращает id
    задач, чей процесс умер, пока сервер лежал. Принятый риск: переиспользованный PID
    считается живым, это не лечим.
    """
    rows = conn.execute(
        "SELECT id, launch_pid, dispatch_id FROM tasks WHERE launched_by = 'listik' "
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
            _start_poller(conn, row["id"], pid, notify, dispatch_id=row["dispatch_id"])
        except OSError as exc:  # прочая ошибка проверки — считаем процесс живым
            print(f"autostart {row['id']}: проверка pid {pid}: {exc}",
                  file=sys.stderr, flush=True)
            _start_poller(conn, row["id"], pid, notify, dispatch_id=row["dispatch_id"])
        else:
            _start_poller(conn, row["id"], pid, notify, dispatch_id=row["dispatch_id"])
    return lost


def _start_poller(conn, task_id: str, pid: int, notify, *, dispatch_id: str | None):
    """Поток-демон, опрашивающий живой pid, который сервер не запускал в этом процессе."""
    thread = threading.Thread(
        target=_poll, args=(conn, _db_path(conn), task_id, pid, notify, dispatch_id),
        name=f"listik-launch-poll-{task_id}", daemon=True)
    with _trackers_lock:
        _trackers[task_id] = thread
    thread.start()
    return thread


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # PermissionError и прочее — считаем живым
        return True
    return True


def _poll(conn, db_path, task_id: str, pid: int, notify, dispatch_id: str | None) -> None:
    import time
    while _alive(pid):
        time.sleep(POLL_INTERVAL)
    own = db_mod.connect(db_path) if db_path else None
    target = own or conn
    try:
        ts = store.now_iso()
        cur = target.execute(
            "UPDATE tasks SET launch_finished_at = ?, updated_at = ? WHERE id = ? "
            "AND launch_pid = ? AND dispatch_id IS ? "
            "AND (launch_finished_at IS NULL OR launch_finished_at = '')",
            (ts, ts, task_id, pid, dispatch_id))
        if cur.rowcount == 0:  # запись уже сделана или задачу перезапустили
            target.commit()
            return
        store.add_comment(target, task_id,
                          f"автостарт: процесс {pid} завершился; слежение было потеряно "
                          "при перезапуске сервера — код выхода неизвестен",
                          author="agent:listik", kind="journal")
    except Exception as exc:  # noqa: BLE001 — падать в демоне нельзя
        print(f"autostart {task_id}: не записал завершение процесса {pid}: {exc}",
              file=sys.stderr, flush=True)
        return
    finally:
        if own is not None:
            own.close()
    _notify(notify, task_id)


# ------------------------------------------------------------------ отзыв (revoke)

def _signal(pid: int, sig: int) -> tuple[str, Exception | None]:
    """Отправить сигнал группе `pid`, с откатом на одиночный pid.

    `("ok", None)` — сигнал ушёл; `("dead", None)` — процесс уже мёртв (`killpg`
    поймал `ProcessLookupError`, но лидер группы мог умереть раньше самого
    процесса — проверяем `_alive` после `waitpid(WNOHANG)`, чтобы собрать
    собственного зомби); `("denied", exc)` — сигнал не прошёл (`PermissionError`).
    """
    try:
        os.killpg(pid, sig)
        return "ok", None
    except ProcessLookupError:
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        if not _alive(pid):
            return "dead", None
        try:
            os.kill(pid, sig)
            return "ok", None
        except ProcessLookupError:
            return "dead", None
        except PermissionError as exc:
            return "denied", exc
    except PermissionError as exc:
        return "denied", exc


def _wait_dead(task_id: str, pid: int, grace: float) -> bool:
    """Дождаться смерти `pid` до `grace` секунд, опрашивая раз в 0.05 с.

    `_procs[task_id]` (свой `Popen`, если сервер тот же, что запускал процесс) даёт
    `proc.poll()`; иначе (после `recover`) — `os.waitpid(pid, WNOHANG)` (собрать
    своего зомби; на чужом процессе после `recover` вызов безвреден) и `_alive`.
    """
    import time
    proc = _procs.get(task_id)

    def dead() -> bool:
        if proc is not None:
            return proc.poll() is not None
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        return not _alive(pid)

    deadline = time.monotonic() + grace
    while True:
        if dead():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_KILL_POLL_INTERVAL)


def _kill_process(task_id: str, pid: int, grace: float) -> str:
    """Снять процесс: SIGTERM, ожидание `grace`, эскалация до SIGKILL.

    Возвращает исход: `"снят"` (смерть подтверждена в этом вызове) или `"не
    снят"` (сигнал не прошёл или процесс пережил SIGKILL за `grace`).
    """
    status, exc = _signal(pid, signal.SIGTERM)
    if status == "denied":
        print(f"autostart {task_id}: не удалось снять процесс {pid}: {exc}",
              file=sys.stderr, flush=True)
        return "не снят"
    if status == "dead":
        return "снят"
    if _wait_dead(task_id, pid, grace):
        return "снят"
    status, exc = _signal(pid, signal.SIGKILL)
    if status == "denied":
        print(f"autostart {task_id}: не удалось снять процесс {pid}: {exc}",
              file=sys.stderr, flush=True)
        return "не снят"
    if status == "dead":
        return "снят"
    if _wait_dead(task_id, pid, grace):
        return "снят"
    return "не снят"


def revoke(conn, task_id: str, *, actor: str | None = None, harness: str | None = None,
           note: str | None = None, kill: bool = True, notify=None) -> dict:
    """Отозвать полномочия текущего запуска и (по умолчанию) снять его процесс.

    Единственный способ снять полномочия у живого процесса до того, как он сам
    умрёт: поколение поднимается **первым шагом**, одной транзакцией, — с этого
    момента любая запись со старым токеном уходит в карантин (`fence.guard`), а
    поток слежения старого запуска (`_track`/`_poll`, порция a) видит
    `dispatch_id IS NULL` и пишет только строку журнала. Снятие процесса — второй
    шаг, уже необязательный для итога отзыва: `kill=False` оставляет его жить
    (сценарий зомби из стенда). `launch_pid`/`launched_at`/`launch_log`/
    `launch_exit_code` не трогаются — это история прежнего запуска; поток
    слежения того запуска и дальше пишет только журнал, `launch_exit_code`
    остаётся `NULL`. Держатель, этап, статус, `launch_route`, `autostart` не
    меняются.

    `generation == 0` (задачу не запускали) — `ValueError`. Задачи нет —
    `errors.NotFound`. Параллельный отзыв того же поколения — второй получает
    `ValueError("поколение задачи изменилось параллельно, повтори отзыв")` и не
    трогает базу вовсе.
    """
    row = conn.execute(
        "SELECT generation, dispatch_id, launch_pid, launch_finished_at, launched_by "
        "FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise errors_mod.NotFound(f"задача не найдена: {task_id}")
    old = int(row["generation"] or 0)
    if old == 0:
        raise ValueError(f"задача {task_id} не запускалась: отзывать нечего")
    dispatch_id = row["dispatch_id"]
    pid = row["launch_pid"]
    finished_at = row["launch_finished_at"]
    launched_by = row["launched_by"]

    # Шаг 1 — отзыв поколения, первым и одной транзакцией: иначе поток слежения
    # старого запуска мог бы проснуться на смерти процесса, пока dispatch_id ещё
    # совпадает, и записать launch_exit_code/launch_finished_at, а умирающий
    # процесс держал бы валидный токен до самого конца.
    ts = store.now_iso()
    cur = conn.execute(
        "UPDATE tasks SET generation = generation + 1, dispatch_id = NULL, "
        "launched_by = NULL, updated_at = ? WHERE id = ? AND generation = ?",
        (ts, task_id, old))
    if cur.rowcount == 0:
        conn.rollback()
        raise ValueError("поколение задачи изменилось параллельно, повтори отзыв")
    conn.commit()

    # Шаг 2 — снятие процесса: только «наш и незавершённый» запуск.
    is_ours_alive = bool(launched_by == "listik" and pid and pid > 1
                         and pid != os.getpid())
    if not is_ours_alive:
        outcome = "не наш запуск"
    elif finished_at:
        outcome = "уже завершён"
    elif not kill:
        outcome = "оставлен жить"
    else:
        outcome = _kill_process(task_id, pid, KILL_GRACE)

    # Шаг 3 — запись исхода: launch_finished_at только при подтверждённой в этом
    # вызове смерти, и только если ещё не было записано.
    if outcome == "снят" and not finished_at:
        conn.execute("UPDATE tasks SET launch_finished_at = ? WHERE id = ?",
                     (store.now_iso(), task_id))

    actor_key, actor_kind = actors_mod.resolve(actor, conn)
    if actor:
        actors_mod.remember(conn, actor, actor_key, actor_kind)
    store.event(conn, task_id, "revoke", from_value=str(old), to_value=str(old + 1),
               actor=actor_key, harness=harness,
               note=f"{note or 'полномочия отозваны'}; запуск {dispatch_id or '—'}, "
                    f"pid {pid or '—'}, процесс {outcome}")
    # add_comment коммитит всё — UPDATE launch_finished_at и событие revoke выше
    # уходят одной транзакцией с журналом.
    store.add_comment(
        conn, task_id,
        f"автостарт: полномочия поколения {old} отозваны (запуск {dispatch_id or '—'}, "
        f"pid {pid or '—'}, процесс {outcome}); новое поколение {old + 1}",
        author="agent:listik", kind="journal")
    _notify(notify, task_id, "revoke")
    return store.get_task(conn, task_id)

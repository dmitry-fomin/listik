"""HTTP API и доска Listik (stdlib, без внешних зависимостей).

Один процесс — одна точка записи в базу. Слушает только 127.0.0.1 и требует токен
из config.toml в корне Listik. Агенты (dsh/grok/claude/codex) ходят сюда же, поэтому API
делает всё: и чтение, и запись, и переходы этапов.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import queue
import signal
import sqlite3
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import actors as actors_mod
from . import assistant as assistant_mod
from . import config as config_mod
from . import db as db_mod
from . import deps as deps_mod
from . import embed as embed_mod
from . import errors as errors_mod
from . import launcher as launcher_mod
from . import mcp
from . import paths
from . import routes as routes_mod
from . import routes_store
from . import search as search_mod
from . import skills as skills_mod
from . import store
from . import voice as voice_mod

# Соединение с базой — своё на каждый поток.
#
# ThreadingHTTPServer обслуживает запросы в отдельных потоках (доска шлёт
# /api/board, /api/stats, /api/ready и /api/blocked параллельно, плюс SSE и
# фоновый досчёт векторов). Одно соединение sqlite3 на всех — это не сериализация,
# а гонка: объект connection не потокобезопасен, и параллельные запросы в него
# давали то «database disk image is malformed», то «bad parameter or other API
# misuse», то IndexError на строках-результатах. Писателей это ломало бы тихо:
# сервер отвечает 500, а доска показывает «ошибка операции».
# Поэтому соединение живёт в threading.local, а пишет всё равно SQLite: WAL
# плюс busy_timeout в `db.connect` сериализуют запись между соединениями.
_conn_local = threading.local()
_conn_lock = threading.Lock()
_conn_made = False
#: Живые соединения по id потока: по ним `_invalidate_connections` переоткрывает
#: чужие соединения после подмены файла базы (listik-cfzk) и закрывает их вместе
#: с потоком (listik-sxcd).
_conns: dict[int, sqlite3.Connection] = {}
#: Поколение файла базы. Растёт, когда файл подменили/удалили или соединение
#: поймало DatabaseError: соединение с прошлым поколением закрывается и
#: открывается заново — иначе запись уходила бы в удалённый inode.
_db_generation = 0
#: Поколение, для которого схема и миграции уже применены (`db.init`).
_schema_generation = -1
_subs: list[queue.Queue] = []
_subs_lock = threading.Lock()


def get_conn():
    """Соединение текущего потока; схема и миграции применяются раз за поколение."""
    global _conn_made, _schema_generation
    _watch_tick()  # подмена файла видна и без фонового потока (не чаще интервала)
    conn = getattr(_conn_local, "conn", None)
    if conn is not None and getattr(_conn_local, "generation", None) == _db_generation:
        return conn
    if conn is not None:
        # Соединение прошлого поколения: смотрим на удалённый/подменённый файл.
        close_thread_conn()
    with _conn_lock:
        generation = _db_generation
        if not _conn_made or _schema_generation != generation:
            # После подмены файла схему и миграции применяем заново: копия может
            # быть старее текущей версии Listik.
            conn = db_mod.init()
            _conn_made = True
            _schema_generation = generation
        else:
            conn = db_mod.connect()
        _conns[threading.get_ident()] = conn
    _conn_local.conn = conn
    _conn_local.generation = generation
    return conn


def close_thread_conn() -> None:
    """Закрыть соединение текущего потока (listik-sxcd).

    Поток-обработчик ThreadingHTTPServer живёт одно HTTP-соединение; без явного
    закрытия его sqlite-соединение ждало сборщика мусора и держало fd на базе и на
    уже удалённых WAL. Вызывается в конце каждого потока-обработчика.
    """
    ident = threading.get_ident()
    with _conn_lock:
        conn = _conns.pop(ident, None)
    if conn is None:
        conn = getattr(_conn_local, "conn", None)
        if conn is None:
            return
    _conn_local.conn = None
    _conn_local.generation = None
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass
    # Последнее соединение sqlite закрывает вместе с собой и -wal — это не подмена.
    _refresh_wal()


# ------------------------------------------------------- подмена файла базы
#
# 13.09.2026 базу и WAL заменили (или удалили) под работающим сервером: соединения
# остались на удалённых inode, фоновые потоки каждый цикл писали «database disk
# image is malformed», status показывал -1 задач, а помогал только перезапуск
# (listik-cfzk). Поэтому сервер следит за inode файла базы и WAL и на подмену
# отвечает громко: строка в listik.log, запись в /api/health и `listik status`,
# плюс переоткрытие всех соединений (`_invalidate_connections`).

#: Как часто сервер сам проверяет файлы базы; в get_conn — не чаще этого интервала.
DB_WATCH_INTERVAL = 2.0

_watch_lock = threading.Lock()
#: Последний замер: {"db": (dev, ino) | None, "wal": (dev, ino) | None}.
_fingerprint: dict | None = None
#: Путь базы, к которому относится `_fingerprint`. Другой путь (тест подставил
#: свою временную базу в `paths.DB_PATH`) — не подмена, а новая базовая линия
#: (listik-mfpl): иначе сравнивали бы inode разных файлов.
_fingerprint_path: Path | None = None
#: Последняя подмена файла в этом процессе — уходит в /api/health и `listik status`
#: (в отличие от `_db_error`, это не «прямо сейчас», а факт: он не сбрасывается).
_db_replaced: dict | None = None
_last_watch = 0.0
_dbwatch_stop = threading.Event()
#: Поток надзора, запущенный `start_db_watch`; останавливается `stop_db_watch`.
_dbwatch_thread: threading.Thread | None = None


def _file_id(path: Path) -> tuple[int, int] | None:
    """(устройство, inode) файла; None — файла нет. При подмене inode меняется."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _wal_path() -> Path:
    return Path(str(paths.DB_PATH) + "-wal")


def _fingerprint_now() -> dict:
    return {"db": _file_id(paths.DB_PATH), "wal": _file_id(_wal_path())}


def _open_conns() -> int:
    with _conn_lock:
        return len(_conns)


def _refresh_wal() -> None:
    """Перечитать inode WAL, не считая изменение подменой.

    sqlite сам удаляет -wal, когда закрывается последнее соединение (и создаёт
    заново при следующем открытии); это не подмена. Файл базы не трогаем: его
    подмена в этом окне всё ещё должна быть замечена.
    """
    global _fingerprint
    with _watch_lock:
        if _fingerprint is not None:
            _fingerprint = {**_fingerprint, "wal": _file_id(_wal_path())}


def _fingerprint_diff(before: dict, after: dict, open_conns: int) -> dict | None:
    """Что случилось с файлами базы между замерами; None — всё на месте.

    Подмена: база исчезла или сменила inode; WAL сменил inode или исчез, пока у
    сервера есть открытые соединения (сам sqlite удаляет -wal только вместе с
    последним соединением, значит, его удалил кто-то извне).
    """
    changes: list[str] = []
    db_before, db_after = before.get("db"), after.get("db")
    wal_before, wal_after = before.get("wal"), after.get("wal")
    if db_before and not db_after:
        changes.append("файл базы исчез")
    elif db_before and db_after and db_before != db_after:
        changes.append("файл базы заменён")
    if wal_before and wal_after and wal_before != wal_after:
        changes.append("файл WAL заменён")
    elif wal_before and not wal_after and open_conns > 0:
        changes.append("файл WAL исчез")
    if not changes:
        return None
    kind = "db" if changes[0].startswith("файл базы") else "wal"
    if len(changes) > 1:
        kind = "db+wal"
    return {
        "kind": kind,
        "at": store.now_iso(),
        "detail": "; ".join(changes),
        "before": before,
        "after": after,
    }


def _watch_tick(*, force: bool = False) -> dict | None:
    """Один замер файлов базы; событие подмены уходит в лог, health и status."""
    global _fingerprint, _fingerprint_path, _last_watch
    now = time.monotonic()
    with _watch_lock:
        path = Path(paths.DB_PATH)
        if path != _fingerprint_path:
            # Сменился сам путь базы — прошлый замер про другой файл (listik-mfpl).
            _fingerprint, _fingerprint_path = None, path
            force = True
        if not force and now - _last_watch < DB_WATCH_INTERVAL:
            return None
        _last_watch = now
        before = _fingerprint
        after = _fingerprint_now()
        _fingerprint = after
    if before is None:
        return None
    event = _fingerprint_diff(before, after, _open_conns())
    if event is not None:
        _note_replaced(event)
    return event


def _note_replaced(event: dict) -> None:
    """Подмена файла: громко в лог, в health/status и переоткрыть все соединения."""
    global _db_error, _db_replaced
    _db_replaced = event
    _db_error = {"where": "watch",
                 "error": f"файл базы подменён: {event['detail']}",
                 "at": event["at"]}
    print(f"[watch] ПОДМЕНА ФАЙЛА БАЗЫ: {event['detail']} "
          f"(было {event['before']}, стало {event['after']}) — "
          f"переоткрываю все соединения с базой", flush=True)
    _invalidate_connections()


def _invalidate_connections() -> None:
    """Закрыть все соединения: старые смотрят на удалённый или подменённый файл.

    Чужие соединения закрываем, а не ждём следующего запроса: запись в удалённый
    inode не возвращает ошибку — данные просто теряются молча. Своё соединение
    закроет `close_thread_conn`, следующее `get_conn` откроет уже новое поколение.
    """
    global _db_generation
    mine = threading.get_ident()
    with _conn_lock:
        _db_generation += 1
        foreign = [(ident, conn) for ident, conn in _conns.items() if ident != mine]
        for ident, _ in foreign:
            _conns.pop(ident, None)
    for _ident, conn in foreign:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    close_thread_conn()


def start_db_watch(interval: float = DB_WATCH_INTERVAL) -> threading.Thread:
    """Фоновый надзор за файлами базы: подмена или удаление — событие в лог и health.

    Отдельный поток, а не только проверка в `get_conn`: подмену нужно заметить и
    тогда, когда запросов нет (ночью, на простаивающем сервере). Остановка —
    `stop_db_watch()` (serve вызывает её при завершении, listik-2gn8).
    """
    global _dbwatch_stop, _dbwatch_thread
    stop_db_watch()  # второй запуск не плодит потоки
    _watch_tick(force=True)  # запомнить исходные inode
    stop = threading.Event()  # своё событие: цикл не зависит от подмены глобала

    def loop() -> None:
        while not stop.wait(interval):
            try:
                _watch_tick(force=True)
            except Exception as exc:  # noqa: BLE001 — надзор не должен падать
                print(f"[watch] пропуск: {type(exc).__name__}: {exc}", flush=True)

    thread = threading.Thread(target=loop, name="listik-watch", daemon=True)
    _dbwatch_stop, _dbwatch_thread = stop, thread
    thread.start()
    return thread


def stop_db_watch(timeout: float = 5.0) -> bool:
    """Остановить поток надзора и дождаться его. Идемпотентна.

    True — потока нет или он завершился за `timeout`.
    """
    global _dbwatch_thread
    thread = _dbwatch_thread
    _dbwatch_stop.set()
    if thread is None:
        return True
    if thread is not threading.current_thread():
        thread.join(timeout)
    if thread.is_alive():
        return False
    _dbwatch_thread = None
    return True


# Последняя ошибка базы в фоновом потоке — отдаётся в /api/health, чтобы не молчать.
# Это состояние «прямо сейчас», а не история: первый же проход без DatabaseError
# снимает его (_background_db_ok), иначе health показывал бы старую ошибку даже
# после восстановления базы (listik-9csm).
_db_error: dict | None = None


def _background_db_error(where: str, exc: Exception) -> None:
    """DatabaseError: громко в лог, в health, и переоткрыть все соединения.

    Если базу или WAL подменили под работающим сервером, соединения ловят
    «database disk image is malformed» бесконечно; свежие соединения видят новый файл.
    """
    global _db_error
    _db_error = {"where": where, "error": f"{type(exc).__name__}: {exc}", "at": store.now_iso()}
    print(f"[{where}] ОШИБКА БАЗЫ: {type(exc).__name__}: {exc} — переоткрываю соединения",
          flush=True)
    _invalidate_connections()


def _background_db_ok() -> None:
    """Проход без DatabaseError: снять прошлую ошибку из /api/health (listik-9csm).

    База восстановилась (её пересоздали, вернули WAL, отпустил busy_timeout) — молчать
    об этом нельзя ровно так же, как об ошибке: держатель смотрит на health и решает,
    нужен ли перезапуск демона. Запись о подмене файла (`_db_replaced`) при этом
    остаётся: это факт, который видно в `listik status` до перезапуска сервера.
    """
    global _db_error
    if _db_replaced is not None and _db_error is not None:
        print("[watch] база снова отвечает; запись о подмене файла остаётся в status "
              "до перезапуска сервера", flush=True)
    if _db_error is None:
        return
    where = _db_error.get("where") or "db"
    print(f"[{where}] база снова отвечает — сбрасываю db_error", flush=True)
    _db_error = None


_embed_stop = threading.Event()


def _background_pass(batch_limit: int = 200) -> None:
    """Один проход фоновой индексации: сначала documents, затем embed.

    DatabaseError каждого шага уходит в `_background_db_error` и оставляет запись в
    /api/health; если за весь проход база ни разу не упала — `_background_db_ok`
    снимает прошлую ошибку (listik-9csm). Ошибки, не связанные с базой (нет ollama,
    битый файл документа), записи не создают и снять её не мешают: проход, в котором
    база читалась нормально, не должен держать старую ошибку из-за недоступного ollama.
    """
    db_ok = True
    try:
        from . import documents as documents_mod
        res = documents_mod.refresh_all(get_conn())
        if res.get("reindexed"):
            print(f"[documents] переиндексировано: {res['reindexed']}", flush=True)
            publish("documents", res)
    except sqlite3.DatabaseError as exc:
        db_ok = False
        _background_db_error("documents", exc)
    except Exception as exc:  # noqa: BLE001
        print(f"[documents] пропуск: {type(exc).__name__}: {exc}", flush=True)
    try:
        # соединение своё на поток, поэтому лок больше не нужен
        res = embed_mod.embed_pending(get_conn(), limit=batch_limit, verbose=False)
        if res.get("embedded"):
            search_mod.invalidate_vectors()
            print(f"[embed] досчитано векторов: {res['embedded']}", flush=True)
            publish("embed", res)
    except sqlite3.DatabaseError as exc:
        db_ok = False
        _background_db_error("embed", exc)
    except Exception as exc:  # noqa: BLE001
        print(f"[embed] пропуск: {type(exc).__name__}: {exc}", flush=True)
    if db_ok:
        _background_db_ok()


def start_embed_worker(interval: float = 45.0, batch_limit: int = 200) -> threading.Thread:
    """Фоновый досчёт векторов.

    Задачи создаются и правятся постоянно, а поиск должен находить свежее.
    Раз в `interval` секунд добираем то, для чего вектора ещё нет или текст изменился.
    Векторная ветка необязательна для работы, поэтому любые ошибки (в том числе
    недоступный ollama) только логируются.
    """
    def loop() -> None:
        while not _embed_stop.wait(interval):
            _background_pass(batch_limit)

    thread = threading.Thread(target=loop, name="listik-embed", daemon=True)
    thread.start()
    return thread


def publish(kind: str, payload: dict) -> None:
    """Рассылка событий подписчикам SSE (доска обновляется без перезагрузки)."""
    message = errors_mod.json_dumps({"kind": kind, "at": store.now_iso(), "payload": payload})
    with _subs_lock:
        dead = []
        for q in _subs:
            try:
                q.put_nowait(message)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _subs.remove(q)


#: Виды кадров, которые пускает POST /api/notify: «задача» — о записи в неё
#: сообщает stdio-MCP, у которого своего publish нет (listik-hkdp); «маршрут» —
#: тот, кто пишет таблицу `routes` в обход сервера, так же будит доску
#: (шаг listik-8jgz, порция c).
NOTIFY_KINDS = frozenset({"task", "route"})


def notify_publish(conn: sqlite3.Connection, body: dict) -> dict:
    """Разослать доске «перечитай», ничего не меняя в базе.

    `publish` живёт в процессе сервера, а писать в ту же sqlite можно и мимо него
    (`bin/listik mcp` по stdio): без такого вызова доска показывала бы старое до
    перезагрузки. `kind="route"` — кадр по всей таблице
    маршрутов, задачи он не касается и не проверяет: `key` в нём необязателен
    (`None` — «перечитай список целиком»). `kind="task"` (по умолчанию) требует
    `task_id` и существующую задачу — иначе кадр будил бы доски ради записи,
    которой сервер не знает.
    """
    kind = str(body.get("kind") or "task").strip() or "task"
    if kind not in NOTIFY_KINDS:
        raise ApiError(400, f"неизвестный kind: {kind}")
    action = str(body.get("action") or "notify").strip()[:200] or "notify"
    if kind == "route":
        key = body.get("key")
        key = str(key).strip() or None if key is not None else None
        publish("route", {"key": key, "action": action})
        return {"published": True, "kind": "route", "key": key, "action": action}
    task_id = str(body.get("task_id") or body.get("id") or "").strip()
    if not task_id:
        raise ApiError(400, "не передан обязательный параметр: task_id")
    if conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
        raise ApiError(404, f"задача не найдена: {task_id}")
    publish(kind, {"id": task_id, "action": action})
    return {"published": True, "kind": kind, "id": task_id, "action": action}


# ------------------------------------------------------------------ утилиты

def as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on")


def as_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        # Машинный код ошибки (см. listik/errors.py): CLI и агенты ветвятся по нему,
        # а не по HTTP-статусу. Явный code важнее статуса: «задача уже удерживается» —
        # это 400 по контракту API, но по смыслу conflict.
        self.code = code or errors_mod.code_for_status(status)


def api_error(status: int, exc: BaseException) -> ApiError:
    """ApiError по пойманному исключению: сообщение без кавычек KeyError + код по типу.

    Статус здесь не перебивает код: `NotFound` — «не найдено» (404), голый
    `KeyError` словаря (нет поля у карточки) — наша ошибка, и `error_response`
    отдаст по ней 500/internal, а не «проверь идентификатор» (listik-xut1).
    """
    err = errors_mod.as_error(exc)
    return ApiError(status, err.message, code=err.code)


def error_response(exc: BaseException) -> tuple[int, str, str]:
    """HTTP-ответ по исключению обработчика: (статус, сообщение, код).

    ApiError отдаётся как есть. Ошибка базы — 503 и запись в health: подменённый
    или повреждённый файл нельзя показывать как «ошибка операции» на доске
    (listik-cfzk), а закрытое соединение (`ProgrammingError` после переоткрытия)
    значит «повтори запрос». Нарушение ограничений (IntegrityError) — это логика
    приложения, а не файл базы, и остаётся 500.

    Непойманный `KeyError` (обращение к отсутствующему ключу словаря) сюда и
    попадает: обработчики ловят только `errors.NotFound`, поэтому баг не
    притворяется 404 («проверь идентификатор»), а честно отдаётся как 500/internal
    с текстом без кавычек (listik-xut1).
    """
    if isinstance(exc, ApiError):
        return exc.status, exc.message, exc.code
    if isinstance(exc, errors_mod.BadArgument):
        # Негодный аргумент — 400 даже там, где обработчик ValueError не ловит
        # (чтения: /api/tasks, /api/board, /api/ready с неизвестным владельцем).
        return 400, str(exc), errors_mod.BAD_ARGUMENT
    if isinstance(exc, errors_mod.Forbidden):
        # Чужой владелец: обработчики её не ловят (это PermissionError, а не
        # KeyError/ValueError), и до 500 доходить она не должна.
        return 403, str(exc), errors_mod.FORBIDDEN
    if isinstance(exc, sqlite3.IntegrityError):
        return 500, f"{type(exc).__name__}: {exc}", errors_mod.INTERNAL
    if isinstance(exc, (sqlite3.DatabaseError, sqlite3.ProgrammingError)):
        _background_db_error("http", exc)
        return 503, (f"база Listik недоступна ({type(exc).__name__}: {exc}); "
                     "соединения переоткрыты — повтори запрос, состояние: listik status"), \
            errors_mod.SERVER_ERROR
    # KeyError печатаем без кавычек: `str(KeyError("нет"))` даёт `"'нет'"`.
    text = errors_mod.message_of(exc) if isinstance(exc, KeyError) else str(exc)
    return 500, f"{type(exc).__name__}: {text}", errors_mod.INTERNAL


def _check_role_launchers(roles) -> None:
    """Отказать, если роль ссылается на скил-запускатор, которого нет у этой установки.

    Смысл проверки — поймать опечатку в ключе до записи в базу: формат `плагин:скил`
    проверяет схема (`listik/routes.py`), а существование скила зависит от машины и
    потому проверяется только здесь.  Каталога запускаторов нет вовсе (установка без
    `plugins/`) — проверки нет: иначе на такой машине нельзя было бы записать ни один
    расклад.
    """
    if not isinstance(roles, dict) or not skills_mod.launchers_available():
        return
    known = set(skills_mod.launcher_keys())
    for role, cell in roles.items():
        if not isinstance(cell, dict):
            continue
        skill = cell.get("skill")
        if isinstance(skill, str) and skill not in known:
            raise ApiError(400, f"roles.{role}.skill: скила-запускатора {skill!r} нет; "
                           f"доступны: {', '.join(sorted(known))}",
                           code=errors_mod.BAD_ARGUMENT)


def need(body: dict, key: str):
    value = body.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ApiError(400, f"не передан обязательный параметр: {key}")
    return value


# ------------------------------------------------------------------ обработчики

def runtime_info(root: Path | None = None) -> dict:
    """Откуда запущен сервер: каталог кода, cwd и не чужой ли это worktree (listik-i23u).

    Сервер, поднятый из связанного git worktree, работает на коде ветки задачи —
    это надо видеть в `/api/health` и `listik status`.
    """
    root = Path(root or paths.ROOT_DIR)
    info = {"code_dir": str(root), "data_dir": str(paths.DATA_DIR), "cwd": os.getcwd(),
            "worktree": False, "main_repo": None, "warning": None}
    git_dir = store._git_value(root, "rev-parse", "--absolute-git-dir")
    common = store._git_value(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if git_dir and common and Path(git_dir).resolve() != Path(common).resolve():
        info["worktree"] = True
        info["main_repo"] = str(Path(common).resolve().parent)
        info["warning"] = (f"сервер запущен из git worktree {root}, а не из основного "
                           f"репозитория {info['main_repo']} — код ветки задачи; "
                           "перезапусти из основного каталога")
    return info


def handle(method: str, path: str, query: dict, body: dict, authed: bool = False,
           owner: str | None = None) -> tuple[int, object]:
    """`owner` — идентичность запроса из заголовка `X-Listik-Owner` (None, если его нет).

    Она уходит в store как `as_owner`; в локальном режиме store её игнорирует.
    У комментария и у вопроса/ответа (`needs-owner`) без явного автора она
    становится автором-человеком: иначе клиент без `author`/`actor` оставлял бы
    запись без автора вовсе.
    """
    conn = get_conn()
    parts = [p for p in path.strip("/").split("/") if p]
    q1 = lambda k, d=None: query.get(k, [d])[0] if isinstance(query.get(k), list) else query.get(k, d)  # noqa: E731

    if path == "/api/health":
        cfg = config_mod.load()
        data = {
            "status": "ok",
            "version": "0.1.0",
            "now": store.now_iso(),
            "embed": {"model": cfg["embed"]["model"]},
            "authed": authed,
            # Только пути установки для диагностики несовпадений даже при отказе
            # токена. Содержимое конфига и подробности базы остаются закрытыми.
            "installation": {
                "code_dir": str(paths.ROOT_DIR.resolve()),
                "data_dir": str(paths.DATA_DIR.resolve()),
                "config_path": str(paths.CONFIG_PATH.resolve()),
            },
        }
        # Режим — без токена: по нему клиент понимает, нужно ли представляться.
        # Список людей и то, как сервер понял заголовок, — только авторизованному.
        data["mode"] = "server" if config_mod.is_server_mode(cfg) else "local"
        if authed:
            data["users"] = config_mod.users(cfg) if config_mod.is_server_mode(cfg) else []
            data["owner"] = owner if config_mod.is_server_mode(cfg) else None
        if authed and _db_error is not None:
            data["db_error"] = _db_error
        # Подмена файла базы (listik-cfzk): не «ошибка сейчас», а факт — висит в
        # status до перезапуска сервера, даже если новые соединения уже работают.
        if authed and _db_replaced is not None:
            data["db_replaced"] = _db_replaced
        # Проба живости отдаётся без токена (по ней CLI понимает, поднят ли сервер),
        # поэтому подробности о базе — только авторизованному.
        if authed:
            routes_state = routes_mod.state(conn)
            data.update({
                "db": str(paths.DB_PATH),
                "runtime": runtime_info(),
                "counts": db_mod.counts(conn),
                "embed": embed_mod.health(cfg["embed"]["model"]),
                "routes": {
                    "ok": routes_state.ok,
                    "error": routes_state.error,
                    "path": routes_state.path,
                    "count": len(routes_state.routes),
                },
            })
        return 200, data

    if path == "/api/routes/launchers":
        # Справочник для редактора состава ролей на доске: какие скилы-запускаторы
        # стоят у этой установки, какие вендоры и роли вообще бывают.
        if method != "GET":
            raise ApiError(405, "метод не поддерживается")
        return 200, {"skills_available": skills_mod.launchers_available(),
                     "launchers": skills_mod.launchers(),
                     "providers": list(routes_mod.PROVIDERS),
                     "roles": list(routes_mod.ROLE_KEYS)}

    if path == "/api/routes":
        # Данные — из таблицы `routes`: правка записи в базе видна сразу, перезапуск
        # сервера не нужен. `command` отдаётся, как и всё в /api/*, — только по токену.
        if method == "GET":
            return 200, routes_store.routes_response(conn)
        if method == "POST":
            unknown = [k for k in body if k not in ("key", "roles")]
            if unknown:
                raise ApiError(400, f"поле нельзя передать: {unknown[0]}",
                               code=errors_mod.BAD_ARGUMENT)
            key = str(need(body, "key")).strip()
            info = skills_mod.skill_info(key)
            if info is None:
                raise ApiError(400, f"скила {key!r} нет среди "
                               f"plugins/feature-pipeline/skills", code=errors_mod.BAD_ARGUMENT)
            try:
                routes_store.get_route(conn, key)
            except errors_mod.NotFound:
                pass
            else:
                raise ApiError(409, f"маршрут {key!r} уже есть", code=errors_mod.CONFLICT)
            roles = body.get("roles")
            if roles is not None:
                # Ключа нет или `null` — запись с пустым раскладом, как раньше.
                # Явный `{}` — отказ: у файла и у PATCH пустой расклад тоже не принимается.
                if roles == {}:
                    raise ApiError(400, "roles: нужна хотя бы одна роль",
                                   code=errors_mod.BAD_ARGUMENT)
                _check_role_launchers(roles)
            try:
                record = routes_store.create_route(
                    conn, key=key, kind="pipeline", title=info["title"], hint=info["hint"],
                    icon=routes_mod.fallback_icon("pipeline", key), visible=False,
                    harness=None, command=None, roles=roles)
            except ValueError as exc:
                raise ApiError(400, errors_mod.message_of(exc),
                               code=errors_mod.BAD_ARGUMENT) from exc
            publish("route", {"key": key, "action": "created"})
            return 201, record
        raise ApiError(405, "метод не поддерживается")

    if path == "/api/routes/sync":
        if method != "GET":
            raise ApiError(405, "метод не поддерживается")
        return 200, routes_store.sync_report(conn)

    if path == "/api/routes/reorder":
        if method != "POST":
            raise ApiError(405, "метод не поддерживается")
        unknown = [k for k in body if k != "keys"]
        if unknown:
            raise ApiError(400, f"поле нельзя передать: {unknown[0]}",
                           code=errors_mod.BAD_ARGUMENT)
        keys = need(body, "keys")
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise ApiError(400, "keys: должен быть массивом строк", code=errors_mod.BAD_ARGUMENT)
        try:
            records = routes_store.reorder(conn, keys)
        except ValueError as exc:
            raise ApiError(400, errors_mod.message_of(exc), code=errors_mod.BAD_ARGUMENT) from exc
        publish("route", {"key": None, "action": "reordered"})
        return 200, records

    if len(parts) == 3 and parts[0] == "api" and parts[1] == "routes":
        key = urllib.parse.unquote(parts[2])
        if method == "PATCH":
            unknown = [k for k in body if k not in routes_store.UPDATE_FIELDS]
            if unknown:
                raise ApiError(400, f"поле нельзя менять: {unknown[0]}",
                               code=errors_mod.BAD_ARGUMENT)
            if not body:
                raise ApiError(400, "нечего менять", code=errors_mod.BAD_ARGUMENT)
            if "roles" in body:
                _check_role_launchers(body["roles"])
            try:
                record = routes_store.update_route(conn, key, **body)
            except errors_mod.NotFound as exc:
                raise api_error(404, exc) from exc
            except ValueError as exc:
                raise ApiError(400, errors_mod.message_of(exc),
                               code=errors_mod.BAD_ARGUMENT) from exc
            publish("route", {"key": key, "action": "updated"})
            return 200, record
        if method == "DELETE":
            try:
                routes_store.get_route(conn, key)
            except errors_mod.NotFound as exc:
                raise api_error(404, exc) from exc
            # Удаление справочной записи, а не смена маршрута задачи: снимаем
            # launch_route/метки у всех задач без гарда route_change_denied
            # (store.clear_route_on_route_removed), иначе половина из них,
            # взятых в работу, получила бы отказ, а ссылка на удалённый
            # маршрут осталась бы висеть.
            tasks_cleared = store.clear_route_on_route_removed(conn, key)
            routes_store.delete_route(conn, key)
            publish("route", {"key": key, "action": "removed"})
            return 200, {"removed": key, "tasks_cleared": tasks_cleared}
        raise ApiError(405, "метод не поддерживается")

    if path == "/api/assistant/status":
        # Настроен ли помощник: без api_key в [assistant] доска прячет кнопки.
        # `voice` — голосовой ввод: нужны оба ключа, [assistant] и [deepgram].
        # Ключи наружу не отдаются — только факт их наличия.
        cfg = config_mod.load()
        data = assistant_mod.status(cfg)
        data["voice"] = voice_mod.available(cfg)
        return 200, data

    if path == "/api/meta":
        return 200, {
            "projects": store.list_projects(conn, include_archived=as_bool(q1("archived", False))),
            "actors": store.list_actors(conn),
            "facets": store.facet_values(conn),
            "statuses": store.STATUS_TITLES,
            "stages": store.STAGE_TITLES,
            "priorities": store.PRIORITY_TITLES,
        }

    # --- репозитории (проекты) для настроек доски: добавить, скрыть, убрать
    if path == "/api/projects":
        if method == "GET":
            return 200, {"projects": store.list_all_projects(conn),
                         "root": str(paths.PROJECTS_ROOT)}
        if method == "POST":
            try:
                project = store.add_project(
                    conn, path=body.get("path"), slug=body.get("slug"),
                    title=body.get("title"), kind=body.get("kind") or "native")
            except ValueError as exc:
                # Всё, на чём падает add_project, — доводы запроса (нет каталога,
                # относительный path, пустой slug), а не конфликт состояния:
                # code_of(ValueError) дал бы conflict, поэтому код явный.
                raise ApiError(400, errors_mod.message_of(exc),
                               code=errors_mod.BAD_ARGUMENT) from exc
            publish("project", {"slug": project["slug"],
                                "action": "created" if project.get("created") else "updated"})
            return 201, project

    if len(parts) == 3 and parts[0] == "api" and parts[1] == "projects":
        slug = urllib.parse.unquote(parts[2])
        if method in ("PATCH", "PUT"):
            try:
                project = store.update_project(
                    conn, slug, title=body.get("title"), path=body.get("path"),
                    color=body.get("color"), archived=body.get("archived"),
                    kind=body.get("kind"), routing=body.get("routing"))
            except errors_mod.NotFound as exc:
                raise api_error(404, exc) from exc
            except ValueError as exc:
                raise api_error(400, exc) from exc
            publish("project", {"slug": slug, "action": "updated"})
            return 200, project
        if method == "DELETE":
            try:
                # force можно передать и телом, и строкой запроса: DELETE с телом
                # поддерживают не все клиенты, а удаление проекта с задачами — редкое
                force = as_bool(body.get("force", False)) or as_bool(q1("force", False))
                out = store.remove_project(conn, slug, force=force)
            except errors_mod.NotFound as exc:
                raise api_error(404, exc) from exc
            except ValueError as exc:
                raise api_error(409, exc) from exc
            publish("project", {"slug": slug, "action": "removed"})
            return 200, out

    if path == "/api/stats":
        return 200, store.stats(conn, project=q1("project"))

    if path == "/api/board":
        return 200, store.board(
            conn,
            group_by=q1("group_by", "status"),
            project=q1("project"),
            include_closed=as_bool(q1("include_closed", False)),
            limit_per_column=as_int(q1("limit"), 300) or 300,
            as_owner=owner,
        )

    if path == "/api/ready":
        return 200, {
            "tasks": deps_mod.ready_tasks(
                conn, project=q1("project"), stage=q1("stage"), harness=q1("harness"),
                include_occupied=as_bool(q1("include_occupied", False)),
                limit=as_int(q1("limit"), 50) or 50, as_owner=owner),
            "cycles": deps_mod.cycles(conn),
            "generated_at": store.now_iso(),
        }

    if path == "/api/blocked":
        return 200, {
            "tasks": deps_mod.blocked_tasks(
                conn, project=q1("project"), limit=as_int(q1("limit"), 100) or 100),
            "generated_at": store.now_iso(),
        }

    if path == "/api/deps/suggested":
        return 200, {
            "items": deps_mod.suggested(conn, project=q1("project"),
                                        limit=as_int(q1("limit"), 100) or 100),
            "generated_at": store.now_iso(),
        }

    if path == "/api/timeline":
        return 200, {"items": store.task_timeline(
            conn, limit=as_int(q1("limit"), 100) or 100, project=q1("project"))}

    if path == "/api/tasks" and method == "GET":
        return 200, store.list_tasks(
            conn,
            project=q1("project"), status=q1("status"), stage=q1("stage"),
            assignee=q1("assignee"), holder=q1("holder"),
            needs_owner=as_bool(q1("needs_owner", False)),
            issue_type=q1("type"), label=q1("label"), text=q1("text"),
            include_closed=as_bool(q1("include_closed", False)),
            include_archived=as_bool(q1("include_archived", False)),
            limit=as_int(q1("limit"), 200) or 200, offset=as_int(q1("offset"), 0) or 0,
            order=q1("order", "updated") or "updated",
            as_owner=owner,
        )

    if path == "/api/tasks" and method == "POST":
        autostart = as_bool(body.get("autostart", False))
        route = body.get("route")
        if route is not None and not isinstance(route, str):
            raise ApiError(400, "route должен быть строкой")
        parent = body.get("parent")
        if parent is not None and not isinstance(parent, str):
            raise ApiError(400, "parent должен быть строкой")
        discovered_from = body.get("discovered_from")
        if discovered_from is not None and not isinstance(discovered_from, str):
            raise ApiError(400, "discovered_from должен быть строкой")
        # Автостарт без маршрута запускать нечего: задача не создаётся вовсе.
        if autostart and not (route or "").strip():
            raise ApiError(400, "autostart: нужен непустой route")
        try:
            task = store.create_task(
                conn,
                title=need(body, "title"),
                project=body.get("project"),
                description=body.get("description", ""),
                acceptance=body.get("acceptance", ""),
                design=body.get("design", ""),
                notes=body.get("notes", ""),
                issue_type=body.get("type") or body.get("issue_type") or "task",
                status=body.get("status", "open"),
                priority=as_int(body.get("priority"), 2),
                assignee=body.get("assignee"),
                stage=body.get("stage"),
                labels=body.get("labels") or [],
                spec_path=body.get("spec_path"),
                checklist_path=body.get("checklist_path"),
                review_path=body.get("review_path"),
                decision_path=body.get("decision_path"),
                journal_path=body.get("journal_path"),
                external_ref=body.get("external_ref"),
                source=body.get("source", "native"),
                task_id=body.get("id"),
                # Поле `owner` — «на кого» заводим; заголовок — «кто заводит».
                owner=body.get("owner"),
                as_owner=owner,
                created_by=body.get("actor") or body.get("created_by"),
                needs_owner=as_bool(body.get("needs_owner", False)),
                harness=body.get("harness"),
                autostart=autostart,
                route=route,
                parent=parent or None,
                discovered_from=discovered_from or None,
                # Подсказка «упомянутые id без связи» нужна тому, кто завёл карточку.
                hints=True,
            )
        except KeyError as exc:  # NotFound — подкласс KeyError
            # Указан несуществующий parent/discovered_from: задача не создана.
            raise api_error(404, exc) from exc
        except ValueError as exc:
            raise api_error(400, exc) from exc
        if autostart:
            # Процесс не ждём: start возвращается сразу после Popen, отказ (нет
            # маршрута/command/каталога) не отменяет создание задачи.
            launcher_mod.start(conn, task["id"], notify=publish)
            task = store.get_task(conn, task["id"])
        publish("task", {"id": task["id"], "action": "created"})
        return 201, task

    if len(parts) >= 2 and parts[0] == "api" and parts[1] == "tasks":
        if len(parts) == 5 and parts[3] == "documents":
            # Документы задачи содержимым: на удалённом сервере файлов проектов нет,
            # поэтому текст приезжает телом запроса и читается из базы.
            tid, kind = parts[2], urllib.parse.unquote(parts[4])
            from . import documents
            if method == "PUT":
                content = body.get("content")
                if not isinstance(content, str):
                    raise ApiError(400, "не передан обязательный параметр: content")
                try:
                    out = documents.put_document(conn, tid, kind, content,
                                                 path=body.get("path"), actor=body.get("actor"))
                except errors_mod.NotFound as exc:
                    raise api_error(404, exc) from exc
                except ValueError as exc:
                    raise api_error(400, exc) from exc
                publish("task", {"id": tid, "action": "document"})
                return 200, out
            if method == "GET":
                try:
                    out = documents.get_document(conn, tid, kind)
                except errors_mod.NotFound as exc:
                    raise api_error(404, exc) from exc
                except ValueError as exc:
                    raise api_error(400, exc) from exc
                return 200, out
            raise ApiError(405, "метод не поддерживается")
        if len(parts) == 5 and parts[3] == "deps" and method == "DELETE":
            tid, dep_id = parts[2], urllib.parse.unquote(parts[4])
            out = store.remove_dep(conn, tid, dep_id, dep_type=q1("dep_type"))
            publish("task", {"id": tid, "action": "deps"})
            return 200, out
        if len(parts) == 3:
            tid = parts[2]
            if method == "GET":
                try:
                    task = store.get_task(conn, tid, with_details=as_bool(q1("details", True)))
                    if as_bool(q1("deps", True)):
                        task["deps_state"] = deps_mod.ready(conn, tid)
                    return 200, task
                except errors_mod.NotFound as exc:
                    raise api_error(404, exc) from exc
            if method in ("PATCH", "PUT"):
                # `route` — то же поле, что колонка `launch_route`: так маршрут
                # называет создание задачи, доска шлёт его же. Смена разрешена
                # только пока работа не началась — отказ даёт store (400).
                fields = {k: v for k, v in body.items()
                          if k in store.UPDATABLE or k == store.ROUTE_ALIAS}
                if store.ROUTE_ALIAS in fields and store.ROUTE_FIELD not in fields:
                    fields[store.ROUTE_FIELD] = fields.pop(store.ROUTE_ALIAS)
                try:
                    task = store.update_task(conn, tid, actor=body.get("actor"),
                                             harness=body.get("harness"),
                                             note=body.get("note"), as_owner=owner,
                                             **fields)
                except errors_mod.NotFound as exc:
                    raise api_error(404, exc) from exc
                except ValueError as exc:
                    # store refuses changes the current state does not allow —
                    # with the route that is "работа уже началась".
                    raise api_error(400, exc) from exc
                publish("task", {"id": tid, "action": "updated"})
                return 200, task
            if method == "DELETE":
                store.delete_task(conn, tid)
                publish("task", {"id": tid, "action": "deleted"})
                return 200, {"deleted": tid}
        if len(parts) == 4:
            tid, action = parts[2], parts[3]
            if action == "context" and method == "GET":
                try:
                    from . import documents
                    out = documents.context(conn, tid, q1("stage") or "s1-spec",
                                             portion=q1("portion"),
                                             max_chars=as_int(q1("max_chars"), None))
                except errors_mod.NotFound as exc:
                    raise api_error(404, exc) from exc
                return 200, out
            try:
                if action == "claim":
                    out = store.claim(conn, tid, holder=need(body, "holder"),
                                      harness=body.get("harness"), note=body.get("note"),
                                      actor=body.get("actor"), as_owner=owner,
                                      force=as_bool(body.get("force", False)))
                elif action == "heartbeat":
                    out = store.heartbeat(conn, tid, holder=need(body, "holder"),
                                          note=body.get("note"), harness=body.get("harness"),
                                          actor=body.get("actor"), as_owner=owner)
                elif action == "stage":
                    out = store.next_stage(conn, tid, holder=body.get("holder"),
                                           note=body.get("note"), harness=body.get("harness"),
                                           actor=body.get("actor"), as_owner=owner,
                                           to_stage=body.get("to") or body.get("stage"))
                elif action == "comment":
                    # Явный автор (`--actor`/`author`) сильнее: агент остаётся
                    # `agent:<имя>`. Без автора комментарий приписываем человеку,
                    # представившемуся заголовком `X-Listik-Owner`, — а не держателю
                    # карточки, которым часто оказывается агент (listik-015y).
                    out = store.add_comment(conn, tid, need(body, "text"),
                                            author=body.get("author") or body.get("actor") or owner,
                                            kind=body.get("kind", "comment"),
                                            harness=body.get("harness"))
                elif action == "deps" and body.get("depends_on"):
                    out = store.add_dep(conn, tid, need(body, "depends_on"),
                                        body.get("dep_type", "blocks"), body.get("actor"), confirm=as_bool(body.get("confirm", False)))
                elif action == "deps":
                    out = deps_mod.graph(conn, tid, depth=as_int(body.get("depth"), 3) or 3)
                elif action == "ready":
                    out = deps_mod.ready(conn, tid)
                elif action == "mentions":
                    out = deps_mod.mentioned(conn, tid, limit=as_int(body.get("limit"), 50) or 50)
                elif action == "needs-owner":
                    # Вопрос/ответ — такой же комментарий в истории, поэтому автор
                    # берётся тем же правилом, что и у `comment` (listik-z0sd):
                    # явный `actor` агента сильнее, а без него подписываем человека
                    # из заголовка `X-Listik-Owner`, а не оставляем запись без автора.
                    out = store.set_needs_owner(conn, tid, value=as_bool(body.get("value", True)),
                                                text=body.get("note"),
                                                actor=body.get("actor") or owner,
                                                harness=body.get("harness"))
                elif action == "release":
                    out = store.update_task(conn, tid, actor=body.get("actor"),
                                            holder="", note=body.get("note") or "освободил")
                elif action == "done":
                    out = store.update_task(conn, tid, actor=body.get("actor"),
                                            status="done", stage="done",
                                            result=body.get("result", ""),
                                            close_reason=body.get("reason") or body.get("result"),
                                            note=body.get("note"))
                else:
                    raise ApiError(404, f"неизвестное действие: {action}")
            except errors_mod.NotFound as exc:
                raise api_error(404, exc) from exc
            except ValueError as exc:
                raise api_error(400, exc) from exc
            # Читающие действия ходят тем же путём (граф зависимостей, ready,
            # упоминания), но доску не меняют: событие шлём только от записей,
            # иначе чтение карточки будило бы все открытые доски (listik-1p86).
            if action not in ("ready", "mentions") and not (
                    action == "deps" and not body.get("depends_on")):
                publish("task", {"id": tid, "action": action})
            return 200, out

    if path == "/api/search":
        query_text = q1("q") or q1("query") or ""
        if not query_text:
            raise ApiError(400, "нужен параметр q")
        res = search_mod.search(
            conn, query_text,
            limit=as_int(q1("limit"), 15) or 15,
            project=q1("project"), status=q1("status"), stage=q1("stage"),
            actor=q1("actor"), needs_owner=as_bool(q1("needs_owner", False)),
            mode=q1("mode", "hybrid") or "hybrid",
        )
        return 200, res

    if path == "/api/memory":
        q = q1("q")
        if q:
            items = search_mod.search_memories(
                conn, q, limit=as_int(q1("limit"), 20) or 20, project=q1("project"),
                mode=q1("mode", "hybrid") or "hybrid")
            return 200, items
        rows = conn.execute(
            "SELECT key, project, body, updated_at FROM memories "
            "ORDER BY updated_at DESC LIMIT ?", (as_int(q1("limit"), 50) or 50,)).fetchall()
        return 200, [dict(r) for r in rows]

    if path == "/api/assistant/suggest" and method == "POST":
        # Помощник DeepSeek: ключ читается из config.toml на сервере и в браузер
        # не уходит. Маршрут предлагается только из записей таблицы `routes`.
        try:
            return 200, assistant_mod.suggest(
                body.get("field"), body.get("text", ""), body.get("context"),
                routes=routes_store.list_routes(conn))
        except assistant_mod.AssistantError as exc:
            raise ApiError(exc.status, exc.message, exc.code) from exc

    if path == "/api/assistant/transcribe" and method == "POST":
        # Голос: запись приходит в JSON как base64 (сырые байты в JSON не влезают).
        # Ключ Deepgram читается на сервере и в браузер не уходит; размер записи
        # проверяется уже после декодирования.
        raw_audio = body.get("audio_base64")
        if not isinstance(raw_audio, str):
            raise ApiError(400, "нужен audio_base64", code=errors_mod.BAD_ARGUMENT)
        try:
            audio = base64.b64decode(raw_audio, validate=True)
        except ValueError as exc:
            raise ApiError(400, f"невалидный base64: {exc}",
                           code=errors_mod.BAD_ARGUMENT) from exc
        try:
            return 200, voice_mod.transcribe(audio, body.get("mime") or "")
        except assistant_mod.AssistantError as exc:
            raise ApiError(exc.status, exc.message, exc.code) from exc

    if path == "/api/assistant/draft" and method == "POST":
        # Черновик задачи из рассказа: проекты и маршруты сервер берёт сам из базы.
        # Ничего не создаётся: ответ — только черновик для формы.
        try:
            return 200, voice_mod.draft(body.get("text", ""),
                                        projects=store.list_projects(conn),
                                        routes=routes_store.list_routes(conn))
        except assistant_mod.AssistantError as exc:
            raise ApiError(exc.status, exc.message, exc.code) from exc

    if path == "/api/embed" and method == "POST":
        cfg = config_mod.load()
        res = embed_mod.embed_pending(
            conn, limit=as_int(body.get("limit"), 0) or 0,
            kinds=body.get("kinds", "task,comment,chunk"),
            model=cfg["embed"]["model"], verbose=False,
        )
        return 200, res

    if path == "/api/notify" and method == "POST":
        # Событие от того, кто писал мимо сервера (stdio-MCP): состояние не
        # меняется, доска просто перечитывает задачу. Токен проверен выше, как у
        # остальных /api/*.
        return 200, notify_publish(conn, body)

    if path == "/api/events":
        limit = as_int(q1("limit"), 50) or 50
        rows = conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return 200, {"items": [dict(r) for r in rows]}

    raise ApiError(404, f"нет такого эндпоинта: {method} {path}")


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "listik/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # тише в консоли
        if self.path.startswith("/api/health"):
            return
        if self.server.quiet:  # type: ignore[attr-defined]
            return
        print(f"  {self.address_string()} {fmt % args}", flush=True)

    # --- helpers
    def _token(self) -> str:
        cfg = config_mod.load()
        return (cfg.get("auth") or {}).get("token", "")

    def _owner(self) -> str | None:
        """Идентичность запроса: `X-Listik-Owner` после strip; пустой заголовок — None."""
        value = (self.headers.get("X-Listik-Owner") or "").strip()
        return value or None

    def _authed(self, query: dict) -> bool:
        token = self._token()
        if not token:
            return True
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:].strip() == token:
            return True
        if self.headers.get("X-Listik-Token") == token:
            return True
        return bool(query.get("token") and query["token"][0] == token)

    def _send(self, status: int, payload: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers",
                         "Authorization,Content-Type,X-Listik-Token,X-Listik-Owner")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,PUT,DELETE,OPTIONS")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, data) -> None:
        self._send(status, errors_mod.json_dumps(data).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, message: str, code: str | None = None) -> None:
        self._json(status, errors_mod.http_error_body(status, message, code))

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = errors_mod.json_loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ApiError(400, f"невалидный JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ApiError(400, "тело запроса должно быть объектом JSON")
        return data

    # --- методы
    def do_OPTIONS(self):  # noqa: N802
        self._send(204, b"", "text/plain")

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        path = parsed.path

        if path == "/mcp":
            # Транспорт MCP — только POST, токен здесь не проверяется.
            payload = errors_mod.json_dumps({"ok": False, "error": "MCP: только POST"}).encode("utf-8")
            return self._send(405, payload, "application/json; charset=utf-8", {"Allow": "POST"})

        if path.startswith("/.well-known/"):
            # MCP-клиент ищет тут OAuth-метаданные и принимает HTML SPA-фолбэка за них.
            return self._error(404, "нет такого эндпоинта")

        if path == "/api/stream":
            if not self._authed(query):
                return self._error(401, "нужен токен")
            return self._stream()

        if path.startswith("/api/"):
            # Проба живости отвечает и без токена: по ней CLI понимает, поднят ли сервер.
            # Но подробности о базе отдаются только авторизованному клиенту.
            authed = self._authed(query)
            if path != "/api/health" and not authed:
                return self._error(401, "нужен токен: Authorization: Bearer <token>")
            try:
                status, data = handle("GET", path, query, {}, authed=authed,
                                      owner=self._owner())
            except Exception as exc:  # noqa: BLE001
                status, message, code = error_response(exc)
                return self._error(status, message, code)
            return self._json(status, {"ok": True, "data": data})

        return self._static(path, query)

    def do_POST(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/mcp":
            # У /mcp своя авторизация, поэтому до общей проверки токена.
            return self._mcp()
        query = urllib.parse.parse_qs(parsed.query)
        if not self._authed(query):
            return self._error(401, "нужен токен")
        try:
            body = self._read_body()
            status, data = handle("POST", parsed.path, query, body, authed=True,
                                  owner=self._owner())
        except Exception as exc:  # noqa: BLE001
            status, message, code = error_response(exc)
            return self._error(status, message, code)
        return self._json(status, {"ok": True, "data": data})

    def do_PATCH(self):  # noqa: N802
        self._write_method("PATCH")

    def do_PUT(self):  # noqa: N802
        self._write_method("PUT")

    def do_DELETE(self):  # noqa: N802
        self._write_method("DELETE")

    def _write_method(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/mcp":
            # PUT/PATCH/DELETE на /mcp — только 405, без проверки токена.
            payload = errors_mod.json_dumps({"ok": False, "error": "MCP: только POST"}).encode("utf-8")
            return self._send(405, payload, "application/json; charset=utf-8", {"Allow": "POST"})
        query = urllib.parse.parse_qs(parsed.query)
        if not self._authed(query):
            return self._error(401, "нужен токен")
        try:
            body = self._read_body()
            status, data = handle(method, parsed.path, query, body, authed=True,
                                  owner=self._owner())
        except Exception as exc:  # noqa: BLE001
            status, message, code = error_response(exc)
            return self._error(status, message, code)
        return self._json(status, {"ok": True, "data": data})

    # --- MCP: минимальное подмножество транспорта Streamable HTTP
    def _mcp(self) -> None:
        """Один POST — одно JSON-RPC-сообщение, ответ обычным JSON, без SSE и сессий.

        Соединение на время запроса берётся из `get_conn()`, разбор сообщения — общий
        с stdio (`mcp.handle`). После успешного ответа пишущие инструменты шлют событие
        доске; на сам ответ событие не влияет и отправляется уже после него.
        """
        def plain(status: int, message: str, extra: dict | None = None) -> None:
            body = errors_mod.json_dumps({"ok": False, "error": message}).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8", extra)

        def rpc_error(status: int, code: int, message: str, rid) -> None:
            body = errors_mod.json_dumps(mcp.rpc_error(rid, code, message)).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        # 1. Защита от DNS rebinding: браузер шлёт Origin, MCP-клиенты — нет.
        if self.headers.get("Origin"):
            self.close_connection = True
            return plain(403, "запросы с Origin к /mcp запрещены", {"Connection": "close"})

        # 2. Своя авторизация: ?token= в строке запроса здесь не принимается.
        token = self._token()
        if token:
            auth = self.headers.get("Authorization", "")
            bearer = auth.startswith("Bearer ") and auth[7:].strip() == token
            if not bearer and self.headers.get("X-Listik-Token") != token:
                self.close_connection = True
                return plain(401, "нужен токен: Authorization: Bearer <token>",
                             {"WWW-Authenticate": 'Bearer realm="listik"',
                              "Connection": "close"})

        # 3. Тело больше 5 МБ не читаем, а соединение закрываем: непрочитанное тело
        # испортило бы следующий запрос на keep-alive.
        length = as_int(self.headers.get("Content-Length"), 0) or 0
        if length > 5 * 1024 * 1024:
            self.close_connection = True
            return plain(413, "тело больше 5 МБ", {"Connection": "close"})

        # 4. Тело и разбор JSON.
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            request = errors_mod.json_loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return rpc_error(400, -32700, f"невалидный JSON: {exc}", None)

        # 5. Один POST — одно сообщение; пакеты и мусор не поддерживаются.
        rid = request.get("id") if isinstance(request, dict) else None
        if isinstance(request, list):
            return rpc_error(400, -32600, "пакетные запросы не поддерживаются", None)
        if not isinstance(request, dict) or not isinstance(request.get("method"), str):
            return rpc_error(400, -32600, "неверный запрос JSON-RPC", rid)

        # 6. Разбор сообщения — тот же, что у stdio.
        try:
            response = mcp.handle(request, conn=get_conn(), owner=self._owner())
        except Exception as exc:  # noqa: BLE001
            status, message, _code = error_response(exc)
            return rpc_error(status, -32603, message, rid)

        # 7. Уведомление — отвечать нечем.
        if response is None:
            return self._send(202, b"", "application/json")

        # 8. Обычный ответ: JSON-RPC как есть, без обёртки ok/data.
        self._json(200, response)

        # 9. Событие доске — уже после ответа; любой сбой здесь на ответ не влияет.
        try:
            event = mcp.notify_event(request, response)
            if event is not None:
                task_id, action = event
                publish("task", {"id": task_id, "action": action})
        except Exception:  # noqa: BLE001 — событие не влияет на ответ MCP
            pass

    # --- статика доски
    def _static(self, path: str, query: dict) -> None:
        """Отдаёт собранную доску (web/dist). Неизвестный путь — SPA-фолбэк на index.html."""
        dist = paths.WEB_DIR / "dist"
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (dist / rel).resolve()
        if not str(target).startswith(str(dist.resolve())) or not target.is_file():
            # Путь с расширением — это файл, а не маршрут приложения: на запрос
            # ассета отвечаем честным 404, а не HTML-страницей с кодом 200.
            # Safari, попросивший /favicon.ico и получивший HTML, считает иконку
            # битой, запоминает отказ и рисует вкладку без фавиконки (listik-5cb0).
            if Path(rel).suffix:
                return self._send(404, b"not found\n", "text/plain; charset=utf-8",
                                  {"Cache-Control": "no-store"})
            target = dist / "index.html"
            if not target.is_file():
                return self._board_not_built()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        payload = target.read_bytes()
        cache = "no-store" if target.name == "index.html" else "max-age=60"
        self._send(200, payload, ctype, {"Cache-Control": cache})

    def _board_not_built(self) -> None:
        html = """<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Listik — доска не собрана</title>
<style>body{font:15px/1.6 -apple-system,system-ui,sans-serif;max-width:720px;margin:12vh auto;padding:0 24px;color:#1c1c1e}
code{background:#f2f2f7;padding:2px 6px;border-radius:4px}pre{background:#f2f2f7;padding:12px;border-radius:8px;overflow:auto}</style>
<h1>API работает, доска ещё не собрана</h1>
<p>Сервер Listik отвечает (проверить: <code>/api/health</code>), но собранного фронта нет.</p>
<pre>cd ~/Projects/Listik/web
npm install
npm run build</pre>
<p>Для разработки доски можно поднять vite: <code>npm run dev</code> → <code>http://127.0.0.1:5173</code>
(запросы к <code>/api</code> проксируются на этот сервер).</p>
<p>Ссылка с токеном: <code>listik token</code></p></html>"""
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", {"Cache-Control": "no-store"})

    # --- SSE
    def _stream(self) -> None:
        q: queue.Queue = queue.Queue(maxsize=100)
        with _subs_lock:
            _subs.append(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(b": listik stream\n\n")
            self.wfile.flush()
            while True:
                try:
                    message = q.get(timeout=15)
                    self.wfile.write(f"data: {message}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _subs_lock:
                if q in _subs:
                    _subs.remove(q)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, quiet: bool = False):
        super().__init__(addr, handler)
        self.quiet = quiet

    def process_request_thread(self, request, client_address):
        # Поток на соединение: его sqlite-соединение закрываем вместе с потоком.
        try:
            super().process_request_thread(request, client_address)
        finally:
            close_thread_conn()


def make_server(host: str, port: int, quiet: bool = False) -> Server:
    return Server((host, port), Handler, quiet=quiet)


def port_holder(port: int) -> tuple[int, str] | None:
    """Кто слушает порт: (pid, командная строка) по lsof, None — никто или lsof нет."""
    import subprocess
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=5).stdout.split()
        pid = int(out[0])
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None
    return pid, cmd


def _cmd_tokens(cmd: str) -> list[str]:
    """Токены командной строки: кавычки учитываем, битую строку разбираем по пробелам."""
    import shlex
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def _is_listik_binary(token: str) -> bool:
    """Токен — имя бинаря Listik: `listik`, `bin/listik`, `/opt/listik/bin/listik`.

    Именно имя (последний компонент пути), а не подстрока: `listik-l2fy/vite` и
    `listik-helper/bin/app` бинарём Listik не являются.
    """
    return not token.startswith("-") and Path(token).name == "listik"


def is_listik_serve(cmd: str) -> bool:
    """Командная строка вида `.../bin/listik serve` — сервер Listik, а не чужой процесс.

    `listik` обязан быть именем бинаря, а `serve` — отдельным токеном после него:
    `vite serve` из каталога `listik-l2fy`, `listik-helper/bin/app serve` и
    `listik --mode server` (`server` — не токен `serve`) сервером Listik не считаются.
    """
    tokens = _cmd_tokens(cmd)
    for i, token in enumerate(tokens):
        if _is_listik_binary(token) and "serve" in tokens[i + 1:]:
            return True
    return False


def bind_or_explain(host: str, port: int, quiet: bool) -> Server:
    """make_server, но занятый порт — понятное сообщение и код 1 вместо трейсбека."""
    import errno
    try:
        return make_server(host, port, quiet=quiet)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        msg = f"порт {host}:{port} уже занят"
        holder = port_holder(port)
        if holder and is_listik_serve(holder[1]):
            msg += (f" другим сервером Listik (pid {holder[0]}, pid-файла нет)\n"
                    f"остановить: listik stop")
        elif holder:
            msg += f" процессом pid {holder[0]}: {holder[1]}\nосвободите порт или задайте другой: --port"
        else:
            msg += "\nосвободите порт или задайте другой: --port"
        raise SystemExit(msg) from None


def pid_file() -> Path:
    return paths.PID_PATH


def write_pid() -> None:
    """Записать pid-файл, создав каталог данных: при LISTIK_HOME его ещё нет."""
    pid_file().parent.mkdir(parents=True, exist_ok=True)
    pid_file().write_text(str(os.getpid()))


def log_file() -> Path:
    return paths.LOG_PATH


def read_pid() -> int | None:
    try:
        pid = int(pid_file().read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def daemonize() -> None:
    """Уйти в фон отдельной сессией, чтобы терминал не убивал сервер."""
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    log_file().parent.mkdir(parents=True, exist_ok=True)
    log = open(log_file(), "a", buffering=1)  # noqa: SIM115 — живёт до конца процесса
    os.dup2(log.fileno(), sys.stdout.fileno())
    os.dup2(log.fileno(), sys.stderr.fileno())
    devnull = open(os.devnull, "rb")  # noqa: SIM115
    os.dup2(devnull.fileno(), sys.stdin.fileno())


def _exit_on_sigterm() -> None:
    """SIGTERM (так останавливает `listik stop`) по умолчанию убивает процесс без раскрутки
    стека, и `finally` в `serve()` не удаляет listik.pid (listik-a7pw). Превращаем сигнал в
    SystemExit(0): `serve_forever` прерывается, `finally` закрывает сокет и убирает pid-файл."""
    def _handler(signum, frame):
        raise SystemExit(0)
    try:
        signal.signal(signal.SIGTERM, _handler)
    except (AttributeError, ValueError):
        pass  # не главный поток — оставляем поведение по умолчанию


def serve(host: str | None = None, port: int | None = None, quiet: bool = False,
          background: bool = False, no_embed: bool = False) -> None:
    # Сервер поднимают и фоновым запуском: без этого SIGHUP от закрытия терминала
    # убивает процесс, и команды агентов молча уходят в локальный режим.
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    except (AttributeError, ValueError):
        pass
    cfg = config_mod.load()
    token = (cfg.get("auth") or {}).get("token") or ""
    if not token:
        cfg, token = config_mod.ensure_token(cfg)
    host = host or cfg["server"]["host"]
    port = int(port or cfg["server"]["port"])

    if background:
        existing = read_pid()
        if existing:
            print(f"сервер уже запущен (pid {existing}); остановить: listik stop")
            return
        # Сокет биндим до fork: после daemonize уже нельзя заводить потоки и
        # открывать соединения в родителе — на macOS fork из многопоточного процесса падает.
        httpd = bind_or_explain(host, port, quiet=True)
        url = f"http://{host}:{port}/?token={token}"
        print(f"Listik в фоне: {url}")
        print(f"лог:  {log_file()}")
        print(f"pid:  {pid_file()}")
        daemonize()
        _exit_on_sigterm()
        write_pid()
        conn = get_conn()
        # После daemonize: сообщение о неудачном ввозе маршрутов должно попасть в listik.log.
        routes_store.ensure_imported(conn)
        launcher_mod.recover(conn, notify=publish)
        # Надзор за файлами базы — до фоновой индексации: подмену нужно заметить,
        # даже если ollama нет и векторы не считаются (listik-cfzk).
        start_db_watch()
        if not no_embed:
            start_embed_worker()
        try:
            httpd.serve_forever()
        finally:
            stop_db_watch()
            httpd.server_close()
            try:
                pid_file().unlink()
            except OSError:
                pass
        return

    # Порт занимаем первым: при занятом порте не трогаем launcher и не заводим потоки.
    httpd = bind_or_explain(host, port, quiet=quiet)
    conn = get_conn()
    routes_store.ensure_imported(conn)
    launcher_mod.recover(conn, notify=publish)
    start_db_watch()
    if not no_embed:
        start_embed_worker()
    url = f"http://{host}:{port}/?token={token}"
    print(f"Listik слушает http://{host}:{port}")
    print(f"доска:          {url}")
    print(f"база:           {paths.DB_PATH}")
    print(f"токен:          {token}")
    print("Ctrl+C — остановить")
    _exit_on_sigterm()
    try:
        write_pid()
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nостановлен")
    finally:
        stop_db_watch()
        httpd.server_close()
        try:
            pid_file().unlink()
        except OSError:
            pass

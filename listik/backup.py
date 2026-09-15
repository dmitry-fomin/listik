"""Штатные резервные копии и восстановление базы Listik (карточка listik-cfzk).

Почему не `cp`: сервер держит базу в режиме WAL, свежие транзакции лежат в
`listik.db-wal`, и копия «на живую» через cp/mv может быть внутренне
несогласованной (файл базы без своего WAL). Хуже того, если подменить или удалить
файлы под работающим сервером, его соединения остаются на удалённых inode:
фоновые потоки бесконечно пишут «database disk image is malformed», а запись уходит
в никуда — до перезапуска (так и случилось 13.09.2026).

Поэтому и backup, и restore идут через sqlite backup API: он снимает согласованный
снимок даже при работающем сервере и пишет один файл без -wal/-shm. restore при
работающем сервере отказывает (`conflict`): сервер держит открытые соединения, и
после подмены файла продолжал бы писать в удалённый inode. Остановить его можно
флагом `--stop` — тогда restore сам погасит демон и дождётся, пока тот отпустит
базу. Перед восстановлением текущая база копируется в
`listik.db.bak-pre-restore-<время>`, а старые -wal/-shm удаляются: они принадлежат
прежнему файлу и после подмены превратились бы в «malformed».
"""
from __future__ import annotations

import os
import signal
import sqlite3
from pathlib import Path

from . import db as db_mod
from . import errors
from . import paths
from . import store
from . import util

#: Как называются копии рядом с базой: listik.db.bak-2026-09-14-105500.
#: Шаблон уже покрыт .gitignore (`listik.db.bak-*`).
BACKUP_MARK = ".bak-"
SIDECARS = ("-wal", "-shm")

#: Сколько секунд ждать остановки сервера перед восстановлением (`--stop`).
STOP_TIMEOUT = 15.0


# ------------------------------------------------------------------ утилиты

def db_path_of(db_path: Path | str | None = None) -> Path:
    """Путь к базе: явный аргумент или общий `paths.DB_PATH` (уважает LISTIK_DB)."""
    return util.path(db_path or paths.DB_PATH)


def _stamp() -> str:
    return util.stamp()


def default_backup_path(db_path: Path | str | None = None) -> Path:
    """Куда пишет `listik backup` без `--out`: рядом с базой, с отметкой времени."""
    db = db_path_of(db_path)
    return db.with_name(f"{db.name}{BACKUP_MARK}{_stamp()}")


def sidecar_paths(db: Path) -> list[Path]:
    return [util.path(str(db) + suffix) for suffix in SIDECARS]


def _unique_path(path: Path) -> Path:
    """Свободное имя: у предохранительной копии не должно быть перезаписи."""
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.name}-{n}")
        if not candidate.exists():
            return candidate
    return path


def same_file(left, right) -> bool:
    """Один и тот же файл на диске (inode), с запасным сравнением путей."""
    try:
        return os.path.samefile(str(left), str(right))
    except OSError:
        return util.resolved(left) == util.resolved(right)


def _open_readonly(path: Path) -> sqlite3.Connection:
    """Соединение только на чтение. URI-форма: путь может содержать пробелы."""
    return sqlite3.connect(util.resolved(path).as_uri() + "?mode=ro", uri=True, timeout=30.0)


def _open_source(path: Path) -> sqlite3.Connection:
    """Открыть базу для чтения, а если WAL осиротел (нет -shm) — дать sqlite его разобрать.

    Read-only соединение не умеет восстанавливать WAL без `-shm` («attempt to write
    a readonly database»), поэтому в этом единственном случае открываем обычное
    соединение: sqlite докатит журнал, ничего не потеряв.
    """
    try:
        return _open_readonly(path)
    except sqlite3.OperationalError:
        return sqlite3.connect(path, timeout=30.0)


def _integrity(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row else "?"


def _is_listik_db(conn: sqlite3.Connection) -> bool:
    try:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    except sqlite3.DatabaseError:
        return False
    return "tasks" in names


def _copy_db(src: sqlite3.Connection, dst_path: Path) -> None:
    """Согласованная копия через sqlite backup API (не копирование файла).

    После копии база переводится в journal_mode=DELETE: заголовок копии достаётся
    от WAL-базы, и без этого рядом с резервной копией остались бы свои -wal/-shm —
    лишние файлы, которые при следующем открытии могли бы «восстанавливаться».
    `db.connect` вернёт WAL при первом же открытии копии сервером.
    """
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
        dst.commit()
        dst.execute("PRAGMA journal_mode = DELETE").fetchone()
    finally:
        dst.close()


def _fsync_dir(path: Path) -> None:
    """Чтобы после os.replace подмена пережила падение машины."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


# ------------------------------------------------------------------ работающий сервер

def running_server(db_path: Path | str | None = None) -> dict | None:
    """Сервер Listik, который держит именно эту базу: `{pid, how}` или None.

    Два независимых признака: живой /api/health, который сам называет путь к своей
    базе (надёжно при LISTIK_DB и нестандартном конфиге), и pid-файл/порт установки
    по умолчанию — на случай, когда токен не принят и health не отдаёт подробностей.
    """
    from . import client
    from . import server as server_mod

    db = db_path_of(db_path)
    cfg = util.load_config()
    host = cfg["server"]["host"]
    port = int(cfg["server"]["port"])

    health = client.health(host, port)
    if health and health.get("authed") and health.get("db") \
            and same_file(health["db"], db):
        how = f"http://{host}:{port}"
        pid = server_mod.read_pid()
        if pid:
            return {"pid": pid, "how": how}
        # pid-файла нет (сервер поднят вручную или его удалили) — ищем по порту.
        holder = server_mod.port_holder(port)
        if holder and server_mod.is_listik_serve(holder[1]):
            return {"pid": holder[0], "how": f"{how} (порт {port})"}
        return {"pid": None, "how": how}

    # pid-файл и порт описывают базу установки, а не любую (LISTIK_DB) — иначе
    # restore временной базы отказывал бы из-за постороннего сервера. База установки —
    # та же, что у CLI и сервера, `paths.DB_PATH` (LISTIK_HOME и LISTIK_DB учтены);
    # рядом исторический якорь `ROOT_DIR/listik.db`: без LISTIK_HOME это ровно она,
    # а `test_backup_restore` подменяет ROOT_DIR уже после импорта модуля.
    if same_file(db, paths.DB_PATH) or same_file(db, paths.ROOT_DIR / "listik.db"):
        pid = server_mod.read_pid()
        if pid:
            return {"pid": pid, "how": "listik.pid"}
        holder = server_mod.port_holder(port)
        if holder and server_mod.is_listik_serve(holder[1]):
            return {"pid": holder[0], "how": f"порт {port}"}
    return None


def _process_command(pid: int) -> str | None:
    """Команда процесса: "" — процесса нет, None — проверить не удалось (нет ps)."""
    import subprocess
    try:
        proc = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip()


def stop_server(info: dict, *, timeout: float = STOP_TIMEOUT) -> dict:
    """Погасить сервер Listik и дождаться, пока он отпустит базу.

    Убиваем только процесс, который действительно `listik serve`: pid-файл мог
    устареть, а посылать SIGTERM чужому процессу нельзя. Если проверить нечем
    (в окружении нет `ps`), честно отказываем — молча решить, что сервер уже
    остановлен, значит подменить базу под работающим сервером.
    """
    from . import server as server_mod

    pid = info.get("pid")
    if not pid:
        raise errors.ListikError(
            "работает сервер Listik, но его pid не удалось определить",
            code=errors.CONFLICT, hint="останови его вручную: listik stop")
    cmd = _process_command(int(pid))
    if cmd is None:
        raise errors.ListikError(
            f"не удалось проверить, что pid {pid} — это `listik serve` (ps недоступен)",
            code=errors.CONFLICT,
            hint="останови сервер сам: listik stop; потом повтори restore без --stop")
    if not cmd:
        return {"stopped": False, "pid": int(pid), "note": "процесс уже завершился"}
    if not server_mod.is_listik_serve(cmd):
        raise errors.ListikError(
            f"pid {pid} — это не `listik serve`, а «{cmd}»",
            code=errors.CONFLICT,
            hint="останови сервер вручную: listik stop (или проверь listik.pid)")
    try:
        os.kill(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        return {"stopped": False, "pid": int(pid), "note": "процесс уже завершился"}
    except PermissionError as exc:
        raise errors.ListikError(
            f"нет прав остановить сервер (pid {pid}): {exc}",
            code=errors.CONFLICT, hint="останови его сам: listik stop") from exc

    deadline = util.monotonic() + timeout
    while util.monotonic() < deadline:
        if not _alive(int(pid)):
            return {"stopped": True, "pid": int(pid), "how": info.get("how")}
        util.sleep(0.1)
    raise errors.ListikError(
        f"сервер (pid {pid}) не остановился за {timeout:.0f} с",
        code=errors.CONFLICT,
        hint="восстановление не выполнено; проверь сервер и останови его: listik stop")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# ------------------------------------------------------------------ backup

def backup(db_path: Path | str | None = None, out_path: Path | str | None = None, *,
           overwrite: bool = False) -> dict:
    """Согласованная копия базы через sqlite backup API.

    Работает и при живом сервере: backup API читает базу в одной транзакции, поэтому
    в копию попадают и данные из WAL. Копия проверяется `PRAGMA integrity_check`,
    битая — удаляется, чтобы её нельзя было принять за резервную.
    """
    db = db_path_of(db_path)
    if not db.exists():
        raise errors.ListikError(
            f"базы нет: {db}", code=errors.NOT_FOUND,
            hint="создать базу: listik init (или укажи LISTIK_DB)")
    out = util.expanduser(out_path) if out_path else default_backup_path(db)
    if out.is_dir():
        raise errors.ListikError(
            f"по пути копии каталог: {out}", code=errors.BAD_ARGUMENT,
            hint="укажи файл: listik backup --out <файл>")
    if out.exists() and not overwrite:
        raise errors.ListikError(
            f"файл уже есть: {out}", code=errors.CONFLICT,
            hint="выбери другое имя (--out) или разреши перезапись (--force)")

    out.parent.mkdir(parents=True, exist_ok=True)
    src = _open_source(db)
    try:
        _copy_db(src, out)
        check = _open_readonly(out)
        try:
            integrity = _integrity(check)
            counts = db_mod.counts(check)
        finally:
            check.close()
    except sqlite3.Error as exc:
        out.unlink(missing_ok=True)
        raise errors.ListikError(
            f"копию снять не удалось: {type(exc).__name__}: {exc}",
            code=errors.SERVER_ERROR,
            hint="проверь базу и диск: listik status; подробности в listik.log") from exc
    finally:
        src.close()

    if integrity != "ok":
        out.unlink(missing_ok=True)
        raise errors.ListikError(
            f"копия получилась битой: integrity_check вернул «{integrity}»",
            code=errors.SERVER_ERROR,
            hint="похоже, база повреждена — проверь диск и listik.db; копия удалена")
    _fsync_dir(out.parent)
    return {
        "ok": True,
        "db": str(db),
        "backup": str(out),
        "bytes": out.stat().st_size,
        "integrity": integrity,
        "counts": counts,
        "created_at": store.now_iso(),
    }


# ------------------------------------------------------------------ restore

def restore(backup_path: Path | str, db_path: Path | str | None = None, *,
            stop: bool = False, force: bool = False) -> dict:
    """Восстановить базу из копии, сделанной `listik backup`.

    При работающем сервере отказывает (`conflict`), потому что его соединения
    остались бы на старом (удалённом) inode. `stop=True` сначала останавливает
    сервер, `force=True` разрешает откат к копии с меньшим числом задач и подмену
    файла, даже если сервер успел подняться снова.
    """
    db = db_path_of(db_path)
    src_path = util.expanduser(backup_path)
    if not src_path.is_file():
        raise errors.ListikError(
            f"файла копии нет: {src_path}", code=errors.NOT_FOUND,
            hint="копии лежат рядом с базой: ls <каталог базы>/listik.db.bak-*")

    report = {"server": None, "warning": None}
    running = running_server(db)
    if running and not stop:
        pid = f" (pid {running['pid']})" if running.get("pid") else ""
        raise errors.ListikError(
            f"сервер Listik работает{pid} — восстановление подменяет файл базы, "
            "а его соединения остались бы на удалённом inode",
            code=errors.CONFLICT,
            hint="останови сервер: listik stop, потом повтори; "
                 "или сразу: listik restore <копия> --stop")
    if running:
        report["server"] = {**running, **stop_server(running)}

    # 1. Проверяем копию до любых изменений: не Listik-база и не битая — отказ.
    src = _open_source(src_path)
    try:
        if not _is_listik_db(src):
            raise errors.ListikError(
                f"это не база Listik: {src_path}",
                code=errors.BAD_ARGUMENT,
                hint="укажи файл, созданный `listik backup` (в нём есть таблица tasks)")
        integrity = _integrity(src)
        if integrity != "ok":
            raise errors.ListikError(
                f"копия битая: integrity_check вернул «{integrity}»",
                code=errors.BAD_ARGUMENT,
                hint="возьми другую копию: ls <каталог базы>/listik.db.bak-*")
        counts_new = db_mod.counts(src)

        # 2. Откат назад по числу задач — только осознанно (--force).
        if db.exists() and not force:
            current = _current_counts(db)
            if current and counts_new.get("tasks", 0) < current.get("tasks", 0):
                raise errors.ListikError(
                    f"в копии {counts_new.get('tasks', 0)} задач, а в текущей базе "
                    f"{current.get('tasks', 0)} — это откат назад",
                    code=errors.CONFLICT,
                    hint="если откат действительно нужен, повтори с --force")

        # 3. Предохранительная копия текущей базы: restore не должен быть необратимым.
        safety = None
        if db.exists():
            safety_path = _unique_path(
                db.with_name(f"{db.name}{BACKUP_MARK}pre-restore-{_stamp()}"))
            try:
                backup(db, safety_path, overwrite=True)
                safety = str(safety_path)
            except errors.ListikError as exc:
                report["warning"] = (f"предохранительную копию снять не удалось "
                                     f"({exc.message}); текущая база будет перезаписана")

        # 4. Собираем замену рядом с базой и проверяем её до подмены.
        tmp = db.with_name(f"{db.name}.restore-{os.getpid()}")
        db.parent.mkdir(parents=True, exist_ok=True)
        tmp.unlink(missing_ok=True)  # хвост прошлого неудачного restore
        try:
            try:
                _copy_db(src, tmp)
                check = _open_readonly(tmp)
                try:
                    tmp_integrity = _integrity(check)
                finally:
                    check.close()
            except sqlite3.Error as exc:
                raise errors.ListikError(
                    f"восстановление не удалось: {type(exc).__name__}: {exc}",
                    code=errors.SERVER_ERROR,
                    hint="текущая база не тронута; подробности в listik.log") from exc
            if tmp_integrity != "ok":
                raise errors.ListikError(
                    f"восстановленная база не прошла integrity_check: «{tmp_integrity}»",
                    code=errors.SERVER_ERROR,
                    hint="текущая база не тронута; проверь диск и исходную копию")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    finally:
        src.close()

    # 5. Перед самой подменой убеждаемся, что сервер не поднялся снова: он бы
    #    остался на удалённом inode. `--force` — осознанное «я знаю, что делаю».
    again = running_server(db)
    if again and not force:
        pid = f" (pid {again['pid']})" if again.get("pid") else ""
        tmp.unlink(missing_ok=True)
        raise errors.ListikError(
            f"сервер Listik снова работает{pid} — восстановление отменено",
            code=errors.CONFLICT,
            hint="останови сервер: listik stop; "
                 "или повтори с --force, если сервер точно не должен работать")

    # 6. Подмена: сначала убираем -wal/-shm прежней базы (после подмены они
    #    превратились бы в «malformed»), затем атомарный os.replace.
    removed = []
    for side in sidecar_paths(db):
        if side.exists():
            try:
                side.unlink()
                removed.append(str(side))
            except OSError as exc:
                tmp.unlink(missing_ok=True)
                raise errors.ListikError(
                    f"не удалось убрать старый журнал {side.name}: {exc}",
                    code=errors.SERVER_ERROR,
                    hint="останови сервер и проверь права на каталог базы") from exc
    try:
        os.replace(tmp, db)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise errors.ListikError(
            f"не удалось подменить файл базы: {exc}", code=errors.SERVER_ERROR,
            hint="текущая база не тронута; проверь права на каталог базы") from exc
    _fsync_dir(db.parent)

    # 7. Проверяем то, что реально лежит на месте базы.
    final = _open_readonly(db)
    try:
        final_integrity = _integrity(final)
        counts = db_mod.counts(final)
    finally:
        final.close()

    return {
        "ok": True,
        "db": str(db),
        "backup": str(src_path),
        "at": store.now_iso(),
        "integrity": final_integrity,
        "counts": counts,
        "removed_sidecars": removed,
        "safety_copy": safety,
        "warning": report["warning"],
        "server": report["server"],
    }


def _current_counts(db: Path) -> dict | None:
    """Сколько задач в текущей базе; None — базу не прочитать (тогда не мешаем)."""
    try:
        conn = _open_source(db)
    except sqlite3.Error:
        return None
    try:
        return db_mod.counts(conn)
    except sqlite3.Error:
        return None
    finally:
        conn.close()

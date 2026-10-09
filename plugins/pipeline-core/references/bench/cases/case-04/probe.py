"""alembic upgrade онлайн на существующей базе ревизии 0011: старое событие `stage` с заметкой
по умолчанию получает `transition`."""
import contextlib
import io
import os
import sqlite3
import sys
from pathlib import Path

try:
    from alembic.config import main as alembic_main

    db = Path(os.environ["LISTIK_HOME"]) / "probe.db"
    os.environ["LISTIK_DB"] = str(db)
    ini = str(Path.cwd() / "alembic.ini")
    with contextlib.redirect_stdout(io.StringIO()):
        alembic_main(argv=["-c", ini, "-q", "upgrade", "0011_task_orchestrator"])
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO events(task_id, ts, kind, from_value, to_value, note) "
                     "VALUES('t1', '2026-09-01T00:00:00Z', 'stage', 's1-spec', 's2-review', "
                     "'этап -> s2-review (handoff)')")
        conn.commit()
        conn.close()
        alembic_main(argv=["-c", ini, "-q", "upgrade", "head"])
    conn = sqlite3.connect(db)
    got = conn.execute("SELECT transition FROM events WHERE task_id = 't1'").fetchone()[0]
    conn.close()
except BaseException as exc:  # noqa: BLE001 — контракт пробы: любой сбой (и SystemExit alembic) — код 2
    print(f"ошибка пробы: {exc!r}")
    sys.exit(2)
ok = got == "handoff"
print(f"alembic upgrade 0011 -> head: transition старого события — ждали 'handoff', "
      f"получили {got!r} — {'верно' if ok else 'не досыпано'}")
sys.exit(0 if ok else 1)

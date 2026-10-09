"""ready_tasks(limit=1): ровно одна свободная задача с высшим приоритетом."""
import os
import sys
from pathlib import Path

try:
    from listik import db as db_mod, deps, store

    conn = db_mod.init(Path(os.environ["LISTIK_HOME"]) / "probe.db")
    blocker = store.create_task(conn, title="X", project="other")["id"]
    for i in range(3):
        waiting = store.create_task(conn, title=f"B{i}", project="demo", priority=0)["id"]
        store.add_dep(conn, waiting, blocker, "blocks", created_by="probe")
    free = [store.create_task(conn, title=f"F{p}", project="demo", priority=p)["id"]
            for p in (1, 2, 3)]
    got = [t["id"] for t in deps.ready_tasks(conn, project="demo", limit=1)]
except Exception as exc:  # noqa: BLE001 — контракт пробы: любой сбой — код 2
    print(f"ошибка пробы: {exc!r}")
    sys.exit(2)
ok = got == free[:1]
print(f"ready_tasks(limit=1): ждали [{free[0]}], получили {got} — {'верно' if ok else 'лишние задачи'}")
sys.exit(0 if ok else 1)

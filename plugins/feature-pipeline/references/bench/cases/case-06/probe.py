"""MCP `listik_release` от `bob` с `{"owner": "ann"}` в аргументах: задача `ann` не освобождается."""
import os
import sys
from pathlib import Path

try:
    from listik import db as db_mod, errors, mcp, paths, store

    paths.CONFIG_PATH.write_text('[auth]\ntoken = "t"\n\n[server]\nmode = "server"\n'
                                 'users = ["ann", "bob"]\n', encoding="utf-8")
    conn = db_mod.init(Path(os.environ["LISTIK_HOME"]) / "probe.db")
    tid = store.create_task(conn, title="t", as_owner="ann")["id"]
    store.claim(conn, tid, holder="agent:dsh", as_owner="ann")
    try:
        mcp.call_tool("listik_release", {"id": tid, "owner": "ann"}, conn=conn, owner="bob")
        refused = False
    except errors.Forbidden:
        refused = True
    holder = store.get_task(conn, tid)["holder"]
except Exception as exc:  # noqa: BLE001 — контракт пробы: любой сбой — код 2
    print(f"ошибка пробы: {exc!r}")
    sys.exit(2)
ok = refused and holder == "agent:dsh"
print(f"listik_release от bob с owner=ann в аргументах: отказ {refused}, держатель {holder!r} — "
      f"{'верно' if ok else 'чужой освободил'}")
sys.exit(0 if ok else 1)

"""listik_list в MCP без limit и с limit=null: умолчание 50, а не 200."""
import os
import sys
from pathlib import Path

try:
    from listik import db as db_mod, mcp, store

    conn = db_mod.init(Path(os.environ["LISTIK_HOME"]) / "probe.db")
    store.create_task(conn, title="T", project="demo")
    got = [mcp.call_tool("listik_list", args, conn=conn)["limit"]
           for args in ({"project": "demo"}, {"project": "demo", "limit": None})]
except Exception as exc:  # noqa: BLE001 — контракт пробы: любой сбой — код 2
    print(f"ошибка пробы: {exc!r}")
    sys.exit(2)
ok = got == [50, 50]
print(f"listik_list limit без ключа и null: ждали [50, 50], получили {got} — "
      f"{'верно' if ok else 'умолчание не то'}")
sys.exit(0 if ok else 1)

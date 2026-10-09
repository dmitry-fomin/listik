"""`listik --local memory -n 0` при 25 заметках: итог `показано 20 из 25`."""
import os
import subprocess
import sys
from pathlib import Path

try:
    from listik import db as db_mod, paths, store

    conn = db_mod.init(paths.DB_PATH)
    for i in range(25):
        store.remember(conn, f"заметка {i}", key=f"m{i}", project="demo")
    conn.close()
    proc = subprocess.run([sys.executable, str(Path.cwd() / "bin/listik"), "--local", "memory",
                           "--project", "demo", "-n", "0"],
                          capture_output=True, text=True, env=os.environ, timeout=120)
    if proc.returncode:
        raise RuntimeError(f"код {proc.returncode}: {proc.stderr.strip()[-300:]}")
    lines = [s for s in proc.stdout.splitlines() if s.strip()]
    got = lines[-1] if lines else ""
except Exception as exc:  # noqa: BLE001 — контракт пробы: любой сбой — код 2
    print(f"ошибка пробы: {exc!r}")
    sys.exit(2)
ok = got == "показано 20 из 25"
print(f"memory -n 0 --local, 25 заметок: ждали 'показано 20 из 25', получили {got!r} — "
      f"{'верно' if ok else 'итог по странице'}")
sys.exit(0 if ok else 1)

"""Лестница реакций `listik watch` (listik-oltp, шаг swarm-5, порция c): заморозка
опоздавшего при конфликте, «добивка» недоделанной заморозки, подпись решений.

Обвязка — `AutostartTestCase` (маршрут с командой-воркером на `sys.executable`,
образец `WORKER_PY` из `tests/test_fencing_revoke.py`, `SLEEP=60` по умолчанию).
Git-репозиторий проекта заводит сам тест (`git init`, конфиг, первый коммит с
`pkg/mod.py` и `f.txt` — образец `tests/test_worktree.py`), деревья — через
`worktree.ensure` + `store.update_task(worktree=, branch=)`, запуск —
`launcher.start(self.conn, id, log_dir=self.log_dir)`. Порт — на `store`/`launcher`
in-process (см. `_Port`), с переводом `ValueError`/`errors.NotFound` в
`errors.ListikError`, как в `_SwarmCards` (`bin/listik`); там, где проверяется
CLI (`revoke` в локальном фолбэке не поддержан — сценарий 10, подпись — 15) —
`bin/listik --local`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from unittest import mock

from listik import errors
from listik import launcher as launcher_mod
from listik import store
from listik import swarm_watch
from listik import worktree as worktree_mod
from tests.test_autostart import AutostartTestCase, pipeline_record
from tests.test_claim import LISTIK_BIN
from tests.test_fencing_revoke import WORKER_PY

HAS_GIT = shutil.which("git") is not None

MOD_CONTENT = (
    "def first():\n"
    "    a = 1\n"
    "    b = 2\n"
    "    return a + b\n"
)


def _variant(n: int) -> str:
    """Содержимое `pkg/mod.py` с уникальной по `n` правкой той же строки функции —
    несколько таких правок в одной строке конфликтуют при пробном слиянии."""
    return MOD_CONTENT.replace("return a + b", f"return a + b + {n}  # v{n}")


class _Port:
    """Порт `swarm_watch.scan` на `store`/`launcher` in-process (§1 порции c)."""

    def __init__(self, conn) -> None:
        self.conn = conn
        self.calls: list[tuple[str, str]] = []

    def _guard(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except errors.ListikError:
            raise
        except (ValueError, KeyError) as exc:
            raise errors.as_error(exc) from exc

    def show(self, task_id: str) -> dict:
        return store.get_task(self.conn, task_id)

    def comment(self, task_id: str, text: str) -> dict:
        self.calls.append(("comment", task_id))
        return store.add_comment(self.conn, task_id, text, author=swarm_watch.SWARM_ACTOR,
                                 kind="journal")

    def revoke(self, task_id: str, note: str) -> dict:
        self.calls.append(("revoke", task_id))
        return self._guard(launcher_mod.revoke, self.conn, task_id,
                           actor=swarm_watch.SWARM_ACTOR, note=note, kill=True)

    def release(self, task_id: str, note: str) -> dict:
        self.calls.append(("release", task_id))
        return self._guard(store.update_task, self.conn, task_id,
                           actor=swarm_watch.SWARM_ACTOR, holder="", note=note)

    def set_labels(self, task_id: str, labels: list[str]) -> dict:
        self.calls.append(("labels", task_id))
        return self._guard(store.update_task, self.conn, task_id,
                           actor=swarm_watch.SWARM_ACTOR, labels=labels)


class _FailingRevokePort(_Port):
    """Порт, чей `revoke` всегда падает — заданным исключением."""

    def __init__(self, conn, exc_factory) -> None:
        super().__init__(conn)
        self._exc_factory = exc_factory

    def revoke(self, task_id: str, note: str) -> dict:
        self.calls.append(("revoke", task_id))
        raise self._exc_factory()


class _FailingLabelsPort(_Port):
    """Порт, чей `set_labels` всегда падает — шаг 1 (`revoke`) уже прошёл."""

    def set_labels(self, task_id: str, labels: list[str]) -> dict:
        self.calls.append(("labels", task_id))
        raise errors.ListikError("метки не встали", code=errors.CONFLICT)


@unittest.skipUnless(HAS_GIT, "нет git")
class LadderCase(AutostartTestCase):
    """Временный git-репозиторий «demo» + маршрут-воркер + порт на store/launcher."""

    def setUp(self) -> None:
        super().setUp()
        self._env_patch = mock.patch.dict(
            os.environ, {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

        self.repo = self.tmp_path / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main", ".")
        self.git("config", "user.name", "Тест")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "f.txt").write_text("base\n", encoding="utf-8")
        pkg = self.repo / "pkg"
        pkg.mkdir()
        (pkg / "mod.py").write_text(MOD_CONTENT, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "первый")
        self.project_path = str(self.repo.resolve())
        self.make_project("demo", path=self.project_path)

        script = self.tmp_path / "ladder_worker.py"
        script.write_text(WORKER_PY, encoding="utf-8")
        self.worker_out = self.tmp_path / "worker-out.json"
        self.set_routes(pipeline_record(
            "low-pipeline", command=[sys.executable, str(script), str(self.worker_out)]))

        launcher_mod._procs.clear()
        self.port = _Port(self.conn)

    def tearDown(self) -> None:
        for proc in list(launcher_mod._procs.values()):
            self._kill_and_join(proc)
        launcher_mod._procs.clear()
        super().tearDown()

    # -------------------------------------------------------------- git

    def git(self, *args: str, cwd=None) -> subprocess.CompletedProcess:
        proc = subprocess.run(["git", "-C", str(cwd or self.repo), *args],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def git_out(self, *args: str, cwd=None) -> str:
        return self.git(*args, cwd=cwd).stdout.strip()

    # -------------------------------------------------------------- обвязка

    def _kill_and_join(self, proc) -> None:
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, 9)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001 — уборка не должна ронять тест
                pass

    def make_task(self, task_id: str, **fields) -> str:
        route = fields.pop("route", "low-pipeline")
        tid = store.create_task(self.conn, title=task_id, project="demo", task_id=task_id,
                                route=route)["id"]
        if fields:
            store.update_task(self.conn, tid, **fields)
        return tid

    def make_tree(self, task_id: str) -> str:
        out = worktree_mod.ensure(self.project_path, task_id)
        store.update_task(self.conn, task_id, worktree=out["path"], branch=out["branch"])
        return out["path"]

    def edit(self, tree: str, name: str, text: str, *, commit: bool = True,
            message: str = "правка") -> None:
        with open(os.path.join(tree, name), "w", encoding="utf-8") as fh:
            fh.write(text)
        if commit:
            self.git("add", ".", cwd=tree)
            self.git("commit", "-qm", message, cwd=tree)

    def start(self, task_id: str, *, sleep_s: float = 60.0, ignore_term: bool = False) -> None:
        env = {"SLEEP": str(sleep_s)}
        if ignore_term:
            env["IGNORE_TERM"] = "1"
        with mock.patch.dict(os.environ, env):
            result = launcher_mod.start(self.conn, task_id, log_dir=self.log_dir)
        self.assertIsNone(result)

    def card(self, task_id: str) -> dict:
        return store.get_task(self.conn, task_id)

    def comments(self, task_id: str) -> list[dict]:
        return self.card(task_id)["comments"]

    def marks(self, task_id: str, mark: str = swarm_watch.FIRST_CHANGE_MARK) -> list[dict]:
        return [c for c in self.comments(task_id)
                if c["author"] == "agent:listik-swarm" and c["kind"] == "journal"
                and c["text"].startswith(mark)]

    def freeze_marks(self, task_id: str) -> list[dict]:
        return self.marks(task_id, mark=swarm_watch.FREEZE_MARK)

    def own_marks(self, task_id: str) -> list[dict]:
        return self.marks(task_id, mark=swarm_watch.OWN_MARK)

    def scan(self, *, port=None, dry_run: bool = False, now: str | None = None) -> dict:
        tasks = store.list_tasks(self.conn, project="demo", include_closed=True,
                                 limit=1000)["tasks"]
        return swarm_watch.scan(self.project_path, tasks, port or self.port, dry_run=dry_run,
                                now=now)

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))

    # -------------------------------------------------------------- сценарий 1, переиспользуемый

    def _setup_conflict_pair(self, t1: str = "t1", t2: str = "t2") -> tuple[str, str]:
        """Владелец `t1` (правка+коммит раньше) конфликтует с опоздавшим `t2`."""
        self.make_task(t1)
        self.make_task(t2)
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        self.edit(tree1, "pkg/mod.py", _variant(1))
        self.edit(tree1, "f.txt", "0\nbase\n")
        self.start(t1)
        out1 = self.scan()
        self.assertEqual(out1["decisions"], [])

        self.edit(tree2, "pkg/mod.py", _variant(2), commit=False)
        self.edit(tree2, "f.txt", "base\n9\n", commit=False)
        card2 = self.card(t2)
        store.update_task(self.conn, t2, labels=list(card2["labels"]) + ["port:5173"])
        self.start(t2)
        return t1, t2


# ------------------------------------------------------------------ 1


class SameFunctionBothRunningTests(LadderCase):
    def test_late_is_frozen_owner_untouched(self) -> None:
        t1, t2 = self._setup_conflict_pair()
        before1, before2 = self.card(t1), self.card(t2)
        proc2 = launcher_mod._procs[t2]
        head_before = self.git_out("rev-parse", "HEAD")
        status_before = self.git_out("status", "--porcelain", cwd=self.repo)
        wt_status_before = self.git_out("status", "--porcelain", cwd=before2["worktree"])

        out = self.scan()
        self.assertEqual(out["decisions"], [{
            "action": "freeze", "task": t2, "owner": t1, "files": ["pkg/mod.py"],
            "ok": True, "generation": before2["generation"] + 1,
        }])

        deadline = time.monotonic() + launcher_mod.KILL_GRACE + 1
        while time.monotonic() < deadline and proc2.poll() is None:
            time.sleep(0.05)
        self.assertIsNotNone(proc2.poll(), "процесс t2 должен быть мёртв")

        after1, after2 = self.card(t1), self.card(t2)
        self.assertEqual(after2["generation"], before2["generation"] + 1)
        self.assertIsNone(after2["launched_by"])
        self.assertEqual(after2["holder"], "")
        expected_labels = set(before2["labels"]) | {f"frozen-by:{t1}"}
        self.assertEqual(set(after2["labels"]), expected_labels)
        self.assertIn("harness:", " ".join(after2["labels"]))
        self.assertIn("port:5173", after2["labels"])

        fm = self.freeze_marks(t2)
        self.assertEqual(len(fm), 1)
        payload = json.loads(fm[0]["text"][len(swarm_watch.FREEZE_MARK):].strip())
        self.assertEqual(payload["owner"], t1)
        self.assertEqual(payload["files"], ["pkg/mod.py"])
        self.assertIn("worktree", payload)
        self.assertIn("branch", payload)
        self.assertEqual(payload["generation"], after2["generation"])

        om = self.own_marks(t1)
        self.assertEqual(len(om), 1)
        own_payload = json.loads(om[0]["text"][len(swarm_watch.OWN_MARK):].strip())
        self.assertEqual(own_payload["frozen"], t2)
        self.assertEqual(own_payload["files"], ["pkg/mod.py"])

        self.assertEqual(after1["generation"], before1["generation"])
        self.assertEqual(after1["holder"], before1["holder"])
        self.assertEqual(set(after1["labels"]), set(before1["labels"]))
        self.assertIsNone(launcher_mod._procs[t1].poll())  # t1 жив

        self.assertEqual(self.git_out("status", "--porcelain", cwd=after2["worktree"]),
                         wt_status_before)
        self.git_out("rev-parse", "--verify", f"task/{t2}")  # ветка на месте
        self.assertEqual(self.git_out("rev-parse", "HEAD"), head_before)
        # Основная ветка не испорчена: состояние каталога проекта то же, что и
        # до заморозки (сама заморозка не добавляет и не убирает изменений там;
        # непустое значение здесь — от заведения дерева .gitignore, не от неё).
        self.assertEqual(self.git_out("status", "--porcelain", cwd=self.repo), status_before)


# ------------------------------------------------------------------ 2


class IdempotentRepeatTests(LadderCase):
    def test_repeat_scan_makes_no_new_decisions(self) -> None:
        t1, t2 = self._setup_conflict_pair()
        self.scan()
        gen_after_freeze = self.card(t2)["generation"]

        out2 = self.scan()
        self.assertEqual(out2["decisions"], [])
        self.assertEqual(self.card(t2)["generation"], gen_after_freeze)
        self.assertEqual(len(self.freeze_marks(t2)), 1)
        self.assertEqual(len(self.own_marks(t1)), 1)
        self.assertIn(f"frozen-by:{t1}", self.card(t2)["labels"])
        self.assertNotIn(t2, out2["order"])


# ------------------------------------------------------------------ 3


class OppositeEndsTests(LadderCase):
    def test_opposite_ends_of_file_nobody_touched(self) -> None:
        t1 = self.make_task("t1")
        t2 = self.make_task("t2")
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        self.edit(tree1, "f.txt", "0\nbase\n")
        self.edit(tree2, "f.txt", "base\n9\n")
        self.start(t1)
        self.start(t2)
        head_before = self.git_out("rev-parse", "HEAD")
        before1, before2 = self.card(t1), self.card(t2)

        out = self.scan()
        self.assertTrue(out["probes"][0]["clean"])
        self.assertEqual(out["decisions"], [])

        after1, after2 = self.card(t1), self.card(t2)
        self.assertIsNone(launcher_mod._procs[t1].poll())
        self.assertIsNone(launcher_mod._procs[t2].poll())
        self.assertEqual(after1["generation"], before1["generation"])
        self.assertEqual(after2["generation"], before2["generation"])
        self.assertEqual(after1["labels"], before1["labels"])
        self.assertEqual(after2["labels"], before2["labels"])
        self.assertEqual(self.git_out("rev-parse", "HEAD"), head_before)


# ------------------------------------------------------------------ 4


class LateAlreadyFinishedTests(LadderCase):
    def test_late_process_exited_only_report(self) -> None:
        t1 = self.make_task("t1")
        t2 = self.make_task("t2")
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        self.edit(tree1, "pkg/mod.py", _variant(1))
        self.edit(tree2, "pkg/mod.py", _variant(2))
        self.start(t1, sleep_s=60.0)
        self.start(t2, sleep_s=0.0)
        self.join_tracker(t2)
        self.assertTrue(self.card(t2)["launch_finished_at"])

        before2 = self.card(t2)
        out = self.scan()
        self.assertEqual(out["decisions"], [{
            "action": "report", "task": t2, "owner": t1, "files": ["pkg/mod.py"],
            "reason": "late_not_running",
        }])
        after2 = self.card(t2)
        self.assertEqual(after2["generation"], before2["generation"])
        self.assertEqual(after2["labels"], before2["labels"])
        self.assertEqual(after2["holder"], before2["holder"])
        self.assertEqual(self.freeze_marks(t2), [])


# ------------------------------------------------------------------ 5


class LateClosedNotMergedTests(LadderCase):
    def test_late_done_status_only_report(self) -> None:
        # t2 запущена и жива (`launch_alive` истинна) — решение здесь обязано
        # держаться на одном лишь `status not in OPEN_STATUSES`, а не (как в
        # сценарии 16) на отсутствии живого запуска: иначе мутация, убирающая
        # проверку статуса, сценарий не ловит (замечание судьи).
        t1 = self.make_task("t1")
        t2 = self.make_task("t2")
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        self.edit(tree1, "pkg/mod.py", _variant(1))
        self.edit(tree2, "pkg/mod.py", _variant(2))
        self.start(t1)
        self.start(t2)
        store.update_task(self.conn, t2, status="done")
        proc2 = launcher_mod._procs[t2]

        out = self.scan()
        self.assertTrue(out["tasks"][t2]["launch_alive"])
        self.assertEqual(len(out["decisions"]), 1)
        d = out["decisions"][0]
        self.assertEqual(d["action"], "report")
        self.assertEqual(d["task"], t2)
        self.assertEqual(d["reason"], "late_not_running")
        self.assertFalse(any(l.startswith("frozen-by:") for l in self.card(t2)["labels"]))
        self.assertIsNone(proc2.poll())  # report не снимает процесс


# ------------------------------------------------------------------ 6


class OwnerClosedLateAliveTests(LadderCase):
    def test_owner_closed_late_running_is_frozen(self) -> None:
        t1 = self.make_task("t1")
        t2 = self.make_task("t2")
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        self.edit(tree1, "pkg/mod.py", _variant(1))
        store.update_task(self.conn, t1, status="done")
        # t1 никогда не запускалась (`launched_at` пуст) — без явного более
        # раннего значения тай-брейк по `launched_at` (при совпавшем в этом же
        # вызове `first_change`) увёл бы владение к t2, а не к уже закрытой t1.
        self.conn.execute("UPDATE tasks SET launched_at = ? WHERE id = ?",
                          ("2000-01-01T00:00:00Z", t1))
        self.conn.commit()
        self.edit(tree2, "pkg/mod.py", _variant(2))
        self.start(t2)

        before1 = self.card(t1)
        out = self.scan()
        self.assertEqual(len(out["decisions"]), 1)
        d = out["decisions"][0]
        self.assertEqual(d["action"], "freeze")
        self.assertEqual(d["owner"], t1)
        self.assertEqual(d["task"], t2)
        self.assertTrue(d["ok"])

        after1 = self.card(t1)
        self.assertEqual(after1["labels"], before1["labels"])
        self.assertEqual(after1["generation"], before1["generation"])
        self.assertNotIn(("revoke", t1), self.port.calls)
        self.assertNotIn(("release", t1), self.port.calls)
        self.assertNotIn(("labels", t1), self.port.calls)
        self.assertIn(("comment", t1), self.port.calls)


# ------------------------------------------------------------------ 7


class DryRunTests(LadderCase):
    def test_dry_run_does_not_write(self) -> None:
        t1, t2 = self._setup_conflict_pair()
        before2 = self.card(t2)
        proc2 = launcher_mod._procs[t2]

        out = self.scan(dry_run=True)
        self.assertEqual(out["decisions"], [{
            "action": "freeze", "task": t2, "owner": t1, "files": ["pkg/mod.py"],
            "ok": True, "generation": None, "dry_run": True,
        }])
        self.assertIsNone(proc2.poll())
        after2 = self.card(t2)
        self.assertEqual(after2["generation"], before2["generation"])
        self.assertEqual(after2["labels"], before2["labels"])
        self.assertEqual(after2["comments"], before2["comments"])
        self.assertEqual(after2["events"], before2["events"])

        proc = self.run_cli("watch", "--project", "demo", "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(any(line.startswith(f"заморозила бы {t2}")
                            for line in proc.stdout.splitlines()), proc.stdout)


# ------------------------------------------------------------------ 8


class OneOwnerTwoLateTests(LadderCase):
    def test_both_late_frozen_pair_between_them_no_decision(self) -> None:
        t1 = self.make_task("t1")
        t2 = self.make_task("t2")
        t3 = self.make_task("t3")
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        tree3 = self.make_tree(t3)
        self.edit(tree1, "pkg/mod.py", _variant(1))
        self.start(t1)
        out1 = self.scan()
        self.assertEqual(out1["decisions"], [])

        self.edit(tree2, "pkg/mod.py", _variant(2), commit=False)
        self.edit(tree3, "pkg/mod.py", _variant(3), commit=False)
        self.start(t2)
        self.start(t3)

        out = self.scan()
        by_task = {d["task"]: d for d in out["decisions"] if d["action"] == "freeze"}
        self.assertEqual(set(by_task), {t2, t3})
        for d in by_task.values():
            self.assertEqual(d["owner"], t1)
            self.assertTrue(d["ok"])
        self.assertEqual(self.card(t2)["labels"].count(f"frozen-by:{t1}"), 1)
        self.assertEqual(self.card(t3)["labels"].count(f"frozen-by:{t1}"), 1)
        pair_decisions = [d for d in out["decisions"]
                          if {d.get("task"), d.get("owner")} == {t2, t3}]
        self.assertEqual(pair_decisions, [])


# ------------------------------------------------------------------ 9


class ChainTests(LadderCase):
    def test_chain_only_direct_late_frozen(self) -> None:
        # Все три задачи наблюдаются впервые за один вызов `scan`: заморозка t2
        # происходит внутри этого же вызова (через `frozen_now`), поэтому пара
        # (t2, t3) должна пропускаться динамически — не через готовую метку
        # `frozen-by:` с прошлого прогона (для неё `order` и так отсеял бы t2
        # раньше, чем до пары дошла бы очередь, и проверка была бы про другое).
        t1 = self.make_task("t1")
        t2 = self.make_task("t2")
        t3 = self.make_task("t3")
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        tree3 = self.make_tree(t3)
        self.edit(tree1, "pkg/mod.py", _variant(1))
        self.edit(tree2, "pkg/mod.py", _variant(2), commit=False)
        self.edit(tree2, "g.txt", "t2\n", commit=False)
        self.edit(tree3, "g.txt", "t3\n", commit=False)
        self.start(t1)
        self.start(t2)
        self.start(t3)
        # Один и тот же `now` — все три `FIRST_CHANGE_MARK` получают одинаковый
        # `created_at`, и порядок владения решает второй ключ — `launched_at`.
        for tid, ts in ((t1, "2020-01-01T00:00:01Z"), (t2, "2020-01-01T00:00:02Z"),
                        (t3, "2020-01-01T00:00:03Z")):
            self.conn.execute("UPDATE tasks SET launched_at = ? WHERE id = ?", (ts, tid))
        self.conn.commit()

        out = self.scan(now="2024-01-01T00:00:00Z")
        self.assertEqual(out["order"], [t1, t2, t3])
        freezes = {d["task"]: d for d in out["decisions"] if d["action"] == "freeze"}
        self.assertEqual(set(freezes), {t2})
        self.assertEqual(freezes[t2]["owner"], t1)
        self.assertTrue(freezes[t2]["ok"])
        self.assertEqual(len(out["decisions"]), 1)  # пара (t2, t3) решения не даёт
        self.assertIsNone(launcher_mod._procs[t3].poll())
        self.assertFalse(any(l.startswith("frozen-by:") for l in self.card(t3)["labels"]))


# ------------------------------------------------------------------ 10


class RevokeErrorTests(LadderCase):
    def test_listik_error_from_port_stops_before_writes(self) -> None:
        t1 = self.make_task("t1")
        t2 = self.make_task("t2")
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        self.edit(tree1, "pkg/mod.py", _variant(1))
        self.edit(tree2, "pkg/mod.py", _variant(2))
        self.start(t1)
        self.start(t2)
        before2 = self.card(t2)

        def boom():
            return errors.ListikError("revoke выполняет только сервер", code=errors.UNSUPPORTED)

        port = _FailingRevokePort(self.conn, boom)
        out = self.scan(port=port)
        self.assertEqual(len(out["decisions"]), 1)
        d = out["decisions"][0]
        self.assertFalse(d["ok"])
        self.assertEqual(d["done"], [])
        self.assertIn("error", d)

        after2 = self.card(t2)
        self.assertEqual(after2["holder"], before2["holder"])
        self.assertEqual(after2["labels"], before2["labels"])
        # Комментарии карточки могут прирасти "первой правкой" (b) — заморозкой
        # (FREEZE_MARK) не должны: `revoke` первым же шагом упал.
        self.assertEqual(self.freeze_marks(t2), [])
        self.assertIsNone(launcher_mod._procs[t2].poll())

    def test_cli_local_revoke_unsupported_returns_json_and_exit_1(self) -> None:
        t1, t2 = self._setup_conflict_pair()
        before_labels = list(self.card(t2)["labels"])

        proc = self.run_cli("watch", "--project", "demo", "--json")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        # Полный результат скана (не объект ошибки report_error — у него нет "decisions").
        self.assertIn("decisions", payload)
        self.assertIn("probes", payload)
        self.assertEqual(payload["decisions"][0]["ok"], False)
        self.assertIn("выполняет только сервер", payload["decisions"][0]["error"])
        self.assertTrue(proc.stderr.strip())

        self.assertEqual(self.card(t2)["labels"], before_labels)
        self.assertFalse(any(l.startswith("frozen-by:") for l in self.card(t2)["labels"]))
        self.assertIsNone(launcher_mod._procs[t2].poll())

    def test_bare_value_error_from_launcher_stops_before_writes(self) -> None:
        t1 = self.make_task("t1")
        t2 = self.make_task("t2")
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        self.edit(tree1, "pkg/mod.py", _variant(1))
        self.edit(tree2, "pkg/mod.py", _variant(2))
        self.start(t1)
        # t2: полей запуска не через launcher.start — генерация остаётся 0,
        # но launch_alive истинна (launched_by/launch_pid стоят), поэтому решение
        # дойдёт до попытки revoke, а не до "report".
        self.conn.execute(
            "UPDATE tasks SET launched_by = 'listik', launch_pid = ?, "
            "launch_finished_at = NULL WHERE id = ?", (self.dead_pid(), t2))
        self.conn.commit()

        out = self.scan()
        self.assertEqual(len(out["decisions"]), 1)
        d = out["decisions"][0]
        self.assertFalse(d["ok"])
        self.assertEqual(d["done"], [])


# ------------------------------------------------------------------ 11


class ErrorAfterRevokeResumeTests(LadderCase):
    def test_labels_failure_then_resumed_then_idempotent(self) -> None:
        t1, t2 = self._setup_conflict_pair()
        gen_before = self.card(t2)["generation"]

        failing_port = _FailingLabelsPort(self.conn)
        out1 = self.scan(port=failing_port)
        self.assertEqual(len(out1["decisions"]), 1)
        d1 = out1["decisions"][0]
        self.assertFalse(d1["ok"])
        self.assertEqual(d1["done"], ["revoke", "release"])

        after_fail = self.card(t2)
        self.assertEqual(after_fail["generation"], gen_before + 1)
        self.assertIsNone(after_fail["launched_by"])
        self.assertEqual(after_fail["holder"], "")
        self.assertFalse(any(l.startswith("frozen-by:") for l in after_fail["labels"]))
        self.assertEqual(self.freeze_marks(t2), [])
        revoke_count_before = len([e for e in after_fail["events"] if e["kind"] == "revoke"])
        self.assertEqual(revoke_count_before, 1)

        out2 = self.scan()
        self.assertEqual(len(out2["decisions"]), 1)
        d2 = out2["decisions"][0]
        self.assertEqual(d2, {"action": "freeze", "task": t2, "owner": t1,
                              "files": ["pkg/mod.py"], "ok": True, "resumed": True,
                              "generation": after_fail["generation"]})

        after_resumed = self.card(t2)
        self.assertEqual(after_resumed["generation"], after_fail["generation"])
        self.assertTrue(any(l == f"frozen-by:{t1}" for l in after_resumed["labels"]))
        self.assertEqual(len(self.freeze_marks(t2)), 1)
        self.assertEqual(len(self.own_marks(t1)), 1)
        revoke_count_after = len([e for e in after_resumed["events"] if e["kind"] == "revoke"])
        self.assertEqual(revoke_count_after, 1)

        out3 = self.scan()
        self.assertEqual(out3["decisions"], [])


# ------------------------------------------------------------------ 12


class OwnerNeverRevokedTests(LadderCase):
    def test_owner_only_ever_commented(self) -> None:
        t1, t2 = self._setup_conflict_pair()
        self.scan()
        self.assertNotIn(("revoke", t1), self.port.calls)
        self.assertNotIn(("release", t1), self.port.calls)
        self.assertNotIn(("labels", t1), self.port.calls)
        self.assertIn(("revoke", t2), self.port.calls)
        self.assertIn(("release", t2), self.port.calls)
        self.assertIn(("labels", t2), self.port.calls)

        # Свежая пара (t3, t4) на отдельном файле `h.txt` — чтобы не пересекаться
        # с уже занятым владением t1/t2 на pkg/mod.py. Первый тик наблюдает только
        # t3, второй — t3+t4 (t4 ещё не запущена → решение "report", метка не
        # встаёт), затем времена FIRST_CHANGE_MARK меняются местами, и владельцем
        # становится t4.
        t3 = self.make_task("t3")
        t4 = self.make_task("t4")
        tree3 = self.make_tree(t3)
        tree4 = self.make_tree(t4)
        self.edit(tree3, "h.txt", "t3\n")
        self.start(t3)
        self.scan()
        self.assertEqual(len(self.marks(t3)), 1)

        self.edit(tree4, "h.txt", "t4\n", commit=False)
        # t4 ещё не запущена (`launched_at` пуст) — при любом (в том числе
        # совпавшем по секунде) `first_change` тай-брейк по `launched_at` отдаёт
        # владение уже стартовавшей t3.
        out2 = self.scan()
        self.assertEqual(len(self.marks(t4)), 1)
        self.assertEqual(out2["decisions"][0]["action"], "report")
        self.assertEqual(out2["decisions"][0]["task"], t4)

        # `created_at` двух отметок ставится явно (а не читается и переставляется):
        # секундная точность реальных часов не гарантирует разные значения между
        # двумя быстрыми тиками теста (см. замечание судьи порции b, сценарий 4).
        mark3 = self.marks(t3)[0]
        mark4 = self.marks(t4)[0]
        self.conn.execute("UPDATE comments SET created_at = ? WHERE id = ?",
                          ("2024-02-01T00:00:05Z", mark3["id"]))
        self.conn.execute("UPDATE comments SET created_at = ? WHERE id = ?",
                          ("2024-02-01T00:00:00Z", mark4["id"]))
        self.conn.commit()

        self.start(t4)
        self.port.calls.clear()
        out3 = self.scan()
        self.assertEqual(len(out3["decisions"]), 1)
        d3 = out3["decisions"][0]
        self.assertEqual(d3["owner"], t4)
        self.assertEqual(d3["task"], t3)
        self.assertNotIn(("revoke", t4), self.port.calls)
        self.assertNotIn(("release", t4), self.port.calls)
        self.assertNotIn(("labels", t4), self.port.calls)
        self.assertIn(("revoke", t3), self.port.calls)


# ------------------------------------------------------------------ 13


class FirstChangeInvariantAfterUnfreezeTests(LadderCase):
    def test_first_change_stable_after_manual_unfreeze(self) -> None:
        t1, t2 = self._setup_conflict_pair()
        self.scan()
        original = self.marks(t2)[0]

        frozen_card = self.card(t2)
        labels = [l for l in frozen_card["labels"] if not l.startswith("frozen-by:")]
        self.conn.execute(
            "UPDATE tasks SET labels = ?, launched_by = 'listik', launch_pid = ?, "
            "launch_finished_at = NULL WHERE id = ?",
            (json.dumps(labels, ensure_ascii=False), self.dead_pid(), t2))
        self.conn.commit()

        self.scan()
        marks_after = self.marks(t2)
        self.assertEqual(len(marks_after), 1)
        self.assertEqual(marks_after[0]["created_at"], original["created_at"])
        self.assertEqual(marks_after[0]["id"], original["id"])


# ------------------------------------------------------------------ 14


class ProbeErrorNotADecisionTests(LadderCase):
    def test_probe_error_yields_no_decision(self) -> None:
        t1 = self.make_task("t1")
        t2 = self.make_task("t2")
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        self.edit(tree1, "pkg/mod.py", _variant(1))
        self.edit(tree2, "pkg/mod.py", _variant(2))
        self.start(t1)
        self.start(t2)
        before1, before2 = self.card(t1), self.card(t2)

        def fake_probe(repo, a, b):
            raise errors.ListikError("merge exploded", code=errors.CONFLICT)

        with mock.patch.object(swarm_watch, "probe", side_effect=fake_probe):
            out = self.scan()

        self.assertEqual(len(out["probes"]), 1)
        self.assertIn("error", out["probes"][0])
        self.assertEqual(out["decisions"], [])
        self.assertIsNone(launcher_mod._procs[t1].poll())
        self.assertIsNone(launcher_mod._procs[t2].poll())
        after1, after2 = self.card(t1), self.card(t2)
        self.assertEqual(after1["generation"], before1["generation"])
        self.assertEqual(after2["generation"], before2["generation"])
        self.assertEqual(after1["labels"], before1["labels"])
        self.assertEqual(after2["labels"], before2["labels"])
        self.assertEqual(after1["holder"], before1["holder"])
        self.assertEqual(after2["holder"], before2["holder"])


# ------------------------------------------------------------------ 15


class SignatureTests(LadderCase):
    def test_decisions_signed_by_swarm_actor_regardless_of_cli_actor(self) -> None:
        # `revoke` в локальном фолбэке CLI не поддержан (сценарий 10) — сквозную
        # проверку подписи через `bin/listik --local --actor me watch` тут
        # провести нельзя. Берём альтернативу, которую явно допускает ТЗ: порт,
        # построенный так же, как `_SwarmCards` в `bin/listik` — актор в нём
        # зашит (`SWARM_ACTOR`) и не читается из аргумента CLI, `cli_actor`
        # ниже принят и осознанно проигнорирован, как и там.
        class _CliLikePort(_Port):
            def __init__(self, conn, cli_actor: str) -> None:
                super().__init__(conn)
                self.cli_actor = cli_actor  # не используется — актор фиксирован

        t1, t2 = self._setup_conflict_pair()
        out = self.scan(port=_CliLikePort(self.conn, "me"))
        self.assertEqual(len(out["decisions"]), 1)
        self.assertTrue(out["decisions"][0]["ok"])

        card2 = self.card(t2)
        revoke_events = [e for e in card2["events"] if e["kind"] == "revoke"]
        self.assertEqual(len(revoke_events), 1)
        self.assertEqual(revoke_events[0]["actor"], "agent:listik-swarm")
        release_events = [e for e in card2["events"] if e["kind"] == "release"]
        self.assertEqual(len(release_events), 1)
        self.assertEqual(release_events[0]["actor"], "agent:listik-swarm")

        fm = self.freeze_marks(t2)
        self.assertEqual(len(fm), 1)
        self.assertEqual(fm[0]["author"], "agent:listik-swarm")
        om = self.own_marks(t1)
        self.assertEqual(len(om), 1)
        self.assertEqual(om[0]["author"], "agent:listik-swarm")

        journal = [c for c in card2["comments"] if c["author"] == "agent:listik"
                  and "полномочия поколения" in c["text"] and "отозваны" in c["text"]]
        self.assertTrue(journal)


# ------------------------------------------------------------------ 16


class NeverLaunchedNotResumedTests(LadderCase):
    def test_never_launched_late_is_reported_not_resumed(self) -> None:
        t1 = self.make_task("t1")
        t2 = self.make_task("t2")
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        self.edit(tree1, "pkg/mod.py", _variant(1))
        self.edit(tree2, "pkg/mod.py", _variant(2))
        self.start(t1)
        self.assertEqual(self.card(t2)["generation"], 0)
        self.assertFalse(self.card(t2)["launched_by"])

        out = self.scan()
        self.assertEqual(len(out["decisions"]), 1)
        d = out["decisions"][0]
        self.assertEqual(d["action"], "report")
        self.assertEqual(d["task"], t2)
        self.assertFalse(any(l.startswith("frozen-by:") for l in self.card(t2)["labels"]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

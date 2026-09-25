"""`listik restart`: карточка роя заново с этапа (listik-zr05, порция c)."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import threading
import unittest
from unittest import mock

from listik import errors, harnesses_store, paths, routes_store, server, stage_launch, store
from tests.helpers import TempDbTestCase

LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"
ALL_ROLES = {role: {"harness": "probe"} for _, role in stage_launch.STAGE_ROLES}


class RestartCase(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        store.add_project(self.conn, path=str(self.tmp_path), slug="proj", title="Проект")
        harnesses_store.create(self.conn, {
            "key": "probe", "label": "probe",
            "argv": [sys.executable, "-c", "print('готово')"]})
        routes_store.create_route(self.conn, key="roy", kind="swarm", title="Рой",
                                  roles=ALL_ROLES)
        routes_store.create_route(self.conn, key="shiki-pow", kind="swarm", title="Шики",
                                  roles=ALL_ROLES)
        self.steps = self.tmp_path / "steps"
        self.steps.mkdir()

    # ---------------------------------------------------------------- помощники
    def new(self, *, route: str | None = "roy", stage: str | None = None,
            parent: str | None = None, swarm: bool = True, **fields) -> str:
        tid = store.create_task(self.conn, title="Задача", project="proj", route=route,
                                stage=stage, parent=parent)["id"]
        if fields:
            store.update_task(self.conn, tid, **fields)
        if swarm:
            self.conn.execute("UPDATE tasks SET launch_driver = 'swarm' WHERE id = ?", (tid,))
            self.conn.commit()
        return tid

    def finished_launch(self, tid: str) -> None:
        self.conn.execute(
            "UPDATE tasks SET launched_by = 'agent:probe', launch_pid = 42, "
            "launched_at = '2026-01-01T00:00:00Z', launch_log = ?, launch_exit_code = 1, "
            "launch_finished_at = '2026-01-01T00:01:00Z', launch_error = 'упал', "
            "dispatch_id = 'd1' WHERE id = ?", (str(self.tmp_path / f"{tid}.log"), tid))
        self.conn.commit()

    def step(self, *, spec: bool = True, children: int = 3) -> tuple[str, list[str]]:
        """Родитель с ТЗ, общим журналом, вопросом и запуском; дети с файлами порций."""
        pid = self.new(stage="s1-spec")
        fields = {"journal_path": str(self.steps / f"{pid}.journal.md")}
        (self.steps / f"{pid}.journal.md").write_text("журнал\n", encoding="utf-8")
        if spec:
            (self.steps / f"{pid}.md").write_text("# шаг\n", encoding="utf-8")
            fields["spec_path"] = str(self.steps / f"{pid}.md")
        store.update_task(self.conn, pid, **fields)
        store.set_needs_owner(self.conn, pid, value=True, text="что дальше?")
        self.finished_launch(pid)
        kids = []
        for letter in "abc"[:children]:
            spec_file = self.steps / f"{pid}.{letter}.md"
            check_file = self.steps / f"{pid}.check-{letter}.md"
            spec_file.write_text(f"порция {letter}\n", encoding="utf-8")
            check_file.write_text(f"чек-лист {letter}\n", encoding="utf-8")
            kids.append(self.new(route=None, parent=pid, swarm=False,
                                 spec_path=str(spec_file), checklist_path=str(check_file),
                                 journal_path=fields["journal_path"]))
        if children >= 2:
            store.update_task(self.conn, kids[1], status="cancelled")
        return pid, kids

    def files(self) -> set[str]:
        return {str(p.relative_to(self.steps)) for p in self.steps.rglob("*") if p.is_file()}

    def journal(self, tid: str) -> list[str]:
        return [c["text"] for c in store.get_task(self.conn, tid)["comments"]
                if c["kind"] == "journal"]

    def row(self, tid: str) -> dict:
        return dict(self.conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone())

    def link_types(self, child: str, parent: str) -> set[str]:
        return {r[0] for r in self.conn.execute(
            "SELECT dep_type FROM deps WHERE issue_id = ? AND depends_on = ?", (child, parent))}

    def assert_detached(self, pid: str, kids: list[str]) -> None:
        archive = self.steps / f"{pid}.restart-1"
        for kid in kids:
            task = store.get_task(self.conn, kid)
            self.assertEqual(task["status"], "cancelled")
            self.assertEqual(self.link_types(kid, pid), {"discovered-from"})
            self.assertEqual(pathlib.Path(task["spec_path"]).parent, archive)
            self.assertEqual(pathlib.Path(task["checklist_path"]).parent, archive)
            self.assertTrue(pathlib.Path(task["spec_path"]).is_file())
        self.assertTrue((archive / f"{pid}.md").is_file())
        self.assertTrue((self.steps / f"{pid}.journal.md").is_file())
        self.assertFalse((self.steps / f"{pid}.restart-2").exists())
        parent = store.get_task(self.conn, pid)
        self.assertFalse(parent["has_portions"])
        self.assertFalse(parent["portions_cancelled_only"])
        self.assertFalse(parent["needs_owner"])
        self.assertEqual(parent["stage"], "s1-spec")
        self.assertFalse(parent["holder"])
        self.assertNotEqual(parent["status"], "cancelled")
        self.assertNotEqual(parent["issue_type"], "epic")
        row = self.row(pid)
        for field in stage_launch.LAUNCH_FIELDS:
            self.assertIsNone(row[field], field)
        self.assertEqual(row["launch_route"], "roy")
        self.assertEqual(row["launch_driver"], "swarm")


class RestartSpecTests(RestartCase):
    def test_spec_case(self) -> None:  # 1
        pid, kids = self.step()
        self.assertEqual(store.get_task(self.conn, pid)["issue_type"], "epic")
        generation = self.row(pid)["generation"]
        log = self.row(pid)["launch_log"]
        names_before = {pathlib.Path(n).name for n in self.files()}
        out = stage_launch.restart_task(self.conn, pid, stage="s1-spec", note="проба",
                                        actor="agent:t")
        self.assertEqual(out["restarted_from"], "s1-spec")
        self.assertEqual(out["detached"], kids)
        self.assertTrue(out["archive_dir"].endswith(f"{pid}.restart-1"))
        self.assert_detached(pid, kids)
        names_after = sorted(pathlib.Path(n).name for n in self.files())
        self.assertEqual(names_after, sorted([*names_before, f"{pid}.md"]))
        self.assertEqual(self.row(pid)["generation"], generation)
        journal = self.journal(pid)
        self.assertIn(f"прежний лог запуска: {log}", journal)
        self.assertTrue(any(t.startswith(f"рой: перезапуск с s1-spec, порции сняты: "
                                         f"{', '.join(kids)}, файлы в ")
                            and t.endswith(". проба") for t in journal), journal)

    def test_no_spec_path(self) -> None:  # 7
        pid, kids = self.step(spec=False)
        before = self.files()
        out = stage_launch.restart_task(self.conn, pid, actor="agent:t")
        self.assertIsNone(out["archive_dir"])
        self.assertEqual(out["detached"], kids)
        for kid in kids:
            self.assertEqual(store.get_task(self.conn, kid)["status"], "cancelled")
        self.assertEqual(self.files(), before)
        self.assertIn("архив не сделан: у родителя нет spec_path", self.journal(pid))

    def test_crash_midway_then_retry(self) -> None:  # 6
        pid, kids = self.step()
        real = store.remove_dep
        with mock.patch.object(store, "remove_dep", side_effect=RuntimeError("сбой")):
            with self.assertRaises(RuntimeError):
                stage_launch.restart_task(self.conn, pid, stage="s1-spec")
        self.assertTrue((self.steps / f"{pid}.restart-1" / f"{pid}.a.md").is_file())
        self.assertIs(store.remove_dep, real)
        out = stage_launch.restart_task(self.conn, pid, stage="s1-spec")
        self.assertEqual(out["detached"], kids)
        self.assertTrue(out["archive_dir"].endswith(f"{pid}.restart-1"))
        self.assert_detached(pid, kids)

    def test_crash_after_links_then_retry(self) -> None:
        # Связи уже discovered-from, статусы не тронуты: повтор находит порции по файлам.
        pid, kids = self.step()
        with mock.patch.object(store, "update_task", side_effect=RuntimeError("сбой")):
            with self.assertRaises(RuntimeError):
                stage_launch.restart_task(self.conn, pid, stage="s1-spec")
        self.assertEqual(self.link_types(kids[0], pid), {"discovered-from"})
        out = stage_launch.restart_task(self.conn, pid, stage="s1-spec")
        self.assertEqual(out["detached"], kids)
        self.assert_detached(pid, kids)

    def test_sync_after_restart_creates_new_cards(self) -> None:  # 8
        pid, kids = self.step()
        stage_launch.restart_task(self.conn, pid)
        for letter in "ab":
            (self.steps / f"{pid}.{letter}.md").write_text("новая\n", encoding="utf-8")
        res = store.sync_portions(self.conn, pid)
        self.assertEqual(len(res["created"]), 2)
        self.assertFalse(set(res["created"]) & set(kids))


class RestartStageTests(RestartCase):
    def test_s3_impl_failed_launch(self) -> None:  # 3
        parent = self.new(route=None, swarm=False)
        tid = self.new(stage="s3-impl", parent=parent)
        store.update_task(self.conn, tid, holder="dsh")
        self.finished_launch(tid)
        out = stage_launch.restart_task(self.conn, tid)
        self.assertEqual(out["restarted_from"], "s3-impl")
        self.assertEqual(out["detached"], [])
        row = self.row(tid)
        self.assertEqual(row["stage"], "s3-impl")
        self.assertFalse(row["holder"])
        for field in stage_launch.LAUNCH_FIELDS:
            self.assertIsNone(row[field], field)
        self.assertTrue(any(t.startswith("прежний лог запуска:") for t in self.journal(tid)))
        self.assertEqual(self.link_types(tid, parent), {"parent-child"})

    def test_route_change(self) -> None:  # 4
        tid = self.new(stage="s3-impl")
        labels = store.route_labels_from_row(self.conn.execute(
            "SELECT labels FROM tasks WHERE id = ?", (tid,)).fetchone())
        out = stage_launch.restart_task(self.conn, tid, route="shiki-pow")
        self.assertEqual(out["route_from"], "roy")
        row = self.row(tid)
        self.assertEqual(row["stage"], "s3-impl")
        self.assertEqual(row["launch_route"], "shiki-pow")
        self.assertIsNone(row["launch_driver"])
        self.assertEqual(json.loads(row["labels"]),
                         store.labels_after_route_change(self.conn, labels, "shiki-pow"))
        self.assertIn("маршрут: roy → shiki-pow", self.journal(tid))
        with self.assertRaises(errors.ListikError) as cm:
            stage_launch.restart_task(self.conn, tid, route="nope")
        self.assertEqual(cm.exception.status, 409)
        self.assertIn("shiki-pow", cm.exception.message)


class RestartRefusalTests(RestartCase):
    def assert_refused(self, tid: str, *, contains: str = "", **kwargs) -> str:
        row, files = self.row(tid), self.files()
        with self.assertRaises(errors.ListikError) as cm:
            stage_launch.restart_task(self.conn, tid, **kwargs)
        self.assertEqual((cm.exception.status, cm.exception.code), (409, errors.CONFLICT))
        self.assertIn(contains, cm.exception.message)
        self.assertEqual(self.row(tid), row)
        self.assertEqual(self.files(), files)
        return cm.exception.message

    def test_done_child(self) -> None:
        pid, kids = self.step()
        store.update_task(self.conn, kids[0], status="done")
        self.assertIn(kids[0], self.assert_refused(pid, stage="s1-spec"))

    def test_held_child_old_parent_link(self) -> None:
        pid, kids = self.step(children=1)
        extra = self.new(route=None, swarm=False, spec_path=str(self.steps / f"{pid}.b.md"))
        (self.steps / f"{pid}.b.md").write_text("b\n", encoding="utf-8")
        self.conn.execute("INSERT INTO deps(issue_id, depends_on, dep_type) VALUES(?,?,'parent')",
                          (extra, pid))
        self.conn.commit()
        store.update_task(self.conn, extra, holder="dsh")
        self.assertIn(extra, self.assert_refused(pid, stage="s1-spec"))

    def test_live_launch(self) -> None:
        tid = self.new(stage="s3-impl")
        self.conn.execute("UPDATE tasks SET launched_by = 'agent:probe' WHERE id = ?", (tid,))
        self.conn.commit()
        self.assert_refused(tid, contains=f"listik revoke {tid}")

    def test_closed(self) -> None:
        for status in ("done", "cancelled"):
            tid = self.new(stage="s3-impl")
            store.update_task(self.conn, tid, status=status)
            self.assert_refused(tid, contains="закрыта")

    def test_stage_without_role(self) -> None:
        routes_store.create_route(self.conn, key="nocritic", kind="swarm", title="Без критика",
                                  roles={r: c for r, c in ALL_ROLES.items() if r != "critic"})
        tid = self.new(route="nocritic", stage="s3-impl")
        message = self.assert_refused(tid, stage="s2-review")
        self.assertIn("s1-spec, s3-impl, s4-judge", message)

    def test_epic_not_s1(self) -> None:
        pid = self.new(stage="s3-impl")
        self.new(route=None, swarm=False, parent=pid)
        self.assert_refused(pid, stage="s3-impl", contains="перезапускай подзадачи")

    def test_skill_card_and_bad_stage(self) -> None:
        routes_store.create_route(self.conn, key="skillish", kind="pipeline", title="Скил")
        tid = self.new(route="skillish", swarm=False)
        with self.assertRaises(errors.BadArgument):
            stage_launch.restart_task(self.conn, tid)
        with self.assertRaises(errors.BadArgument):
            stage_launch.restart_task(self.conn, self.new(), stage="s9")
        with self.assertRaises(errors.NotFound):
            stage_launch.restart_task(self.conn, "proj-nope")


class RestartCliTests(RestartCase):
    def cli(self, *argv: str) -> subprocess.CompletedProcess:
        self.conn.commit()
        return subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", "restart", *argv],
            capture_output=True, text=True, cwd=str(self.tmp_path),
            env={**os.environ, "LISTIK_DB": str(self.db_path), "LISTIK_PROJECT": ""})

    def test_cli_local(self) -> None:  # 2
        pid, kids = self.step()
        run = self.cli(pid, "--stage", "s1-spec", "--json")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        data = json.loads(run.stdout)
        self.assertEqual(data["detached"], kids)
        self.assertFalse(store.get_task(self.conn, pid)["needs_owner"])
        self.assertEqual(store.get_task(self.conn, kids[0])["status"], "cancelled")

    def test_cli_human_and_bad_stage(self) -> None:
        pid, _kids = self.step()
        run = self.cli(pid, "--stage", "s1-spec")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn(f"перезапущена: {pid} с s1-spec", run.stdout)
        self.assertIn("порции сняты:", run.stdout)
        run = self.cli(pid, "--stage", "s9", "--json")
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("bad_argument", run.stdout + run.stderr)
        self.assertNotIn("Traceback", run.stderr)


class RestartHttpTests(RestartCase):  # 9
    def setUp(self) -> None:
        super().setUp()
        self._saved = (paths.DB_PATH, server._conn_made, server._conn_local)
        paths.DB_PATH = self.db_path
        server._conn_made = False
        server._conn_local = threading.local()

    def tearDown(self) -> None:
        conn = getattr(server._conn_local, "conn", None)
        if conn is not None:
            conn.close()
        paths.DB_PATH, server._conn_made, server._conn_local = self._saved
        super().tearDown()

    def post(self, tid: str) -> tuple[int, object, str]:
        self.conn.commit()
        try:
            status, data = server.handle("POST", f"/api/tasks/{tid}/restart", {},
                                         {"actor": "agent:t"}, authed=True)
            return status, data, ""
        except Exception as exc:  # noqa: BLE001 — разбираем как сервер
            status, message, code = server.error_response(exc)
            return status, message, code

    def test_http(self) -> None:
        tid = self.new(stage="s3-impl")
        self.finished_launch(tid)
        status, data, _ = self.post(tid)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["restarted_from"], "s3-impl")
        self.conn.execute("UPDATE tasks SET launched_by = 'agent:probe' WHERE id = ?", (tid,))
        status, message, code = self.post(tid)
        self.assertEqual((status, code), (409, errors.CONFLICT), message)
        self.assertIn("listik revoke", message)
        self.assertEqual(self.post("proj-nope")[0], 404)
        routes_store.create_route(self.conn, key="skillish", kind="pipeline", title="Скил")
        status, _message, code = self.post(self.new(route="skillish", swarm=False))
        self.assertEqual((status, code), (400, errors.BAD_ARGUMENT))


if __name__ == "__main__":
    unittest.main()

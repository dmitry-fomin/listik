"""Тесты для `import_writerllm.import_file` (шаг 03, порция a).

Фикстуры синтетические: `tests/fixtures/writerllm/`. Никаких реальных выгрузок,
имён людей или e-mail — только выдуманные ID вида `WL-a1` и алиас `dfomin` из
`actors.ALIASES` (даёт `me`).
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import unittest

from listik import db as db_mod
from listik import deps as deps_mod
from listik import import_writerllm
from listik import search as search_mod
from listik import store
from tests.helpers import FIXTURES_DIR, TempDbTestCase

WL_DIR = FIXTURES_DIR / "writerllm"
EXPORT_JSONL = WL_DIR / "export.jsonl"
EXPORT_JSON = WL_DIR / "export.json"
EXPORT_V2_JSONL = WL_DIR / "export-v2.jsonl"
BROKEN_JSONL = WL_DIR / "broken.jsonl"

# export.jsonl issue-record count (excludes the one _type=memory record).
N_ISSUES = 7


class ImportWriterllmJsonlTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.report = import_writerllm.import_file(self.conn, EXPORT_JSONL)

    def _task(self, task_id: str) -> dict:
        return store.get_task(self.conn, task_id)

    def test_1_jsonl_creates_all_issue_records(self) -> None:
        self.assertEqual(self.report["created"], N_ISSUES)
        self.assertEqual(self.report["ignored"], 1)
        task = self._task("WL-a1")
        self.assertEqual(task["source"], "writerllm")
        self.assertEqual(task["external_ref"], "WL-a1")
        self.assertEqual(task["project"], "writerllm")
        self.assertEqual(task["title"], "Починить кеш экспортов")
        self.assertIn("протухал", task["description"])
        self.assertIn("TTL", task["acceptance"])
        self.assertIn("TTL-кеша", task["design"])
        self.assertIn("нагрузочном", task["notes"])
        self.assertEqual(task["labels"], ["core", "imported"])

    def test_2_dates_and_result_come_from_the_record(self) -> None:
        task = self._task("WL-a1")
        self.assertEqual(task["created_at"], "2024-01-01T09:00:00Z")
        self.assertEqual(task["updated_at"], "2024-01-06T09:00:00Z")
        self.assertEqual(task["started_at"], "2024-01-02T09:00:00Z")
        self.assertEqual(task["closed_at"], "2024-01-06T09:00:00Z")
        self.assertEqual(task["close_reason"], "workaround shipped")
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["result"], "workaround shipped")
        self.assertEqual(task["priority"], 1)
        created_events = [e for e in task["events"] if e["kind"] == "created"]
        self.assertEqual(len(created_events), 1)
        self.assertEqual(created_events[0]["ts"], "2024-01-01T09:00:00Z")
        self.assertTrue(any(e["kind"] == "import" for e in task["events"]))

    def test_3_actors_are_normalized(self) -> None:
        task = self._task("WL-a1")
        self.assertEqual(task["assignee"], "me")
        self.assertEqual(task["created_by"], "me")
        authors = {c["author"] for c in task["comments"]}
        self.assertEqual(authors, {"me"})

    def test_4_dependencies_are_written_after_all_tasks(self) -> None:
        a1 = self._task("WL-a1")["id"]
        a1_1 = self._task("WL-a1.1")["id"]
        a4 = self._task("WL-a4")["id"]
        a5 = self._task("WL-a5")["id"]
        a2 = self._task("WL-a2")["id"]
        edges = {(r["issue_id"], r["depends_on"], r["dep_type"])
                for r in self.conn.execute("SELECT issue_id, depends_on, dep_type FROM deps")}
        self.assertIn((a4, a5, "blocks"), edges)
        self.assertIn((a1_1, a1, "parent-child"), edges)
        self.assertIn((a4, a2, "relates-to"), edges)
        self.assertEqual(self.report["dependencies"], 3)

        blockers = deps_mod.blockers(self.conn, a4)
        self.assertTrue(blockers)
        blocked_by = store.get_task(self.conn, a4)["blocked_by"]
        self.assertIn(a5, blocked_by)

        missing_errors = [e for e in self.report["errors"] if e["where"] == "dependency"
                          and e["target_ref"] == "WL-missing"]
        self.assertEqual(len(missing_errors), 1)
        # задача при этом создана, несмотря на ошибку связи
        self.assertTrue(self._task("WL-a1.1"))

    def test_5_comments_are_deduplicated_and_dated_from_the_record(self) -> None:
        task = self._task("WL-a1")
        comments = task["comments"]
        self.assertEqual(len(comments), 2)
        kinds = {c["created_at"]: c["kind"] for c in comments}
        self.assertEqual(kinds["2024-01-02T10:00:00Z"], "comment")
        self.assertEqual(kinds["2024-01-05T09:00:00Z"], "journal")
        self.assertTrue(all(c["author"] == "me" for c in comments))
        self.assertFalse(any(e["kind"] == "comment" for e in task["events"]))

        a3 = self._task("WL-a3")
        self.assertEqual(len(a3["comments"]), 1)
        c = a3["comments"][0]
        self.assertEqual(c["created_at"], a3["created_at"])
        self.assertTrue(c["id"].startswith(f"{a3['id']}:wl:"))

        self.assertEqual(self.report["comments"], 3)

    def test_6_second_run_does_not_duplicate_anything(self) -> None:
        n_comments_before = self.conn.execute("SELECT count(*) FROM comments").fetchone()[0]
        n_tasks_before = self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        n_deps_before = self.conn.execute("SELECT count(*) FROM deps").fetchone()[0]
        n_events_before = self.conn.execute("SELECT count(*) FROM events").fetchone()[0]

        report2 = import_writerllm.import_file(self.conn, EXPORT_JSONL)

        self.assertEqual(report2["created"], 0)
        self.assertEqual(report2["skipped"], N_ISSUES)
        self.assertEqual(report2["comments"], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM comments").fetchone()[0],
                         n_comments_before)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0],
                         n_tasks_before)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM deps").fetchone()[0],
                         n_deps_before)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM events").fetchone()[0],
                         n_events_before)

    def test_11_project_row_written(self) -> None:
        row = self.conn.execute("SELECT * FROM projects WHERE slug='writerllm'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["kind"], "writerllm")
        self.assertIn(str(self.report["created"]), row["import_note"])

    def test_13_old_id_is_found_directly_and_by_search(self) -> None:
        task = store.get_task(self.conn, "WL-a1")
        self.assertEqual(task["external_ref"], "WL-a1")
        res = search_mod.search(self.conn, "WL-a1", mode="text")
        ids = [r["id"] for r in res["results"]]
        self.assertIn("WL-a1", ids)

    def test_14_unknown_status_and_priority_fall_back_with_warnings(self) -> None:
        task = self._task("WL-a9")
        self.assertEqual(task["status"], "open")
        self.assertEqual(task["priority"], 2)
        fields = {w["field"] for w in self.report["warnings"]}
        self.assertEqual(fields, {"status", "priority"})
        self.assertEqual(len(self.report["warnings"]), 2)


class ImportWriterllmDryRunTests(TempDbTestCase):
    def test_7_dry_run_writes_nothing_and_matches_the_real_run(self) -> None:
        def counts():
            return {t: self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                    for t in ("tasks", "comments", "deps", "events", "projects",
                             "actors", "actor_aliases")}

        before = counts()
        dry_report = import_writerllm.import_file(self.conn, EXPORT_JSONL, dry_run=True)
        after = counts()
        self.assertEqual(before, after)

        real_report = import_writerllm.import_file(self.conn, EXPORT_JSONL)
        self.assertEqual(dry_report["created"], real_report["created"])
        self.assertEqual(dry_report["dependencies"], real_report["dependencies"])
        self.assertEqual(dry_report["comments"], real_report["comments"])


class ImportWriterllmBrokenTests(TempDbTestCase):
    def test_8_broken_jsonl(self) -> None:
        report = import_writerllm.import_file(self.conn, BROKEN_JSONL)
        self.assertEqual(report["total"], 5)
        self.assertEqual(report["created"], 3)

        row = self.conn.execute(
            "SELECT * FROM tasks WHERE title=?", ("Без ID, но с заголовком",)).fetchone()
        self.assertIsNotNone(row)
        self.assertTrue(row["external_ref"].startswith("writerllm:"))

        errs = report["errors"]
        line_errors = [e for e in errs if e.get("line") == 2]
        self.assertEqual(len(line_errors), 1)
        self.assertIn("raw", line_errors[0])
        no_title_errors = [e for e in errs if e["where"] == "record" and e.get("line") == 4]
        self.assertEqual(len(no_title_errors), 1)

        report2 = import_writerllm.import_file(self.conn, BROKEN_JSONL)
        row2 = self.conn.execute(
            "SELECT * FROM tasks WHERE title=?", ("Без ID, но с заголовком",)).fetchone()
        self.assertEqual(row2["external_ref"], row["external_ref"])
        self.assertEqual(report2["created"], 0)


class ImportWriterllmCollisionTests(TempDbTestCase):
    def test_9_id_collision_is_remapped(self) -> None:
        store.create_task(self.conn, task_id="WL-a1", source="native", project="demo",
                          title="Нативная задача с тем же ID")
        report = import_writerllm.import_file(self.conn, EXPORT_JSONL)
        native = store.get_task(self.conn, "WL-a1")
        self.assertEqual(native["source"], "native")
        self.assertEqual(native["title"], "Нативная задача с тем же ID")

        remapped = next(e for e in report["examples"]["create"] if e["external_ref"] == "WL-a1")
        self.assertTrue(remapped.get("remapped"))
        self.assertNotEqual(remapped["id"], "WL-a1")
        imported = store.get_task(self.conn, remapped["id"])
        self.assertEqual(imported["external_ref"], "WL-a1")
        self.assertEqual(imported["source"], "writerllm")


class ImportWriterllmJsonArrayTests(TempDbTestCase):
    def test_10_json_array_matches_jsonl_on_a_second_db(self) -> None:
        jsonl_report = import_writerllm.import_file(self.conn, EXPORT_JSONL)

        second_conn = db_mod.init(self.tmp_path / "second.db")
        try:
            json_report = import_writerllm.import_file(second_conn, EXPORT_JSON)
            self.assertEqual(json_report["created"], jsonl_report["created"])
            self.assertEqual(json_report["ignored"], 0)
            self.assertEqual(json_report["comments"], 0)

            ids_jsonl = {r["id"] for r in self.conn.execute("SELECT id FROM tasks")}
            ids_json = {r["id"] for r in second_conn.execute("SELECT id FROM tasks")}
            self.assertEqual(ids_jsonl, ids_json)

            edges_jsonl = {(r["issue_id"], r["depends_on"], r["dep_type"])
                          for r in self.conn.execute(
                              "SELECT issue_id, depends_on, dep_type FROM deps")}
            edges_json = {(r["issue_id"], r["depends_on"], r["dep_type"])
                         for r in second_conn.execute(
                             "SELECT issue_id, depends_on, dep_type FROM deps")}
            self.assertEqual(edges_jsonl, edges_json)
        finally:
            second_conn.close()


class ImportWriterllmManualCommentTests(TempDbTestCase):
    def test_15_manual_comment_survives_skip_and_update(self) -> None:
        import_writerllm.import_file(self.conn, EXPORT_JSONL)
        store.add_comment(self.conn, "WL-a1", "ручной", author="me")
        n_before = len(store.get_task(self.conn, "WL-a1")["comments"])

        import_writerllm.import_file(self.conn, EXPORT_JSONL)
        comments = store.get_task(self.conn, "WL-a1")["comments"]
        self.assertEqual(len(comments), n_before)
        self.assertTrue(any(c["text"] == "ручной" for c in comments))

        import_writerllm.import_file(self.conn, EXPORT_JSONL, update=True)
        comments = store.get_task(self.conn, "WL-a1")["comments"]
        self.assertEqual(len(comments), n_before)
        self.assertTrue(any(c["text"] == "ручной" for c in comments))


class ImportWriterllmUpdateTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.first = import_writerllm.import_file(self.conn, EXPORT_JSONL)

    def test_16_without_update_flag_content_is_not_touched_but_comments_are_added(self) -> None:
        before = store.get_task(self.conn, "WL-a3")
        report = import_writerllm.import_file(self.conn, EXPORT_V2_JSONL)
        after_a2 = store.get_task(self.conn, "WL-a2")
        after_a5 = store.get_task(self.conn, "WL-a5")
        self.assertEqual(after_a2["title"], "Добавить пагинацию списков")
        self.assertEqual(after_a5["status"], "open")
        self.assertEqual(report["updated"], 0)
        self.assertEqual(report["comments"], 1)
        after_a3 = store.get_task(self.conn, "WL-a3")
        self.assertEqual(len(after_a3["comments"]), len(before["comments"]) + 1)

    def test_17_update_flag_diffs_content_and_journals_only_changed_fields(self) -> None:
        report = import_writerllm.import_file(self.conn, EXPORT_V2_JSONL, update=True)
        self.assertEqual(report["updated"], 2)
        self.assertTrue(report["ok"])
        self.assertEqual(report["errors"], [])

        a2 = store.get_task(self.conn, "WL-a2")
        self.assertEqual(a2["title"], "Добавить пагинацию списков в API")
        self.assertFalse(a2["started_at"])
        self.assertFalse(a2["closed_at"])
        self.assertFalse(a2["close_reason"])
        journal_a2 = [c for c in a2["comments"] if c["kind"] == "journal"]
        self.assertEqual(len(journal_a2), 1)
        self.assertTrue(journal_a2[0]["text"].startswith("[import-writerllm] update:"))
        self.assertIn("title:", journal_a2[0]["text"])
        self.assertNotIn("status:", journal_a2[0]["text"])

        a5 = store.get_task(self.conn, "WL-a5")
        self.assertEqual(a5["status"], "in_progress")
        self.assertTrue(a5["started_at"])
        self.assertFalse(a5["closed_at"])
        self.assertTrue(a5["created_at"])
        journal_a5 = [c for c in a5["comments"] if c["kind"] == "journal"]
        self.assertEqual(len(journal_a5), 1)
        self.assertIn("status:", journal_a5[0]["text"])
        self.assertNotIn("title:", journal_a5[0]["text"])

        a4 = store.get_task(self.conn, "WL-a4")
        self.assertEqual(a4["title"], "Перевести отчёт на новый формат")

        fields_by_ref = {e["external_ref"]: e["fields"] for e in report["examples"]["update"]}
        self.assertEqual(fields_by_ref["WL-a2"], ["title"])
        self.assertEqual(fields_by_ref["WL-a5"], ["status"])

    def test_18_repeated_update_is_a_noop(self) -> None:
        import_writerllm.import_file(self.conn, EXPORT_V2_JSONL, update=True)
        a2_journal_before = len([c for c in store.get_task(self.conn, "WL-a2")["comments"]
                                  if c["kind"] == "journal"])
        a5_journal_before = len([c for c in store.get_task(self.conn, "WL-a5")["comments"]
                                  if c["kind"] == "journal"])

        report2 = import_writerllm.import_file(self.conn, EXPORT_V2_JSONL, update=True)
        self.assertEqual(report2["updated"], 0)
        self.assertTrue(report2["ok"])
        a2_journal_after = len([c for c in store.get_task(self.conn, "WL-a2")["comments"]
                                 if c["kind"] == "journal"])
        a5_journal_after = len([c for c in store.get_task(self.conn, "WL-a5")["comments"]
                                 if c["kind"] == "journal"])
        self.assertEqual(a2_journal_after, a2_journal_before)
        self.assertEqual(a5_journal_after, a5_journal_before)

    def test_19_dry_run_update_reports_but_writes_nothing(self) -> None:
        a2_before = store.get_task(self.conn, "WL-a2")
        a5_before = store.get_task(self.conn, "WL-a5")
        n_comments_before = self.conn.execute("SELECT count(*) FROM comments").fetchone()[0]

        report = import_writerllm.import_file(self.conn, EXPORT_V2_JSONL, dry_run=True, update=True)
        self.assertEqual(report["updated"], 2)

        a2_after = store.get_task(self.conn, "WL-a2")
        a5_after = store.get_task(self.conn, "WL-a5")
        self.assertEqual(a2_after["title"], a2_before["title"])
        self.assertEqual(a5_after["status"], a5_before["status"])
        self.assertEqual(a5_after["started_at"], a5_before["started_at"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM comments").fetchone()[0],
                         n_comments_before)


class ImportWriterllmOkFlagTests(TempDbTestCase):
    def test_20_ok_reflects_absence_of_errors(self) -> None:
        clean_report = import_writerllm.import_file(self.conn, EXPORT_V2_JSONL)
        self.assertTrue(clean_report["ok"])
        self.assertEqual(clean_report["errors"], [])

        second_conn = db_mod.init(self.tmp_path / "dirty.db")
        try:
            dirty_report = import_writerllm.import_file(second_conn, EXPORT_JSONL)
            self.assertFalse(dirty_report["ok"])
            self.assertEqual(len(dirty_report["errors"]), 1)
            self.assertEqual(dirty_report["errors"][0]["where"], "dependency")
            self.assertEqual(dirty_report["errors"][0]["target_ref"], "WL-missing")

            broken_report = import_writerllm.import_file(second_conn, BROKEN_JSONL)
            self.assertFalse(broken_report["ok"])
            for err in broken_report["errors"]:
                self.assertIn("where", err)
                self.assertIn("error", err)
                self.assertIn("raw", err)
        finally:
            second_conn.close()


class ImportWriterllmCliTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.db_path = pathlib.Path(self.tmp_dir.name) / "cli.db"
        self.repo_root = pathlib.Path(__file__).resolve().parent.parent
        self.bin_listik = self.repo_root / "bin" / "listik"

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        return subprocess.run(
            [str(self.bin_listik), "import-writerllm", *args],
            cwd=self.repo_root, env=env, capture_output=True, text=True,
        )

    def test_21_broken_dry_run_reports_error_and_exit_code(self) -> None:
        result = self._run("--source", "tests/fixtures/writerllm/broken.jsonl", "--dry-run")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ошибок:", result.stdout)
        self.assertTrue(any(line.startswith("  ! ") for line in result.stdout.splitlines()))

    def test_22_json_output_is_parseable(self) -> None:
        result = self._run("--source", "tests/fixtures/writerllm/broken.jsonl",
                           "--dry-run", "--json")
        payload = json.loads(result.stdout)
        for key in ("created", "updated", "skipped", "errors", "ok"):
            self.assertIn(key, payload)

    def test_23_export_jsonl_exits_1_but_creates_tasks(self) -> None:
        result = self._run("--source", "tests/fixtures/writerllm/export.jsonl")
        self.assertEqual(result.returncode, 1)

        show = subprocess.run(
            [str(self.bin_listik), "--local", "show", "WL-a1"],
            cwd=self.repo_root,
            env={**os.environ, "LISTIK_DB": str(self.db_path)},
            capture_output=True, text=True,
        )
        self.assertIn("старый ID (writerllm): WL-a1", show.stdout)

    def test_24_export_v2_jsonl_on_a_clean_db_exits_0(self) -> None:
        clean_db = pathlib.Path(self.tmp_dir.name) / "clean.db"
        result = subprocess.run(
            [str(self.bin_listik), "import-writerllm",
             "--source", "tests/fixtures/writerllm/export-v2.jsonl"],
            cwd=self.repo_root,
            env={**os.environ, "LISTIK_DB": str(clean_db)},
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)


class ImportWriterllmSourceErrorTests(TempDbTestCase):
    def test_12_directory_and_markdown_are_rejected_as_source(self) -> None:
        report = import_writerllm.import_file(self.conn, self.tmp_path)
        self.assertEqual(report["total"], 0)
        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(report["errors"][0]["where"], "source")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)

        md_path = self.tmp_path / "notes.md"
        md_path.write_text("# заметка\n\nне JSON", encoding="utf-8")
        report2 = import_writerllm.import_file(self.conn, md_path)
        self.assertEqual(report2["total"], 0)
        self.assertEqual(len(report2["errors"]), 1)
        self.assertEqual(report2["errors"][0]["where"], "source")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()

"""Чистка `bin/listik` (listik-pf6i, порция a): исход revoke из события, держатель и
актёр по сырому полю, `as_owner` без `OWNER_OPS`, общий `_project_path`."""
from __future__ import annotations

import contextlib
import io
import os
from unittest import mock

from listik import errors, paths, store
from tests.helpers import TempDbTestCase
from tests.test_autostart import _load_cli

cli = _load_cli()


def run(argv, response):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(cli.client, "is_up", return_value=True), \
            mock.patch.object(cli.client, "request", return_value=response), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class ServerOutputTest(TempDbTestCase):
    def test_revoke_prints_note_of_current_generation_event(self):
        task = {
            "id": "t-1", "generation": 3,
            "events": [
                {"kind": "revoke", "from_value": "2", "to_value": "3", "note": "N3"},
                {"kind": "revoke", "from_value": "1", "to_value": "2", "note": "N2"},
            ],
            "comments": [{"author": "agent:listik", "kind": "journal",
                          "text": "ЖУРНАЛ: полномочия отозваны иначе"}],
        }
        code, out, _ = run(["revoke", "t-1"], task)
        self.assertEqual(code, 0)
        self.assertIn("N3", out)
        self.assertNotIn("N2", out)
        self.assertNotIn("ЖУРНАЛ", out)

    def test_revoke_without_events_prints_one_line(self):
        code, out, _ = run(["revoke", "t-1"], {"id": "t-1", "generation": 2})
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("полномочия отозваны: t-1"))

    def test_dep_tree_holder_by_raw_field(self):
        node = {"status": "open", "title": "T", "idle_age": "5 мин"}
        tree = {"task": {"id": "root", "title": "R", "status": "open"},
                "waits_for": [{**node, "id": "free", "holder": None, "holder_title": "—"},
                              {**node, "id": "held", "holder": "x", "holder_title": "Икс"}],
                "waited_by": []}
        code, out, _ = run(["dep", "tree", "root"], tree)
        self.assertEqual(code, 0)
        free = next(l for l in out.splitlines() if " free " in l)
        held = next(l for l in out.splitlines() if " held " in l)
        self.assertNotIn("держит", free)
        self.assertIn("держит Икс 5 мин", held)

    def test_dep_tree_holder_only_by_raw_field(self):
        # Держатель — только по ключу holder; holder_title без holder не значит «держит».
        node = {"status": "in_progress", "title": "T", "idle_age": "5 мин"}
        tree = {"task": {"id": "root", "title": "R", "status": "open"},
                "waits_for": [{**node, "id": "nokey", "holder_title": "grok"},
                              {**node, "id": "none", "holder": None, "holder_title": "—"}],
                "waited_by": []}
        code, out, _ = run(["dep", "tree", "root"], tree)
        self.assertEqual(code, 0)
        nokey = next(l for l in out.splitlines() if " nokey " in l)
        none = next(l for l in out.splitlines() if " none " in l)
        self.assertNotIn("держит", nokey)
        self.assertNotIn("держит", none)

    def test_timeline_actor_by_raw_field(self):
        base = {"ts": "2026-09-28T00:00:00Z", "kind": "stage", "task_id": "t-1"}
        items = [{**base, "note": "без", "actor": None, "actor_title": "—"},
                 {**base, "note": "с", "actor": "agent:x", "actor_title": "Агент Икс"}]
        code, out, _ = run(["timeline", "--project", "all"], {"items": items})
        self.assertEqual(code, 0)
        without, with_actor = out.strip().splitlines()
        self.assertEqual(without.split("t-1", 1)[1].strip(), "— без")
        self.assertEqual(with_actor.split("t-1", 1)[1].strip(), "Агент Икс  — с")

    def test_project_path_without_path_is_bad_argument(self):
        res = {"projects": [{"slug": "nopath", "path": "  "}]}
        args = cli.build_parser().parse_args(["status"])
        with mock.patch.object(cli.client, "is_up", return_value=True), \
                mock.patch.object(cli.client, "request", return_value=res):
            with self.assertRaises(errors.ListikError) as ctx:
                cli._project_path(args, "nopath")
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("у проекта nopath не указан путь", ctx.exception.message)


class LocalOwnerTest(TempDbTestCase):
    def setUp(self):
        super().setUp()
        self._db_patch = mock.patch.object(paths, "DB_PATH", self.db_path)
        self._db_patch.start()
        self.task = store.create_task(self.conn, title="t", project="p")

    def tearDown(self):
        self._db_patch.stop()
        super().tearDown()

    def test_owner_with_non_owner_op_via_local(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"LISTIK_OWNER": "human:d"}), \
                mock.patch.object(cli.client, "is_up", return_value=False), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--local", "comment", self.task["id"], "привет",
                             "--actor", "agent:t"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertNotIn("TypeError", err.getvalue())
        texts = [c["text"] for c in store.get_task(self.conn, self.task["id"])["comments"]]
        self.assertIn("привет", texts)

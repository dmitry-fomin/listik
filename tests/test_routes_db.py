"""Тесты таблицы `routes` и ввоза `routes.json` (шаг 09, порция b).

База — временная (`TempDbTestCase`), настоящий `~/.config/listik/` не трогается:
у ввоза всегда явный путь, а `SOURCE_PATH` подменяется на файл во временном каталоге.
Файл `routes.json` из корня репозитория только читается.
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from listik import db as db_mod
from listik import errors
from listik import launcher
from listik import routes as routes_mod
from listik import routes_store
from listik import skills as skills_mod
from tests.helpers import TempDbTestCase

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
ROUTES_JSON = REPO_DIR / "routes.json"

from tests.test_routes_config import EXPECTED_KEYS
#: Маршруты пресетов pipeline-cc (бывшего claude-codex) — запись 3 `ROUTE_ADDITIONS`.
CC_ADDITION_KEYS = [k for k in EXPECTED_KEYS if k.startswith("cc-")]


def pipeline_record() -> dict:
    return {
        "key": "demo-pipeline",
        "kind": "pipeline",
        "title": "Демо",
        "hint": "подсказка",
        "visible": True,
        "roles": {"impl": {"provider": "claude", "label": "Opus", "title": "Opus · medium"}},
    }


def swarm_record() -> dict:
    return {"key": "dsh", "kind": "swarm", "title": "dsh", "hint": "", "visible": True,
            "roles": {"impl": {"harness": "dsh"}}}


def document(*records) -> dict:
    return {"version": 1, "routes": list(records) if records else [pipeline_record()]}


class RoutesDbTestCase(TempDbTestCase):
    """Общая обвязка: тихий stderr и подмена путей ввоза."""

    def import_sample(self, *, replace=False) -> dict:
        with contextlib.redirect_stderr(io.StringIO()):
            return routes_store.import_file(self.conn, ROUTES_JSON, replace=replace)

    def patch_paths(self, *, source=None):
        """Подменить путь ввоза на временный, чтобы не смотреть в поставку."""
        source = source if source is not None else self.tmp_path / "нет-образца.json"
        return mock.patch.object(routes_mod, "SOURCE_PATH", source)

    def write_routes(self, records, name: str = "routes.json"):
        path = self.tmp_path / name
        path.write_text(json.dumps({"version": 1, "routes": list(records)},
                                   ensure_ascii=False), encoding="utf-8")
        return path


class ImportSampleTests(RoutesDbTestCase):
    """Пункты чек-листа про ввоз образца `routes.json`."""

    def test_sample_imports_in_file_order(self) -> None:
        report = self.import_sample()
        self.assertEqual(report, {"imported": len(EXPECTED_KEYS), "skipped": False,
                                  "source": str(ROUTES_JSON), "replaced": False})
        records = routes_store.list_routes(self.conn)
        self.assertEqual([r["key"] for r in records], EXPECTED_KEYS)
        self.assertEqual([r["position"] for r in records], list(range(len(EXPECTED_KEYS))))

    def test_high_pipeline_roles_keep_providers(self) -> None:
        self.import_sample()
        record = routes_store.get_route(self.conn, "full-high")
        self.assertEqual(list(record["roles"]), ["spec", "critic", "impl", "judge"])
        for cell in record["roles"].values():
            self.assertTrue(cell["provider"])
            self.assertTrue(cell["label"])
            self.assertTrue(cell["title"])

    def test_direct_kind_is_rejected(self) -> None:
        """Вида `direct` нет (listik-ar8v): ни `_prepare`, ни `create_route` его не берут."""
        with self.assertRaises(ValueError) as ctx:
            routes_store._prepare(self.conn, {"key": "grok", "kind": "direct", "title": "grok",
                                              "harness": "grok", "command": ["grok"]})
        self.assertIn("kind", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            routes_store.create_route(self.conn, key="grok", kind="direct", title="grok",
                                      command=["grok"])
        self.assertIn("kind", str(ctx.exception))
        with self.assertRaises(TypeError):  # параметра `harness` больше нет
            routes_store.create_route(self.conn, key="grok", kind="swarm", title="grok",
                                      harness="grok")
        self.assertEqual(routes_store.count(self.conn), 0)

    def test_types_are_python_not_json(self) -> None:
        self.import_sample()
        for record in routes_store.list_routes(self.conn):
            self.assertIsInstance(record["visible"], bool)
            self.assertIsInstance(record["position"], int)
            self.assertIsInstance(record["roles"], dict)
            self.assertNotIn("harness", record)
            self.assertTrue(record["command"] is None or isinstance(record["command"], list))

    def test_cross_pipeline_roles_and_route_placeholder(self) -> None:
        """full-cross: четыре роли, команда claude, свой скил `/pipeline-full:cross` и ключ в {route}."""
        self.import_sample()
        record = routes_store.get_route(self.conn, "full-cross")
        self.assertEqual(record["kind"], "pipeline")
        self.assertEqual(list(record["roles"]), ["spec", "critic", "impl", "judge"])
        self.assertEqual([cell["label"] for cell in record["roles"].values()],
                         ["max", "S+DS", "GLM", "xhigh"])
        self.assertEqual(record["command"][0], "claude")
        values = {name: name for name in routes_mod.PLACEHOLDERS}
        values["route"] = "full-cross"
        values["plugin"] = record["plugin"]
        values["skill"] = skills_mod.skill_of(record["plugin"], "full-cross")
        substituted = [launcher._substitute(element, values)
                       for element in record["command"]]
        self.assertTrue(any("/pipeline-full:cross" in line and "маршрут full-cross" in line
                            for line in substituted))


class ShippedAdditionsTests(RoutesDbTestCase):
    """Маршрут, добавленный в поставку позже, доезжает в уже наполненную базу один раз."""

    def test_existing_install_gets_addition_once(self) -> None:
        self.import_sample()
        routes_store.delete_route(self.conn, "full-cross")
        with self.patch_paths(source=ROUTES_JSON), \
                mock.patch.object(routes_store, "ROUTE_ADDITIONS", [(2, ("full-cross",))]):
            self.assertEqual(routes_store.add_shipped(self.conn), ["full-cross"])
            positions = [r["position"] for r in routes_store.list_routes(self.conn)]
            self.assertEqual(routes_store.get_route(self.conn, "full-cross")["position"],
                             max(positions))
            routes_store.delete_route(self.conn, "full-cross")
            self.assertEqual(routes_store.add_shipped(self.conn), [])
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM routes WHERE key = 'full-cross'").fetchone())

    def test_install_after_addition_2_gets_claude_codex_routes_once(self) -> None:
        self.import_sample()
        cc_keys = CC_ADDITION_KEYS
        for key in cc_keys:
            routes_store.delete_route(self.conn, key)
        self.conn.execute("INSERT INTO meta(key, value) VALUES('routes_additions', '2') "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value")
        self.conn.commit()
        with self.patch_paths(source=ROUTES_JSON):
            self.assertEqual(routes_store.add_shipped(self.conn), cc_keys)
            records = routes_store.list_routes(self.conn)
            self.assertEqual([r["key"] for r in records][-len(cc_keys):], cc_keys)
            self.assertEqual([r["position"] for r in records],
                             sorted(r["position"] for r in records))
            self.assertEqual(routes_store.add_shipped(self.conn), [])

    def test_addition_3_matches_claude_codex_keys_of_routes_json(self) -> None:
        shipped = [r["key"] for r in json.loads(ROUTES_JSON.read_text(encoding="utf-8"))["routes"]
                   if r["key"].startswith("cc-")]
        addition = dict(routes_store.ROUTE_ADDITIONS)[3]
        self.assertEqual(set(addition), set(shipped))
        self.assertEqual(len(addition), len(shipped))

    def test_install_after_addition_1_gets_renamed_keys(self) -> None:
        """listik-d9rj: записи 2 и 3 — с новыми ключами; база после записи 1 их получает из файла."""
        self.assertEqual(dict(routes_store.ROUTE_ADDITIONS)[2], ())
        self.assertEqual(list(dict(routes_store.ROUTE_ADDITIONS)[3]), CC_ADDITION_KEYS)
        self.import_sample()
        expected = CC_ADDITION_KEYS
        for key in expected:
            routes_store.delete_route(self.conn, key)
        self.conn.execute("INSERT INTO meta(key, value) VALUES('routes_additions', '1') "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value")
        self.conn.commit()
        with self.patch_paths(source=ROUTES_JSON):
            self.assertEqual(routes_store.add_shipped(self.conn), expected)
            self.assertEqual(sorted(r["key"] for r in routes_store.list_routes(self.conn)),
                             sorted(EXPECTED_KEYS))
            self.assertEqual(routes_store.add_shipped(self.conn), [])

    def test_additions_numbering_and_claude_presets_last(self) -> None:
        """listik-d9rj, порция f: номера строго растут, последняя запись — пресеты pipeline-claude."""
        numbers = [number for number, _ in routes_store.ROUTE_ADDITIONS]
        self.assertEqual(numbers, sorted(set(numbers)), "номера не растут строго или повторяются")
        self.assertEqual(routes_store.ROUTE_ADDITIONS[-1][0], max(numbers))
        self.assertEqual(numbers[-1], numbers[-2] + 1)
        self.assertEqual(routes_store.ROUTE_ADDITIONS[-1][1], ("claude-xhigh", "claude-high"))

    def test_install_before_claude_presets_gets_them_last_once(self) -> None:
        """База до записи с пресетами pipeline-claude получает их в конец, удалённый не возвращается."""
        previous = str(routes_store.ROUTE_ADDITIONS[-2][0])
        expected = ["claude-xhigh", "claude-high"]
        self.import_sample()
        for key in expected:
            routes_store.delete_route(self.conn, key)
        self.conn.execute("INSERT INTO meta(key, value) VALUES('routes_additions', ?) "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (previous,))
        self.conn.commit()
        with self.patch_paths(source=ROUTES_JSON), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(routes_store.ensure_imported(self.conn)["added"], expected)
            records = sorted(routes_store.list_routes(self.conn), key=lambda r: r["position"])
            self.assertEqual([r["key"] for r in records][-2:], expected)
            routes_store.delete_route(self.conn, "claude-xhigh")
            self.assertEqual(routes_store.ensure_imported(self.conn)["added"], [])
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM routes WHERE key = 'claude-xhigh'").fetchone())

    def test_fresh_import_only_marks(self) -> None:
        with self.patch_paths(source=ROUTES_JSON), contextlib.redirect_stderr(io.StringIO()):
            report = routes_store.ensure_imported(self.conn)
        self.assertEqual(report["added"], [])
        self.assertEqual(routes_store.count(self.conn), len(EXPECTED_KEYS))


class ReimportTests(RoutesDbTestCase):
    """Идемпотентность ввоза и `replace`."""

    def test_second_import_without_replace_is_skipped(self) -> None:
        self.import_sample()
        routes_store.update_route(self.conn, "full-cross", title="Правленый")
        report = self.import_sample()
        self.assertTrue(report["skipped"])
        self.assertEqual(report["imported"], 0)
        self.assertFalse(report["replaced"])
        self.assertEqual(routes_store.get_route(self.conn, "full-cross")["title"], "Правленый")

    def test_import_with_replace_rewrites(self) -> None:
        self.import_sample()
        shipped = routes_store.get_route(self.conn, "full-cross")["title"]
        routes_store.update_route(self.conn, "full-cross", title="Правленый", visible=False)
        report = self.import_sample(replace=True)
        self.assertFalse(report["skipped"])
        self.assertTrue(report["replaced"])
        self.assertEqual(report["imported"], len(EXPECTED_KEYS))
        record = routes_store.get_route(self.conn, "full-cross")
        self.assertEqual(record["title"], shipped)
        self.assertTrue(record["visible"])

    def test_broken_file_keeps_db_and_raises(self) -> None:
        self.import_sample()
        bad = self.tmp_path / "bad.json"
        bad.write_text(json.dumps({"version": 2, "routes": []}), encoding="utf-8")
        before = routes_store.list_routes(self.conn)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(routes_mod.RoutesError):
                routes_store.import_file(self.conn, bad)
        self.assertEqual(routes_store.list_routes(self.conn), before)
        self.assertEqual(routes_store.count(self.conn), len(EXPECTED_KEYS))

    def test_ensure_imported_swallows_broken_file(self) -> None:
        bad = self.tmp_path / "broken.json"
        bad.write_text("{", encoding="utf-8")
        with self.patch_paths(source=bad), contextlib.redirect_stderr(io.StringIO()) as err:
            report = routes_store.ensure_imported(self.conn)
        self.assertIn("error", report)
        self.assertEqual(routes_store.count(self.conn), 0)
        self.assertIn("routes: ввоз не удался", err.getvalue())

    def test_default_source_is_sample(self) -> None:
        with self.patch_paths(source=ROUTES_JSON):
            report = routes_store.ensure_imported(self.conn)
        self.assertEqual(report["imported"], len(EXPECTED_KEYS))
        self.assertEqual(report["source"], str(ROUTES_JSON))

    def test_strip_field_is_not_stored(self) -> None:
        source = self.write_routes([{**pipeline_record(),
                                     "strip": {"glyph": "gear", "label": "x"}}])
        self.import_sample()  # прогреваем базу, дальше replace
        with contextlib.redirect_stderr(io.StringIO()):
            routes_store.import_file(self.conn, source, replace=True)
        record = routes_store.get_route(self.conn, "demo-pipeline")
        self.assertNotIn("strip", record)
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(routes)")}
        self.assertNotIn("strip", columns)


class ReimportCommandTests(RoutesDbTestCase):
    """`listik routes --reimport` (listik-ttjm): перезапись из файла поставки."""

    def test_reimport_rewrites_and_reports_orphans(self) -> None:
        from listik import store
        self.import_sample()
        shipped = routes_store.get_route(self.conn, "full-cross")["title"]
        routes_store.update_route(self.conn, "full-cross", title="Моя правка")
        task = store.create_task(self.conn, title="проба", project="listik")
        self.conn.execute("UPDATE tasks SET launch_route = 'gone-route' WHERE id = ?",
                          (task["id"],))
        self.conn.commit()
        report = routes_store.reimport(self.conn, ROUTES_JSON)
        self.assertTrue(report["replaced"])
        self.assertEqual(report["imported"], len(EXPECTED_KEYS))
        self.assertEqual(report["orphans"], {"gone-route": 1})
        self.assertEqual(routes_store.get_route(self.conn, "full-cross")["title"], shipped)
        row = self.conn.execute("SELECT launch_route FROM tasks WHERE id = ?",
                                (task["id"],)).fetchone()
        self.assertEqual(row[0], "gone-route", "задачу трогать нельзя")

    def test_cli_local_reimport(self) -> None:
        import os
        import subprocess
        import sys
        from tests.test_claim import LISTIK_BIN
        self.import_sample()
        shipped = routes_store.get_route(self.conn, "full-cross")["title"]
        routes_store.update_route(self.conn, "full-cross", title="Моя правка")
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}
        proc = subprocess.run([sys.executable, str(LISTIK_BIN), "--local", "routes",
                               "--reimport", "--json"], capture_output=True, text=True,
                              env=env, cwd=str(REPO_DIR))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        self.assertTrue(report["replaced"])
        self.assertEqual(report["imported"], len(EXPECTED_KEYS))
        self.assertEqual(report["orphans"], {})
        self.assertEqual(routes_store.get_route(self.conn, "full-cross")["title"], shipped)


class ReimportKeepsUserRoutesTests(RoutesDbTestCase):
    """`reimport` сохраняет маршруты не из файла, кроме конвейеров, и пишет бэкап
    (listik-zr05, порция g)."""

    SWARM_ROLES = {"impl": {"harness": "codex", "argv": ["codex", "exec", "{task_id}"]}}
    KEPT = ["chiki-pow", "my-solo", "my-roy"]
    BACKUP_RE = r"routes\.bak-\d{8}T\d{6}Z(-\d+)?\.json"

    def setUp(self) -> None:
        super().setUp()
        from listik import paths, store
        self.data_dir = self.tmp_path / "data"
        patcher = mock.patch.object(paths, "DATA_DIR", self.data_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.import_sample()
        routes_store.create_route(self.conn, key="chiki-pow", kind="swarm", title="Рой чики",
                                  roles=self.SWARM_ROLES)
        routes_store.upsert_route(self.conn, {**swarm_record(), "key": "my-solo",
                                              "title": "Мой dsh"})
        routes_store.upsert_route(self.conn, {**swarm_record(), "key": "my-roy",
                                              "roles": self.SWARM_ROLES})
        routes_store.upsert_route(self.conn, {**pipeline_record(), "key": "my-pipe"})
        routes_store.update_route(self.conn, "full-high", title="Моя правка")
        task = store.create_task(self.conn, title="рой", project="listik")
        self.conn.execute("UPDATE tasks SET launch_route = 'chiki-pow' WHERE id = ?",
                          (task["id"],))
        self.conn.commit()
        self.before = {r["key"]: r for r in routes_store.list_routes(self.conn)}

    def reimport(self, path=ROUTES_JSON) -> dict:
        with contextlib.redirect_stderr(io.StringIO()):
            return routes_store.reimport(self.conn, path)

    def backups(self) -> list:
        return sorted(self.data_dir.glob("routes.bak-*"))

    @staticmethod
    def without_position(record: dict) -> dict:
        return {k: v for k, v in record.items() if k != "position"}

    def test_keeps_user_routes_and_drops_skill_pipelines(self) -> None:
        report = self.reimport()
        for key in self.KEPT:
            self.assertEqual(self.without_position(routes_store.get_route(self.conn, key)),
                             self.without_position(self.before[key]), key)
        with self.assertRaises(errors.NotFound):
            routes_store.get_route(self.conn, "my-pipe")
        self.assertEqual(routes_store.get_route(self.conn, "full-high")["title"],
                         routes_mod.load(ROUTES_JSON).by_key["full-high"]["title"])
        self.assertEqual(sorted(report["kept"]), sorted(self.KEPT))
        self.assertEqual(report["removed"], ["my-pipe"])
        self.assertEqual(report["orphans"], {})
        self.assertTrue(report["replaced"])
        self.assertFalse(report["skipped"])
        self.assertEqual(report["imported"], len(EXPECTED_KEYS))
        backup = pathlib.Path(report["backup"])
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.parent, self.data_dir)
        self.assertRegex(backup.name, "^" + self.BACKUP_RE + "$")

    def test_backup_of_pipeline_without_roles_reads_back(self) -> None:
        """listik-1lbn: конвейер без раскладки не ломает бэкап и `reimport` из него."""
        routes_store.create_route(self.conn, key="bare-pipeline", kind="pipeline", title="Голый")
        path = routes_store._write_backup(self.conn)
        with contextlib.redirect_stderr(io.StringIO()):
            state = routes_mod.load(path)
        self.assertTrue(state.ok, state.error)
        self.reimport(path)
        record = routes_store.get_route(self.conn, "bare-pipeline")
        self.assertEqual(record["kind"], "pipeline")
        self.assertEqual(record["roles"], {})

    def test_backup_is_loadable_and_complete(self) -> None:
        report = self.reimport()
        with contextlib.redirect_stderr(io.StringIO()):
            state = routes_mod.load(report["backup"])
        self.assertTrue(state.ok, state.error)
        for key in ["full-high", "my-pipe", *self.KEPT]:
            self.assertIn(key, state.by_key)
        self.assertEqual(state.by_key["full-high"]["title"], "Моя правка")

    def test_backup_records_have_no_harness(self) -> None:
        """Бэкап таблицы без полей `harness` (listik-ar8v) и `driver` (listik-ujra)."""
        report = self.reimport()
        saved = json.loads(pathlib.Path(report["backup"]).read_text(encoding="utf-8"))["routes"]
        self.assertEqual({r["key"] for r in saved}, set(self.before))
        for record in saved:
            self.assertNotIn("harness", record, record["key"])
            self.assertNotIn("driver", record, record["key"])
        self.assertEqual(next(r for r in saved if r["key"] == "my-solo")["roles"],
                         {"impl": {"harness": "dsh"}})

    def test_swarm_cell_without_argv_survives_backup(self) -> None:
        # Ячейка роя без своего argv (команда — argv харнесса по умолчанию) в файле
        # допустима: каталога харнессов у валидатора файла нет.
        routes_store.create_route(self.conn, key="roy-default", kind="swarm", title="Рой",
                                  roles={"impl": {"harness": "codex"}})
        report = self.reimport()
        with contextlib.redirect_stderr(io.StringIO()):
            state = routes_mod.load(report["backup"])
        self.assertTrue(state.ok, state.error)
        self.assertEqual(state.by_key["roy-default"]["roles"], {"impl": {"harness": "codex"}})
        routes_store.delete_route(self.conn, "roy-default")
        self.reimport(report["backup"])
        self.assertEqual(routes_store.get_route(self.conn, "roy-default")["roles"],
                         {"impl": {"harness": "codex"}})

    def test_file_validator_swarm_cells(self) -> None:
        base = {"key": "p", "title": "t", "visible": True}
        cell = {"impl": {"harness": "codex"}}
        self.assertEqual(routes_mod.validate(document({**base, "kind": "swarm", "roles": cell}))
                         [0]["roles"], cell)
        # Форма ячеек — только по `kind`: у конвейера роевые ячейки не проходят.
        with self.assertRaises(routes_mod.RoutesError):
            routes_mod.validate(document({**base, "kind": "pipeline", "roles": cell}))
        # С каталогом без argv по умолчанию команда по-прежнему обязательна.
        with self.assertRaises(routes_mod.RoutesError):
            routes_mod.validate_swarm_roles(cell, "roles", {"codex": {"argv": None}})

    def test_restore_from_backup(self) -> None:
        report = self.reimport()
        self.reimport(report["backup"])
        self.assertEqual(routes_store.get_route(self.conn, "my-pipe")["title"], "Демо")
        self.assertEqual(routes_store.get_route(self.conn, "full-high")["title"],
                         "Моя правка")

    def test_positions_file_first_then_kept_in_order(self) -> None:
        old_order = [k for k in self.before if k in self.KEPT]
        self.reimport()
        file_keys = [r["key"] for r in routes_mod.load(ROUTES_JSON).routes]
        rows = routes_store.list_routes(self.conn)
        self.assertEqual([r["key"] for r in rows], file_keys + old_order)
        self.assertEqual([r["position"] for r in rows], list(range(len(rows))))

    def test_broken_file_changes_nothing(self) -> None:
        broken = self.tmp_path / "broken.json"
        broken.write_text("{не json", encoding="utf-8")
        with self.assertRaises(routes_mod.RoutesError):
            self.reimport(broken)
        self.assertEqual({r["key"]: r for r in routes_store.list_routes(self.conn)},
                         self.before)
        self.assertEqual(self.backups(), [])

    def test_same_second_backups_get_suffix(self) -> None:
        with mock.patch.object(routes_store, "_backup_stamp", return_value="20260101T000000Z"):
            first = self.reimport()["backup"]
            first_text = pathlib.Path(first).read_text(encoding="utf-8")
            second = self.reimport()["backup"]
        self.assertTrue(first.endswith("routes.bak-20260101T000000Z.json"))
        self.assertTrue(second.endswith("routes.bak-20260101T000000Z-2.json"))
        self.assertEqual(pathlib.Path(first).read_text(encoding="utf-8"), first_text)

    def test_error_in_transaction_rolls_back_but_backup_stays(self) -> None:
        with mock.patch.object(routes_store, "_upsert_raw", side_effect=RuntimeError("сбой")):
            with self.assertRaises(RuntimeError):
                self.reimport()
        self.assertEqual({r["key"]: r for r in routes_store.list_routes(self.conn)},
                         self.before)
        [backup] = self.backups()
        saved = json.loads(backup.read_text(encoding="utf-8"))["routes"]
        self.assertEqual([r["key"] for r in saved], list(self.before))

    def run_cli(self, *argv):
        import os
        import subprocess
        import sys
        from tests.test_claim import LISTIK_BIN
        env = {**os.environ, "LISTIK_DB": str(self.db_path), "LISTIK_HOME": str(self.data_dir),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", "routes", *argv],
                              capture_output=True, text=True, env=env, cwd=str(REPO_DIR))

    def test_cli_from_backup_json(self) -> None:
        backup = self.reimport()["backup"]
        proc = self.run_cli("--reimport", "--from", backup, "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["source"], backup)
        for name in ("backup", "kept", "removed"):
            self.assertIn(name, report)
        self.assertEqual(routes_store.get_route(self.conn, "my-pipe")["title"], "Демо")

    def test_cli_from_without_reimport_is_bad_argument(self) -> None:
        proc = self.run_cli("--from", str(ROUTES_JSON))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_cli_human_output(self) -> None:
        proc = self.run_cli("--reimport")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("сохранены:", proc.stdout)
        self.assertIn("удалены: my-pipe", proc.stdout)
        self.assertIn("копия таблицы:", proc.stdout)


class PluginStoreTests(RoutesDbTestCase):
    """Поле `plugin` в хранилище и бэкапе (listik-d9rj, порция d)."""

    def setUp(self) -> None:
        super().setUp()
        from listik import paths
        patcher = mock.patch.object(paths, "DATA_DIR", self.tmp_path / "data")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sample_rows_carry_plugin(self) -> None:
        self.import_sample()
        record = routes_store.get_route(self.conn, "full-high")
        self.assertEqual(record["plugin"], "pipeline-full")
        row = self.conn.execute("SELECT plugin FROM routes WHERE key = 'cc-low'").fetchone()
        self.assertEqual(row[0], "pipeline-cc")
        self.assertIn("/{plugin}:{skill}", record["command"][-1])
        values = {name: name for name in routes_mod.PLACEHOLDERS}
        values.update(plugin=record["plugin"],
                      skill=skills_mod.skill_of(record["plugin"], "full-high"))
        self.assertIn("/pipeline-full:high", launcher._substitute(record["command"][-1], values))

    def test_prepare_checks_plugin(self) -> None:
        cases = (({"key": "full-high", "plugin": "pipeline-cc"}, "key: у плагина pipeline-cc"),
                 ({"key": "full-high", "plugin": "pipeline-foo"}, "plugin: допустимы"),
                 ({**swarm_record(), "plugin": "pipeline-full"}, "plugin: поле только у kind=pipeline"))
        for over, text in cases:
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as ctx:
                    routes_store.upsert_route(self.conn, {**pipeline_record(), **over})
                self.assertIn(text, str(ctx.exception))

    def test_create_route_with_plugin(self) -> None:
        record = routes_store.create_route(self.conn, key="full-high", kind="pipeline",
                                           title="t", plugin="pipeline-full")
        self.assertEqual(record["plugin"], "pipeline-full")
        bare = routes_store.create_route(self.conn, key="bare", kind="pipeline", title="t")
        self.assertIsNone(bare["plugin"])

    def test_update_route_does_not_change_plugin(self) -> None:
        self.import_sample()
        self.assertNotIn("plugin", routes_store.UPDATE_FIELDS)
        with self.assertRaises(ValueError):
            routes_store.update_route(self.conn, "full-high", plugin="pipeline-cc")
        self.assertEqual(routes_store.get_route(self.conn, "full-high")["plugin"], "pipeline-full")

    def test_backup_has_plugin_and_reads_back(self) -> None:
        self.import_sample()
        routes_store.create_route(self.conn, key="roy", kind="swarm", title="Рой",
                                  roles={"impl": {"harness": "dsh"}})
        routes_store.create_route(self.conn, key="bare", kind="pipeline", title="Голый")
        before = {r["key"]: r["plugin"] for r in routes_store.list_routes(self.conn)}
        with contextlib.redirect_stderr(io.StringIO()):
            report = routes_store.reimport(self.conn, ROUTES_JSON)
        saved = json.loads(pathlib.Path(report["backup"]).read_text(encoding="utf-8"))["routes"]
        for record in saved:
            self.assertIn("plugin", record, record["key"])
        self.assertEqual({r["key"]: r["plugin"] for r in saved}, before)
        self.assertIsNone(next(r for r in saved if r["key"] == "roy")["plugin"])
        self.conn.execute("UPDATE routes SET plugin = NULL")
        self.conn.commit()
        with contextlib.redirect_stderr(io.StringIO()):
            routes_store.reimport(self.conn, report["backup"])
        self.assertEqual({r["key"]: r["plugin"] for r in routes_store.list_routes(self.conn)},
                         before)


class FieldRulesTests(RoutesDbTestCase):
    """Правила полей — по одному случаю на нарушение."""

    def setUp(self) -> None:
        super().setUp()
        self.import_sample()

    def bad(self, **fields) -> str:
        with self.assertRaises(ValueError) as ctx:
            routes_store.update_route(self.conn, "full-high", **fields)
        return str(ctx.exception)

    def test_title_empty(self) -> None:
        self.assertIn("title", self.bad(title=""))

    def test_title_spaces(self) -> None:
        self.assertIn("title", self.bad(title="   "))

    def test_hint_none(self) -> None:
        self.assertIn("hint", self.bad(hint=None))

    def test_icon_unknown(self) -> None:
        self.assertIn("icon", self.bad(icon="turbo"))

    def test_visible_int(self) -> None:
        self.assertIn("visible", self.bad(visible=1))

    def test_command_string(self) -> None:
        self.assertIn("command", self.bad(command="строка"))

    def test_command_empty(self) -> None:
        self.assertIn("command", self.bad(command=[]))

    def test_command_empty_element(self) -> None:
        self.assertIn("command", self.bad(command=["", "x"]))

    def test_hint_empty_is_allowed(self) -> None:
        self.assertEqual(routes_store.update_route(self.conn, "full-high", hint="")["hint"], "")

    def test_icon_none_is_allowed(self) -> None:
        self.assertIsNone(routes_store.update_route(self.conn, "full-high", icon=None)["icon"])

    def test_visible_false_is_allowed(self) -> None:
        self.assertFalse(routes_store.update_route(self.conn, "full-high",
                                                   visible=False)["visible"])


class UpdateRouteTests(RoutesDbTestCase):
    """`update_route`: допустимые и отвергаемые поля."""

    def setUp(self) -> None:
        super().setUp()
        self.import_sample()

    def test_rejected_fields_name_the_field(self) -> None:
        # `key` сюда не подставить: оно уже занято позиционным параметром
        # сигнатуры, Python отвергнет вызов раньше проверки.
        for name, value in (("roles", {}), ("kind", "swarm"),
                            ("harness", "dsh"), ("position", 0),
                            ("command", ["echo", "{task_id}"])):
            with self.subTest(name=name):
                with self.assertRaises(ValueError) as ctx:
                    routes_store.update_route(self.conn, "full-high", **{name: value})
                self.assertIn(name, str(ctx.exception))

    def test_command_on_pipeline_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            routes_store.update_route(self.conn, "full-high", command=["echo", "{task_id}"])
        self.assertIn("command", str(ctx.exception))

    def test_command_is_not_updatable(self) -> None:
        """Точечной правки `command` нет ни у какого вида (listik-ar8v)."""
        self.assertNotIn("command", routes_store.UPDATE_FIELDS)
        routes_store.create_route(self.conn, key="solo", kind="swarm", title="Соло",
                                  roles={"impl": {"harness": "dsh"}})
        with self.assertRaises(ValueError) as ctx:
            routes_store.update_route(self.conn, "solo", command=["echo", "{task_id}"])
        self.assertIn("нельзя менять", str(ctx.exception))

    def test_partial_update_keeps_other_fields(self) -> None:
        before = routes_store.get_route(self.conn, "full-cross")
        after = routes_store.update_route(self.conn, "full-cross", title="Новый")
        self.assertEqual(after["title"], "Новый")
        self.assertEqual(after["command"], before["command"])
        self.assertEqual(after["roles"], before["roles"])


class CrudTests(RoutesDbTestCase):
    """`create_route`, `get_route`, `count`, `delete_route`, `reorder`."""

    def setUp(self) -> None:
        super().setUp()
        self.import_sample()

    def test_create_uses_max_position_plus_one(self) -> None:
        record = routes_store.create_route(self.conn, key="zzz-solo", kind="swarm",
                                           title="Zzz", roles={"impl": {"harness": "dsh"}})
        self.assertEqual(record["position"], len(EXPECTED_KEYS))
        self.assertEqual(routes_store.list_routes(self.conn)[-1]["key"], "zzz-solo")

    def test_create_duplicate_key(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            routes_store.create_route(self.conn, key="full-cross", kind="swarm", title="Dup",
                                      roles={"impl": {"harness": "dsh"}})
        self.assertIn("full-cross", str(ctx.exception))

    def test_get_unknown_raises_not_found(self) -> None:
        with self.assertRaises(errors.NotFound):
            routes_store.get_route(self.conn, "нет-такого")

    def test_count_matches_list(self) -> None:
        self.assertEqual(routes_store.count(self.conn),
                         len(routes_store.list_routes(self.conn)))

    def test_delete_counts_tasks_but_keeps_them(self) -> None:
        self.conn.execute("INSERT INTO tasks(id, launch_route) VALUES('t1', 'full-cross')")
        self.conn.execute("INSERT INTO tasks(id, launch_route) VALUES('t2', 'full-cross')")
        self.conn.execute("INSERT INTO tasks(id, launch_route) VALUES('t3', 'full-nano')")
        self.conn.commit()
        removed = routes_store.delete_route(self.conn, "full-cross")
        self.assertEqual(removed, 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 3)
        self.assertNotIn("full-cross", [r["key"] for r in routes_store.list_routes(self.conn)])
        with self.assertRaises(errors.NotFound):
            routes_store.get_route(self.conn, "full-cross")

    def test_reorder_full(self) -> None:
        reordered = routes_store.reorder(self.conn, list(reversed(EXPECTED_KEYS)))
        self.assertEqual([r["key"] for r in reordered], list(reversed(EXPECTED_KEYS)))
        self.assertEqual([r["position"] for r in reordered], list(range(len(EXPECTED_KEYS))))
        self.assertEqual([r["key"] for r in routes_store.list_routes(self.conn)],
                         list(reversed(EXPECTED_KEYS)))

    def test_reorder_subset_pushes_rest_to_end(self) -> None:
        rest = [k for k in EXPECTED_KEYS if k not in ("full-cross", "full-nano")]
        reordered = routes_store.reorder(self.conn, ["full-cross", "full-nano"])
        self.assertEqual([r["key"] for r in reordered], ["full-cross", "full-nano", *rest])
        positions = [r["position"] for r in reordered]
        self.assertEqual(positions, list(range(len(EXPECTED_KEYS))))

    def test_reorder_unknown_key(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            routes_store.reorder(self.conn, ["нет-такого"])
        self.assertIn("нет-такого", str(ctx.exception))


class BrokenJsonColumnsTests(RoutesDbTestCase):
    """Битый JSON в колонках — пустое значение, а не исключение."""

    def test_list_routes_survives_broken_columns(self) -> None:
        self.import_sample()
        self.conn.execute("UPDATE routes SET roles = '{', command = '[' WHERE key = 'full-high'")
        self.conn.commit()
        record = routes_store.get_route(self.conn, "full-high")
        self.assertEqual(record["roles"], {})
        self.assertIsNone(record["command"])


class SchemaUpgradeTests(unittest.TestCase):
    """`db.init` создаёт `routes` на старой базе, не портя задачи и проекты."""

    def test_init_creates_routes_on_old_db(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = pathlib.Path(tmp.name) / "old.db"
        conn = db_mod.init(path)
        conn.execute("INSERT INTO projects(slug, title) VALUES('p1', 'Старый')")
        conn.execute("INSERT INTO tasks(id, project, title) VALUES('t1', 'p1', 'Старая')")
        conn.execute("DROP TABLE routes")
        conn.execute("UPDATE meta SET value = '8' WHERE key = 'schema_version'")
        conn.commit()
        conn.close()

        conn = db_mod.init(path)
        try:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertIn("routes", tables)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM routes").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 1)
            version = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
            self.assertEqual(version, "16")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()

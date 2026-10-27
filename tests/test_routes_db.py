"""Тесты таблицы `routes` и ввоза `routes.json` формата v2 (шаг 09, порция b; listik-906l).

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

from listik import assistant
from listik import db as db_mod
from listik import errors
from listik import harnesses_store
from listik import launcher
from listik import routes as routes_mod
from listik import routes_store
from listik import skills as skills_mod
from tests.helpers import TempDbTestCase

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
ROUTES_JSON = REPO_DIR / "routes.json"

from tests.test_routes_config import EXPECTED_KEYS

#: Ячейка конвейера v1 — в живой базе до `--reimport`, слой базы её больше не принимает.
OLD_CELL = {"provider": "claude", "label": "x", "title": "y"}
DEVIN = {"harness": "devin", "model": "swe-2", "effort": "max"}
GROK = {"harness": "grok", "model": "grok-4.7", "effort": "high"}


def lp_record(skill: str = "high", **over) -> dict:
    """Запись файла v2."""
    record = {"key": f"lp-{skill}", "plugin": "listik-pipeline", "skill": skill,
              "roles": {"implementer": [dict(DEVIN)]}}
    record.update(over)
    return record


def my_flow(**over) -> dict:
    """Свой конвейер в форме слоя базы: без плагина, со своей командой."""
    record = {"key": "my-flow", "kind": "pipeline", "title": "x", "visible": True,
              "roles": {}, "command": ["true"]}
    record.update(over)
    return record


def swarm_record() -> dict:
    return {"key": "dsh", "kind": "swarm", "title": "dsh", "hint": "", "visible": True,
            "roles": {"impl": {"harness": "dsh"}}}


def insert_old_row(conn, key: str = "full-high", plugin: str = "pipeline-full") -> None:
    """Строка конвейера со старым плагином и ячейкой v1 — как в живой базе до `--reimport`."""
    conn.execute("INSERT INTO routes(key, kind, plugin, title, hint, icon, visible, position, "
                 "command, roles) VALUES(?, 'pipeline', ?, 'Старый', '', 'high', 1, 100, NULL, ?)",
                 (key, plugin, json.dumps({"spec": OLD_CELL})))
    conn.commit()


class RoutesDbTestCase(TempDbTestCase):
    """Общая обвязка: тихий stderr и подмена путей ввоза."""

    def import_sample(self, *, replace=False) -> dict:
        with contextlib.redirect_stderr(io.StringIO()):
            return routes_store.import_file(self.conn, ROUTES_JSON, replace=replace)

    def patch_paths(self, *, source=None):
        """Подменить путь ввоза на временный, чтобы не смотреть в поставку."""
        source = source if source is not None else self.tmp_path / "нет-образца.json"
        return mock.patch.object(routes_mod, "SOURCE_PATH", source)

    def write_routes(self, records, name: str = "routes.json", version: int = 2):
        path = self.tmp_path / name
        path.write_text(json.dumps({"version": version, "routes": list(records)},
                                   ensure_ascii=False), encoding="utf-8")
        return path


class ImportSampleTests(RoutesDbTestCase):
    """Ввоз образца `routes.json` (пункт 14 чек-листа)."""

    def test_sample_imports_in_file_order(self) -> None:
        report = self.import_sample()
        self.assertEqual(report, {"imported": len(EXPECTED_KEYS), "skipped": False,
                                  "source": str(ROUTES_JSON), "replaced": False})
        records = routes_store.list_routes(self.conn)
        self.assertEqual([r["key"] for r in records], EXPECTED_KEYS)
        self.assertEqual([r["position"] for r in records], list(range(len(EXPECTED_KEYS))))

    def test_sample_records_have_skill_and_v2_roles(self) -> None:
        self.import_sample()
        raw = {r["key"]: r for r in json.loads(ROUTES_JSON.read_text(encoding="utf-8"))["routes"]}
        for record in routes_store.list_routes(self.conn):
            with self.subTest(key=record["key"]):
                self.assertEqual(record["skill"], raw[record["key"]]["skill"])
                self.assertEqual(record["plugin"], "listik-pipeline")
                self.assertEqual(record["roles"], raw[record["key"]]["roles"])
                self.assertIs(record["visible"], True)
                self.assertIsNone(record["command"])
        self.assertEqual(routes_store.get_route(self.conn, "lp-high")["skill"], "high")
        self.assertEqual(list(routes_store.get_route(self.conn, "lp-xlow")["roles"]),
                         ["implementer", "judge"])

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

    def test_plugin_and_skill_substitute_into_skill_ref(self) -> None:
        self.import_sample()
        record = routes_store.get_route(self.conn, "lp-high")
        values = {"plugin": record["plugin"], "skill": record["skill"]}
        self.assertEqual(launcher._substitute("/{plugin}:{skill}", values),
                         skills_mod.skill_ref("lp-high"))


class ShippedAdditionsTests(RoutesDbTestCase):
    """Маршрут, добавленный в поставку позже, доезжает в уже наполненную базу один раз."""

    def test_existing_install_gets_addition_once(self) -> None:
        self.import_sample()
        routes_store.delete_route(self.conn, "lp-xlow")
        with self.patch_paths(source=ROUTES_JSON), \
                mock.patch.object(routes_store, "ROUTE_ADDITIONS", [(2, ("lp-xlow",))]):
            self.assertEqual(routes_store.add_shipped(self.conn), ["lp-xlow"])
            positions = [r["position"] for r in routes_store.list_routes(self.conn)]
            self.assertEqual(routes_store.get_route(self.conn, "lp-xlow")["position"],
                             max(positions))
            routes_store.delete_route(self.conn, "lp-xlow")
            self.assertEqual(routes_store.add_shipped(self.conn), [])
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM routes WHERE key = 'lp-xlow'").fetchone())

    def test_old_additions_not_in_file_add_nothing(self) -> None:
        """Ключи старых пресетов из `ROUTE_ADDITIONS` в файле v2 не поставляются."""
        self.import_sample()
        self.conn.execute("INSERT INTO meta(key, value) VALUES('routes_additions', '1') "
                          "ON CONFLICT(key) DO UPDATE SET value = excluded.value")
        self.conn.commit()
        with self.patch_paths(source=ROUTES_JSON):
            self.assertEqual(routes_store.add_shipped(self.conn), [])
        self.assertEqual([r["key"] for r in routes_store.list_routes(self.conn)], EXPECTED_KEYS)

    def test_additions_numbering_and_claude_presets_last(self) -> None:
        """listik-d9rj, порция f: номера строго растут, последняя запись — пресеты pipeline-claude."""
        numbers = [number for number, _ in routes_store.ROUTE_ADDITIONS]
        self.assertEqual(numbers, sorted(set(numbers)), "номера не растут строго или повторяются")
        self.assertEqual(routes_store.ROUTE_ADDITIONS[-1][0], max(numbers))
        self.assertEqual(numbers[-1], numbers[-2] + 1)
        self.assertEqual(routes_store.ROUTE_ADDITIONS[-1][1], ("claude-xhigh", "claude-high"))

    def test_fresh_import_only_marks(self) -> None:
        with self.patch_paths(source=ROUTES_JSON), contextlib.redirect_stderr(io.StringIO()):
            report = routes_store.ensure_imported(self.conn)
        self.assertEqual(report["added"], [])
        self.assertEqual(routes_store.count(self.conn), len(EXPECTED_KEYS))


class ReimportTests(RoutesDbTestCase):
    """Идемпотентность ввоза и `replace`."""

    def test_second_import_without_replace_is_skipped(self) -> None:
        self.import_sample()
        routes_store.update_route(self.conn, "lp-xlow", title="Правленый")
        report = self.import_sample()
        self.assertTrue(report["skipped"])
        self.assertEqual(report["imported"], 0)
        self.assertFalse(report["replaced"])
        self.assertEqual(routes_store.get_route(self.conn, "lp-xlow")["title"], "Правленый")

    def test_import_with_replace_rewrites(self) -> None:
        self.import_sample()
        shipped = routes_store.get_route(self.conn, "lp-xlow")["title"]
        routes_store.update_route(self.conn, "lp-xlow", title="Правленый", visible=False)
        report = self.import_sample(replace=True)
        self.assertFalse(report["skipped"])
        self.assertTrue(report["replaced"])
        self.assertEqual(report["imported"], len(EXPECTED_KEYS))
        record = routes_store.get_route(self.conn, "lp-xlow")
        self.assertEqual(record["title"], shipped)
        self.assertTrue(record["visible"])

    def test_v1_file_keeps_db_and_raises(self) -> None:
        self.import_sample()
        bad = self.write_routes([], name="bad.json", version=1)
        before = routes_store.list_routes(self.conn)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(routes_mod.RoutesError) as ctx:
                routes_store.import_file(self.conn, bad)
        self.assertEqual(str(ctx.exception), "version: должна быть ровно целая 2")
        self.assertEqual(routes_store.list_routes(self.conn), before)
        self.assertEqual(routes_store.count(self.conn), len(EXPECTED_KEYS))

    def test_channel_outside_catalog_is_rejected(self) -> None:
        """Пункт 15: канал `pi` вне каталога — отказ ввоза и `reimport`, таблица прежняя."""
        bad = self.write_routes([lp_record(roles={
            "critic": [{"harness": "pi", "channel": "nope", "effort": "high"}]})])
        text = "roles.critic[0].channel: у харнесса 'pi' нет канала 'nope'"
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(routes_mod.RoutesError) as ctx:
                routes_store.import_file(self.conn, bad)
        self.assertIn(text, str(ctx.exception))
        self.assertEqual(routes_store.list_routes(self.conn), [])
        self.import_sample()
        before = routes_store.list_routes(self.conn)
        from listik import paths
        with mock.patch.object(paths, "DATA_DIR", self.tmp_path / "data"), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(routes_mod.RoutesError) as ctx:
                routes_store.reimport(self.conn, bad)
        self.assertIn(text, str(ctx.exception))
        self.assertEqual(routes_store.list_routes(self.conn), before)

    def test_ensure_imported_swallows_broken_file(self) -> None:
        bad = self.tmp_path / "broken.json"
        bad.write_text("{", encoding="utf-8")
        with self.patch_paths(source=bad), contextlib.redirect_stderr(io.StringIO()) as err:
            report = routes_store.ensure_imported(self.conn)
        self.assertIn("error", report)
        self.assertEqual(routes_store.count(self.conn), 0)
        self.assertIn("routes: ввоз не удался", err.getvalue())

    def test_default_source_is_sample(self) -> None:
        """Пункт 18: свежая база — отчёт без `error`, `fill_members` больше нет."""
        with self.patch_paths(source=ROUTES_JSON):
            report = routes_store.ensure_imported(self.conn)
        self.assertNotIn("error", report)
        self.assertEqual(report["imported"], len(EXPECTED_KEYS))
        self.assertEqual(report["source"], str(ROUTES_JSON))
        self.assertFalse(hasattr(routes_store, "fill_members"))
        self.assertFalse(hasattr(routes_store, "MEMBER_HINTS"))


class ReimportCommandTests(RoutesDbTestCase):
    """`listik routes --reimport` (listik-ttjm): перезапись из файла поставки."""

    def setUp(self) -> None:
        super().setUp()
        from listik import paths
        patcher = mock.patch.object(paths, "DATA_DIR", self.tmp_path / "data")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reimport_rewrites_and_reports_orphans(self) -> None:
        from listik import store
        self.import_sample()
        shipped = routes_store.get_route(self.conn, "lp-xlow")["title"]
        routes_store.update_route(self.conn, "lp-xlow", title="Моя правка")
        task = store.create_task(self.conn, title="проба", project="listik")
        self.conn.execute("UPDATE tasks SET launch_route = 'gone-route' WHERE id = ?",
                          (task["id"],))
        self.conn.commit()
        report = routes_store.reimport(self.conn, ROUTES_JSON)
        self.assertTrue(report["replaced"])
        self.assertEqual(report["imported"], len(EXPECTED_KEYS))
        self.assertEqual(report["orphans"], {"gone-route": 1})
        self.assertEqual(routes_store.get_route(self.conn, "lp-xlow")["title"], shipped)
        row = self.conn.execute("SELECT launch_route FROM tasks WHERE id = ?",
                                (task["id"],)).fetchone()
        self.assertEqual(row[0], "gone-route", "задачу трогать нельзя")

    def test_cli_local_reimport(self) -> None:
        import os
        import subprocess
        import sys
        from tests.test_claim import LISTIK_BIN
        self.import_sample()
        shipped = routes_store.get_route(self.conn, "lp-xlow")["title"]
        routes_store.update_route(self.conn, "lp-xlow", title="Моя правка")
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_HOME": str(self.tmp_path / "data"),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}
        proc = subprocess.run([sys.executable, str(LISTIK_BIN), "--local", "routes",
                               "--reimport", "--json"], capture_output=True, text=True,
                              env=env, cwd=str(REPO_DIR))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        self.assertTrue(report["replaced"])
        self.assertEqual(report["imported"], len(EXPECTED_KEYS))
        self.assertEqual(report["orphans"], {})
        self.assertEqual(routes_store.get_route(self.conn, "lp-xlow")["title"], shipped)


class ReimportKeepsUserRoutesTests(RoutesDbTestCase):
    """`reimport` сохраняет рой, удаляет конвейеры не из файла и пишет бэкап v2
    (listik-zr05, порция g; listik-906l)."""

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
        routes_store.upsert_route(self.conn, my_flow())
        routes_store.update_route(self.conn, "lp-high", title="Моя правка")
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

    def test_keeps_user_routes_and_drops_other_pipelines(self) -> None:
        report = self.reimport()
        for key in self.KEPT:
            self.assertEqual(self.without_position(routes_store.get_route(self.conn, key)),
                             self.without_position(self.before[key]), key)
        with self.assertRaises(errors.NotFound):
            routes_store.get_route(self.conn, "my-flow")
        self.assertEqual(routes_store.get_route(self.conn, "lp-high")["title"],
                         routes_mod.load(ROUTES_JSON).by_key["lp-high"]["title"])
        self.assertEqual(sorted(report["kept"]), sorted(self.KEPT))
        self.assertEqual(report["removed"], ["my-flow"])
        self.assertEqual(report["orphans"], {})
        self.assertTrue(report["replaced"])
        self.assertFalse(report["skipped"])
        self.assertEqual(report["imported"], len(EXPECTED_KEYS))
        backup = pathlib.Path(report["backup"])
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.parent, self.data_dir)
        self.assertRegex(backup.name, "^" + self.BACKUP_RE + "$")

    def test_old_pipelines_removed_swarm_kept(self) -> None:
        """Пункт 20: рой остаётся, старый конвейер `full-high` удаляется."""
        self.conn.execute("DELETE FROM routes WHERE key IN ('chiki-pow', 'my-solo', 'my-roy', "
                          "'my-flow')")
        self.conn.commit()
        routes_store.create_route(self.conn, key="dsh", kind="swarm", title="dsh",
                                  roles={"impl": {"harness": "dsh"}})
        insert_old_row(self.conn)
        report = self.reimport()
        self.assertEqual(report["kept"], ["dsh"])
        self.assertIn("full-high", report["removed"])
        self.assertEqual([r["key"] for r in routes_store.list_routes(self.conn)],
                         [*EXPECTED_KEYS, "dsh"])

    def test_backup_of_pipeline_without_roles_reads_back(self) -> None:
        """listik-1lbn: конвейер `lp-*` без раскладки пишется `{}` и читается обратно."""
        routes_store.delete_route(self.conn, "lp-nano")
        routes_store.create_route(self.conn, key="lp-nano", kind="pipeline", title="Голый",
                                  plugin="listik-pipeline")
        path = routes_store._write_backup(self.conn)
        with contextlib.redirect_stderr(io.StringIO()):
            state = routes_mod.load(path)
        self.assertTrue(state.ok, state.error)
        self.assertEqual(state.by_key["lp-nano"]["roles"], {})
        self.reimport(path)
        record = routes_store.get_route(self.conn, "lp-nano")
        self.assertEqual(record["kind"], "pipeline")
        self.assertEqual(record["roles"], {})

    def test_backup_is_v2_with_only_lp_pipelines(self) -> None:
        report = self.reimport()
        doc = json.loads(pathlib.Path(report["backup"]).read_text(encoding="utf-8"))
        self.assertEqual(list(doc), ["version", "routes"])
        self.assertEqual(doc["version"], 2)
        self.assertEqual([r["key"] for r in doc["routes"]], EXPECTED_KEYS)
        for record in doc["routes"]:
            self.assertEqual(list(record), ["key", "plugin", "skill", "roles"], record["key"])
            self.assertEqual(record["roles"], self.before[record["key"]]["roles"])
        with contextlib.redirect_stderr(io.StringIO()):
            state = routes_mod.load(report["backup"])
        self.assertTrue(state.ok, state.error)

    def test_restore_from_backup(self) -> None:
        """Пункт 21: бэкап возвращает раскладки, остальные поля вычисляются заново."""
        insert_old_row(self.conn)
        routes_store.create_route(self.conn, key="dsh", kind="swarm", title="dsh",
                                  roles={"impl": {"harness": "dsh"}})
        roles = {"implementer": [dict(DEVIN)], "judge": [dict(GROK)]}
        routes_store.update_route(self.conn, "lp-low", roles=roles, visible=False, title="моё")
        report = self.reimport()
        doc = json.loads(pathlib.Path(report["backup"]).read_text(encoding="utf-8"))
        self.assertEqual(doc["version"], 2)
        keys = [r["key"] for r in doc["routes"]]
        for key in ("dsh", "my-flow", "full-high", *self.KEPT):
            self.assertNotIn(key, keys)
        for record in doc["routes"]:
            self.assertEqual(list(record), ["key", "plugin", "skill", "roles"])
        self.reimport(report["backup"])
        record = routes_store.get_route(self.conn, "lp-low")
        self.assertEqual(record["roles"], roles)
        self.assertIs(record["visible"], True)
        self.assertEqual(record["title"], "low")
        for key in self.KEPT:
            self.assertEqual(self.without_position(routes_store.get_route(self.conn, key)),
                             self.without_position(self.before[key]), key)
        self.assertEqual(routes_store.get_route(self.conn, "dsh")["kind"], "swarm")

    def test_file_validator_swarm_cells(self) -> None:
        """Ячейки роя в файле v2 не проходят; проверка роя живёт в `validate_swarm_roles`."""
        cell = {"impl": {"harness": "codex"}}
        with self.assertRaises(routes_mod.RoutesError) as ctx:
            routes_mod.validate({"version": 2, "routes": [lp_record(roles=cell)]})
        self.assertIn("routes[0].roles.impl: неизвестная роль", str(ctx.exception))
        self.assertEqual(routes_mod.validate_swarm_roles(cell, "roles"), cell)
        # С каталогом без argv по умолчанию команда по-прежнему обязательна.
        with self.assertRaises(routes_mod.RoutesError):
            routes_mod.validate_swarm_roles(cell, "roles", {"codex": {"argv": None}})

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

    def test_file_error_changes_nothing_and_writes_no_backup(self) -> None:
        """Пункт 19: запись со старым плагином — отказ до бэкапа и до удаления."""
        bad = self.write_routes([lp_record(plugin="pipeline-full")], name="bad.json")
        before = routes_store.list_routes(self.conn)
        with self.assertRaises(routes_mod.RoutesError) as ctx:
            self.reimport(bad)
        self.assertEqual(str(ctx.exception), "routes[0].plugin: допустимы listik-pipeline")
        self.assertEqual(routes_store.list_routes(self.conn), before)
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
        self.assertEqual([r["key"] for r in saved], EXPECTED_KEYS)

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
        routes_store.update_route(self.conn, "lp-low", roles={"judge": [dict(GROK)]})
        backup = self.reimport()["backup"]
        proc = self.run_cli("--reimport", "--from", backup, "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["source"], backup)
        for name in ("backup", "kept", "removed"):
            self.assertIn(name, report)
        self.assertEqual(sorted(report["kept"]), sorted(self.KEPT))
        self.assertEqual(routes_store.get_route(self.conn, "lp-low")["roles"],
                         {"judge": [GROK]})

    def test_cli_from_without_reimport_is_bad_argument(self) -> None:
        proc = self.run_cli("--from", str(ROUTES_JSON))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_cli_human_output(self) -> None:
        proc = self.run_cli("--reimport")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("сохранены:", proc.stdout)
        self.assertIn("удалены: my-flow", proc.stdout)
        self.assertIn("копия таблицы:", proc.stdout)


class PluginStoreTests(RoutesDbTestCase):
    """Поле `plugin` и вычисляемое `skill` в хранилище (listik-d9rj, порция d; listik-906l)."""

    def setUp(self) -> None:
        super().setUp()
        from listik import paths
        patcher = mock.patch.object(paths, "DATA_DIR", self.tmp_path / "data")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sample_rows_carry_plugin(self) -> None:
        self.import_sample()
        record = routes_store.get_route(self.conn, "lp-high")
        self.assertEqual((record["plugin"], record["skill"]), ("listik-pipeline", "high"))
        row = self.conn.execute("SELECT * FROM routes WHERE key = 'lp-low'").fetchone()
        self.assertEqual(row["plugin"], "listik-pipeline")
        self.assertNotIn("skill", row.keys())

    def test_prepare_checks_plugin(self) -> None:
        """Пункт 18a: три текста `check_plugin` через `upsert_route`, целиком."""
        cases = (({**swarm_record(), "plugin": "listik-pipeline"},
                  "plugin: поле только у kind=pipeline"),
                 (my_flow(key="full-high", plugin="pipeline-full"),
                  "plugin: допустимы listik-pipeline"),
                 (my_flow(key="full-high", plugin="listik-pipeline"),
                  "key: у плагина listik-pipeline ключ начинается с 'lp-'"))
        for record, text in cases:
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as ctx:
                    routes_store.upsert_route(self.conn, record)
                self.assertEqual(str(ctx.exception), text)
        self.assertEqual(routes_store.count(self.conn), 0)

    def test_create_route_with_plugin(self) -> None:
        record = routes_store.create_route(self.conn, key="lp-high", kind="pipeline",
                                           title="t", plugin="listik-pipeline")
        self.assertEqual((record["plugin"], record["skill"]), ("listik-pipeline", "high"))
        bare = routes_store.create_route(self.conn, key="bare", kind="pipeline", title="t")
        self.assertIsNone(bare["plugin"])
        self.assertIsNone(bare["skill"])

    def test_own_pipeline_without_plugin(self) -> None:
        """Пункт 17: свой конвейер с `command` и `roles={}` сохраняется без плагина."""
        record = routes_store.upsert_route(self.conn, my_flow())
        self.assertIsNone(record["plugin"])
        self.assertIsNone(record["skill"])
        self.assertEqual(record["roles"], {})
        self.assertEqual(record["command"], ["true"])

    def test_update_route_does_not_change_plugin(self) -> None:
        self.import_sample()
        self.assertNotIn("plugin", routes_store.UPDATE_FIELDS)
        with self.assertRaises(ValueError):
            routes_store.update_route(self.conn, "lp-high", plugin="pipeline-full")
        self.assertEqual(routes_store.get_route(self.conn, "lp-high")["plugin"],
                         "listik-pipeline")

    def test_backup_has_plugin_and_reads_back(self) -> None:
        self.import_sample()
        before = {r["key"]: r["plugin"] for r in routes_store.list_routes(self.conn)}
        with contextlib.redirect_stderr(io.StringIO()):
            report = routes_store.reimport(self.conn, ROUTES_JSON)
        saved = json.loads(pathlib.Path(report["backup"]).read_text(encoding="utf-8"))["routes"]
        self.assertEqual({r["key"]: r["plugin"] for r in saved}, before)
        self.conn.execute("UPDATE routes SET plugin = NULL")
        self.conn.commit()
        with contextlib.redirect_stderr(io.StringIO()):
            routes_store.reimport(self.conn, report["backup"])
        self.assertEqual({r["key"]: r["plugin"] for r in routes_store.list_routes(self.conn)},
                         before)


class OldRowTests(RoutesDbTestCase):
    """Пункт 18b: строка со старым плагином и ячейкой v1 в живой базе до `--reimport`."""

    def setUp(self) -> None:
        super().setUp()
        insert_old_row(self.conn)

    def test_get_route_has_no_skill(self) -> None:
        record = routes_store.get_route(self.conn, "full-high")
        self.assertIsNone(record["skill"])
        self.assertEqual(record["roles"], {"spec": OLD_CELL})

    def test_routes_response_hides_it(self) -> None:
        data = routes_store.routes_response(self.conn)
        record = next(r for r in data["routes"] if r["key"] == "full-high")
        self.assertIs(record["skill_missing"], True)
        self.assertIs(record["visible"], False)
        self.assertTrue(any("full-high" in w for w in data["warnings"]))

    def test_readers_skip_object_cells(self) -> None:
        self.assertEqual([u for u in harnesses_store.used_by(self.conn, "claude")
                          if u["route"] == "full-high"], [])
        record = {**routes_store.get_route(self.conn, "full-high"), "visible": True}
        [candidate] = assistant.route_candidates([record])
        self.assertEqual(candidate["roles"], {"spec": "claude"})


class FieldRulesTests(RoutesDbTestCase):
    """Правила полей — по одному случаю на нарушение."""

    def setUp(self) -> None:
        super().setUp()
        self.import_sample()

    def bad(self, **fields) -> str:
        with self.assertRaises(ValueError) as ctx:
            routes_store.update_route(self.conn, "lp-high", **fields)
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
        self.assertEqual(routes_store.update_route(self.conn, "lp-high", hint="")["hint"], "")

    def test_icon_none_is_allowed(self) -> None:
        self.assertIsNone(routes_store.update_route(self.conn, "lp-high", icon=None)["icon"])

    def test_visible_false_is_allowed(self) -> None:
        self.assertFalse(routes_store.update_route(self.conn, "lp-high",
                                                   visible=False)["visible"])


class UpdateRouteTests(RoutesDbTestCase):
    """`update_route`: допустимые и отвергаемые поля, расклад v2 (пункт 16)."""

    def setUp(self) -> None:
        super().setUp()
        self.import_sample()

    def rejected_roles(self, roles) -> str:
        before = routes_store.get_route(self.conn, "lp-high")
        with self.assertRaises(ValueError) as ctx:
            routes_store.update_route(self.conn, "lp-high", roles=roles)
        self.assertEqual(routes_store.get_route(self.conn, "lp-high"), before)
        return str(ctx.exception)

    def test_rejected_fields_name_the_field(self) -> None:
        # `key` сюда не подставить: оно уже занято позиционным параметром
        # сигнатуры, Python отвергнет вызов раньше проверки.
        for name, value in (("roles", {}), ("kind", "swarm"),
                            ("harness", "dsh"), ("position", 0),
                            ("command", ["echo", "{task_id}"])):
            with self.subTest(name=name):
                with self.assertRaises(ValueError) as ctx:
                    routes_store.update_route(self.conn, "lp-high", **{name: value})
                self.assertIn(name, str(ctx.exception))

    def test_empty_roles_rejected(self) -> None:
        self.assertEqual(self.rejected_roles({}), "roles: нужна хотя бы одна роль")

    def test_old_cell_rejected(self) -> None:
        self.assertEqual(self.rejected_roles({"writer": OLD_CELL}),
                         "roles.writer: непустой массив исполнителей")
        self.assertIn("неизвестная роль", self.rejected_roles({"spec": OLD_CELL}))

    def test_roles_not_object_rejected(self) -> None:
        self.assertEqual(self.rejected_roles("x"),
                         "roles: должен быть объектом с ролями writer/critic/implementer/lens/judge")

    def test_pi_channel_outside_catalog(self) -> None:
        roles = {"critic": [dict(DEVIN | {"harness": "codex"}),
                            {"harness": "pi", "channel": "nope", "effort": "high"}]}
        self.assertEqual(self.rejected_roles(roles),
                         "roles.critic[1].channel: у харнесса 'pi' нет канала 'nope'")

    def test_pi_missing_from_catalog(self) -> None:
        self.conn.execute("DELETE FROM harnesses WHERE key = 'pi'")
        self.conn.commit()
        roles = {"critic": [{"harness": "pi", "channel": "glm", "effort": "high"}]}
        self.assertEqual(self.rejected_roles(roles),
                         "roles.critic[0].harness: харнесса 'pi' нет в каталоге")

    def test_disabled_pi_is_fine(self) -> None:
        roles = {"critic": [{"harness": "pi", "channel": "deepseek", "effort": "high"}]}
        self.assertEqual(routes_store.update_route(self.conn, "lp-high", roles=roles)["roles"],
                         roles)

    def test_command_on_pipeline_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            routes_store.update_route(self.conn, "lp-high", command=["echo", "{task_id}"])
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
        before = routes_store.get_route(self.conn, "lp-xlow")
        after = routes_store.update_route(self.conn, "lp-xlow", title="Новый")
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
            routes_store.create_route(self.conn, key="lp-xlow", kind="swarm", title="Dup",
                                      roles={"impl": {"harness": "dsh"}})
        self.assertIn("lp-xlow", str(ctx.exception))

    def test_get_unknown_raises_not_found(self) -> None:
        with self.assertRaises(errors.NotFound):
            routes_store.get_route(self.conn, "нет-такого")

    def test_count_matches_list(self) -> None:
        self.assertEqual(routes_store.count(self.conn),
                         len(routes_store.list_routes(self.conn)))

    def test_delete_counts_tasks_but_keeps_them(self) -> None:
        self.conn.execute("INSERT INTO tasks(id, launch_route) VALUES('t1', 'lp-xlow')")
        self.conn.execute("INSERT INTO tasks(id, launch_route) VALUES('t2', 'lp-xlow')")
        self.conn.execute("INSERT INTO tasks(id, launch_route) VALUES('t3', 'lp-nano')")
        self.conn.commit()
        removed = routes_store.delete_route(self.conn, "lp-xlow")
        self.assertEqual(removed, 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 3)
        self.assertNotIn("lp-xlow", [r["key"] for r in routes_store.list_routes(self.conn)])
        with self.assertRaises(errors.NotFound):
            routes_store.get_route(self.conn, "lp-xlow")

    def test_reorder_full(self) -> None:
        reordered = routes_store.reorder(self.conn, list(reversed(EXPECTED_KEYS)))
        self.assertEqual([r["key"] for r in reordered], list(reversed(EXPECTED_KEYS)))
        self.assertEqual([r["position"] for r in reordered], list(range(len(EXPECTED_KEYS))))
        self.assertEqual([r["key"] for r in routes_store.list_routes(self.conn)],
                         list(reversed(EXPECTED_KEYS)))

    def test_reorder_subset_pushes_rest_to_end(self) -> None:
        rest = [k for k in EXPECTED_KEYS if k not in ("lp-xlow", "lp-nano")]
        reordered = routes_store.reorder(self.conn, ["lp-xlow", "lp-nano"])
        self.assertEqual([r["key"] for r in reordered], ["lp-xlow", "lp-nano", *rest])
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
        self.conn.execute("UPDATE routes SET roles = '{', command = '[' WHERE key = 'lp-high'")
        self.conn.commit()
        record = routes_store.get_route(self.conn, "lp-high")
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
            self.assertEqual(version, "17")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()

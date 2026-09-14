"""Найденная по ходу задача: связь `discovered-from` сразу при создании (listik-0wpx).

Проверяем приёмку карточки по пунктам:

1. `listik new --discovered-from <ID>`, поле `discovered_from` у `POST /api/tasks` и параметр
   MCP `listik_create` создают карточку сразу с мягкой связью; несуществующий id — ошибка до
   создания.
2. Протокол (`docs/harness-protocol.md` и блок в AGENTS.md/CLAUDE.md) требует заводить
   найденную задачу с `--discovered-from` — проверяется в tests/test_migrate.py и здесь
   сверкой блока с протоколом.
3. Упоминание чужого id в тексте без связи — `new` предупреждает и предлагает `dep link`
   (поле `link_hints[]` в ответах API и MCP). id внутри файлового пути (компонент после `/`
   или перед расширением) и внутри кавычек/обратных кавычек упоминанием не считается;
   `dep suggest`/`dep link` при этом видят все совпадения (режим `hints` только у подсказки).
4. В карточке-источнике находка видна в связях («найдена при», `incoming: true`).
5. Тесты CLI/API/MCP — этот файл.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import threading
from unittest import mock

from listik import deps, mcp, migrate, paths, server, store
from tests.helpers import TempDbTestCase

LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"


def _dep_rows(conn, issue_id=None, depends_on=None):
    sql = "SELECT * FROM deps"
    params: list = []
    where = []
    if issue_id is not None:
        where.append("issue_id = ?")
        params.append(issue_id)
    if depends_on is not None:
        where.append("depends_on = ?")
        params.append(depends_on)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return [dict(r) for r in conn.execute(sql, tuple(params))]


def _links(conn, task_id):
    return {x["id"]: x for x in store.get_task(conn, task_id)["deps_state"]["soft_links"]}


class CreateWithDiscoveredFromTests(TempDbTestCase):
    """Пункт 1 и 4: связь пишется вместе с карточкой и видна с обеих сторон."""

    def setUp(self) -> None:
        super().setUp()
        self.source = store.create_task(self.conn, title="Источник", project="demo")["id"]

    def test_create_links_source_and_discovered_card(self) -> None:
        found = store.create_task(self.conn, title="Находка", project="demo",
                                  description=f"упирается в {self.source}",
                                  discovered_from=self.source)["id"]
        rows = _dep_rows(self.conn, found, self.source)
        self.assertEqual([r["dep_type"] for r in rows], ["discovered-from"])

        own = _links(self.conn, found)[self.source]
        self.assertEqual(own["dep_title"], "найдена при")
        self.assertFalse(own["incoming"])

        reverse = _links(self.conn, self.source)[found]
        self.assertEqual(reverse["dep_title"], "найдена при")
        self.assertTrue(reverse["incoming"], "карточка-источник не видит находку")

    def test_source_shows_discovered_card_in_dependencies(self) -> None:
        found = store.create_task(self.conn, title="Находка", discovered_from=self.source)["id"]
        dependents = store.get_task(self.conn, self.source)["dependents"]
        self.assertIn({"issue_id": found, "dep_type": "discovered-from"}, dependents)

    def test_soft_link_does_not_block_anything(self) -> None:
        found = store.create_task(self.conn, title="Находка", discovered_from=self.source)["id"]
        state = store.get_task(self.conn, found)["deps_state"]
        self.assertTrue(state["ready"])
        self.assertFalse(state["blocked_by"])

    def test_missing_source_raises_before_insert(self) -> None:
        before = self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        with self.assertRaises(KeyError):
            store.create_task(self.conn, title="Плохая", discovered_from="demo-zzzz")
        after = self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        self.assertEqual(after, before, "карточка создана, хотя источник не найден")

    def test_self_discovered_from_rejected(self) -> None:
        # id задаётся явно (так умеет API): карточка не может быть найдена при самой себе.
        row = store.create_task(self.conn, title="Существующая", task_id="demo-self",
                                project="demo")
        with self.assertRaises(ValueError):
            store.create_task(self.conn, title="Сама при себе", task_id=row["id"],
                              discovered_from=row["id"])
        self.assertEqual(_dep_rows(self.conn, "demo-self", "demo-self"), [])
        fresh = store.get_task(self.conn, "demo-self")
        self.assertEqual(fresh["title"], "Существующая", "карточка всё-таки перезаписана")

    def test_project_is_not_inherited_from_source(self) -> None:
        found = store.create_task(self.conn, title="В другом проекте",
                                  discovered_from=self.source)["id"]
        self.assertIsNone(store.get_task(self.conn, found)["project"])


class LinkHintsTests(TempDbTestCase):
    """Пункт 3: упоминания без связи превращаются в подсказку `dep link`."""

    def setUp(self) -> None:
        super().setUp()
        self.other = store.create_task(self.conn, title="Упомянутая", project="demo")["id"]

    def test_hints_off_by_default(self) -> None:
        task = store.create_task(self.conn, title="Без подсказок", project="demo",
                                 description=f"см. {self.other}")
        self.assertNotIn("link_hints", task)

    def test_hints_list_unlinked_mention(self) -> None:
        task = store.create_task(self.conn, title="С подсказкой", project="demo",
                                 description=f"см. {self.other}", hints=True)
        self.assertEqual([h["id"] for h in task["link_hints"]], [self.other])

    def test_linked_mention_is_not_a_hint(self) -> None:
        task = store.create_task(self.conn, title="Уже связана", project="demo",
                                 description=f"см. {self.other}",
                                 discovered_from=self.other, hints=True)
        self.assertEqual(task["link_hints"], [])

    def test_no_mentions_no_hints(self) -> None:
        task = store.create_task(self.conn, title="Просто задача", project="demo", hints=True)
        self.assertEqual(task["link_hints"], [])

    def test_missing_ids_are_not_hints(self) -> None:
        task = store.create_task(self.conn, title="Чужой id", project="demo",
                                 description="см. demo-zzzz", hints=True)
        self.assertEqual(task["link_hints"], [])

    def test_path_mention_is_not_a_hint(self) -> None:
        """id как компонент пути — ссылка на файл, а не на карточку (вердикт grok).

        `docs/specs/<id>.md` — id перед расширением, `/wt/<id>/listik/store.py` —
        id после `/`; в обоих случаях карточка не упомянута, и `dep link` по
        такой подсказке ставил бы связь с файлом, а не с задачей."""
        for text in (f"спека лежит в docs/specs/{self.other}.md",
                     f"код в /wt/{self.other}/listik/store.py",
                     f"отчёт: ./{self.other}/README.md"):
            with self.subTest(text=text):
                task = store.create_task(self.conn, title="Путь", project="demo",
                                         description=text, hints=True)
                self.assertEqual(task["link_hints"], [], text)

    def test_file_extension_mention_is_not_a_hint(self) -> None:
        """`<id>.md`, `<id>.py:12` — имя файла, а не упоминание задачи."""
        task = store.create_task(self.conn, title="Файл", project="demo",
                                 description=f"см. {self.other}.md и {self.other}.py:12",
                                 hints=True)
        self.assertEqual(task["link_hints"], [])

    def test_quoted_mention_is_not_a_hint(self) -> None:
        """id в кавычках и обратных кавычках — фрагмент кода или цитата (вердикт grok)."""
        for text in (f'x = "{self.other}"',
                     f"if task_id == '{self.other}':",
                     f"`{self.other}`",
                     f"«{self.other}»"):
            with self.subTest(text=text):
                task = store.create_task(self.conn, title="Код", project="demo",
                                         description=text, hints=True)
                self.assertEqual(task["link_hints"], [], text)

    def test_prose_mention_with_period_is_still_a_hint(self) -> None:
        """Точка в конце предложения — не расширение файла: упоминание остаётся."""
        task = store.create_task(self.conn, title="Проза", project="demo",
                                 description=f"Упирается в {self.other}. Пока неясно.",
                                 hints=True)
        self.assertEqual([h["id"] for h in task["link_hints"]], [self.other])

    def test_mention_at_start_of_text_is_still_a_hint(self) -> None:
        """id с нулевой позиции — упоминание: пустая строка «до» не разделитель пути."""
        task = store.create_task(self.conn, title=f"{self.other}: починить",
                                 project="demo", hints=True)
        self.assertEqual([h["id"] for h in task["link_hints"]], [self.other])

    def test_own_id_in_description_is_not_a_hint(self) -> None:
        """Свой id в описании — не повод подсказывать связь с самой собой (вердикт grok)."""
        plain = store.create_task(self.conn, title="Сама себя", task_id="demo-self",
                                  project="demo", description="см. demo-self", hints=True)
        self.assertEqual(plain["link_hints"], [])
        in_path = store.create_task(self.conn, title="Сама себя в пути", task_id="demo-self2",
                                    project="demo",
                                    description="спека: docs/specs/demo-self2.md",
                                    hints=True)
        self.assertEqual(in_path["link_hints"], [])

    def test_dep_link_mode_still_sees_path_mentions(self) -> None:
        """`dep suggest`/`dep link` работают в режиме `text` — их поведение не меняем.

        Фильтр путей и кавычек включён только у подсказок `link_hints` (`mode="hints"`):
        `dep link` запускает человек и сам решает, что связывать."""
        task = store.create_task(self.conn, title="Путь", project="demo",
                                 description=f"спека в docs/specs/{self.other}.md",
                                 hints=True)
        self.assertEqual(task["link_hints"], [])
        self.assertEqual([x["id"] for x in deps.mentioned(self.conn, task["id"])],
                         [self.other])
        self.assertEqual(deps.mentioned(self.conn, task["id"], mode="hints"), [])

    def test_unknown_mention_mode_rejected(self) -> None:
        task = store.create_task(self.conn, title="Режим", project="demo")["id"]
        with self.assertRaises(ValueError):
            deps.mentioned(self.conn, task, mode="nope")


class CliDiscoveredFromTests(TempDbTestCase):
    """Пункты 1, 3, 4 на живом CLI (`--local`, временная база)."""

    def setUp(self) -> None:
        super().setUp()
        self.source = store.create_task(self.conn, title="Источник", project="demo")["id"]

    def _run(self, *args):
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))

    def test_new_with_discovered_from_links(self) -> None:
        p = self._run("new", "Находка", "-p", "demo", "--discovered-from", self.source)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(f"найдена при: ", p.stdout)
        self.assertIn(self.source, p.stdout)
        found = self.conn.execute(
            "SELECT issue_id FROM deps WHERE depends_on=? AND dep_type='discovered-from'",
            (self.source,)).fetchone()
        self.assertIsNotNone(found)
        self.assertIn(found["issue_id"], p.stdout)

    def test_new_with_missing_source_creates_nothing(self) -> None:
        before = self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        p = self._run("new", "Плохая", "-p", "demo", "--discovered-from", "demo-zzzz")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("задача не найдена", p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        after = self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        self.assertEqual(after, before, "карточка создана, хотя источник не найден")

    def test_new_warns_about_unlinked_mention(self) -> None:
        p = self._run("new", "С упоминанием", "-p", "demo", "-d", f"упирается в {self.source}")
        self.assertEqual(p.returncode, 0, p.stderr)
        new_id = self.conn.execute(
            "SELECT id FROM tasks WHERE title='С упоминанием'").fetchone()["id"]
        self.assertIn(self.source, p.stderr)
        self.assertIn(f"listik dep link {new_id}", p.stderr)
        self.assertNotIn(self.source, p.stdout, "предупреждение попало в stdout")

    def test_new_with_link_does_not_warn_about_same_mention(self) -> None:
        p = self._run("new", "Со связью", "-p", "demo", "-d", f"упирается в {self.source}",
                      "--discovered-from", self.source)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("dep link", p.stderr)

    def test_new_does_not_warn_about_path_or_code_mention(self) -> None:
        """Ложное предупреждение по файловому пути и фрагменту кода (вердикт grok)."""
        p = self._run("new", "Со спекой", "-p", "demo", "-d",
                      f"спека: docs/specs/{self.source}.md, код /wt/{self.source}/listik/store.py, "
                      f'проверка: x = "{self.source}"')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("dep link", p.stderr)

    def test_json_output_carries_link_hints(self) -> None:
        p = self._run("new", "Для агента", "-p", "demo", "-d", f"см. {self.source}", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        task = json.loads(p.stdout)
        self.assertEqual([h["id"] for h in task["link_hints"]], [self.source])
        self.assertNotIn("dep link", p.stdout, "--json должен оставаться машинным")

    def test_show_source_prints_discovered_link(self) -> None:
        p = self._run("new", "Находка", "-p", "demo", "--discovered-from", self.source)
        self.assertEqual(p.returncode, 0, p.stderr)
        found = self.conn.execute(
            "SELECT issue_id FROM deps WHERE depends_on=? AND dep_type='discovered-from'",
            (self.source,)).fetchone()["issue_id"]
        shown = self._run("show", self.source)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn(f"{found} (найдена при)", shown.stdout)


class ApiDiscoveredFromTests(TempDbTestCase):
    """Пункт 1 на HTTP-эндпоинте POST /api/tasks."""

    def setUp(self) -> None:
        super().setUp()
        self._saved = (paths.DB_PATH, server._conn_made, server._conn_local)
        paths.DB_PATH = self.db_path
        server._conn_made = False
        server._conn_local = threading.local()
        self.source = store.create_task(self.conn, title="Источник", project="demo")["id"]

    def tearDown(self) -> None:
        conn = getattr(server._conn_local, "conn", None)
        if conn is not None:
            conn.close()
        paths.DB_PATH, server._conn_made, server._conn_local = self._saved
        super().tearDown()

    def post(self, **body):
        return server.handle("POST", "/api/tasks", {}, body, authed=True)

    def test_post_creates_soft_link(self) -> None:
        status, task = self.post(title="Находка", project="demo", discovered_from=self.source)
        self.assertEqual(status, 201)
        rows = _dep_rows(self.conn, task["id"], self.source)
        self.assertEqual([r["dep_type"] for r in rows], ["discovered-from"])
        self.assertEqual(_links(self.conn, self.source)[task["id"]]["incoming"], True)

    def test_post_missing_source_is_404_and_creates_nothing(self) -> None:
        before = self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        with self.assertRaises(server.ApiError) as ctx:
            self.post(title="Плохая", discovered_from="demo-zzzz")
        self.assertEqual(ctx.exception.status, 404)
        after = self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        self.assertEqual(after, before)

    def test_post_non_string_source_is_400(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.post(title="Плохая", discovered_from=5)
        self.assertEqual(ctx.exception.status, 400)

    def test_post_returns_link_hints_for_unlinked_mention(self) -> None:
        status, task = self.post(title="С упоминанием", project="demo",
                                 description=f"см. {self.source}")
        self.assertEqual(status, 201)
        self.assertEqual([h["id"] for h in task["link_hints"]], [self.source])

    def test_post_with_link_has_no_hints(self) -> None:
        status, task = self.post(title="Со связью", project="demo",
                                 description=f"см. {self.source}",
                                 discovered_from=self.source)
        self.assertEqual(status, 201)
        self.assertEqual(task["link_hints"], [])

    def test_post_path_mention_has_no_hints(self) -> None:
        status, task = self.post(title="Со спекой", project="demo",
                                 description=f"спека: docs/specs/{self.source}.md")
        self.assertEqual(status, 201)
        self.assertEqual(task["link_hints"], [])


class McpDiscoveredFromTests(TempDbTestCase):
    """Пункт 1 и 3 в MCP: схема инструмента и сам вызов."""

    def setUp(self) -> None:
        super().setUp()
        self.source = store.create_task(self.conn, title="Источник", project="demo")["id"]

    def test_schema_exposes_discovered_from(self) -> None:
        resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, conn=self.conn)
        tools = {t["name"]: t for t in resp["result"]["tools"]}
        props = tools["listik_create"]["inputSchema"]["properties"]
        self.assertIn("discovered_from", props)
        self.assertIn("discovered-from", tools["listik_create"]["description"])

    def test_create_with_discovered_from(self) -> None:
        out = mcp.call_tool("listik_create", {"title": "Находка", "project": "demo",
                                              "description": f"см. {self.source}",
                                              "discovered_from": self.source}, conn=self.conn)
        rows = _dep_rows(self.conn, out["id"], self.source)
        self.assertEqual([r["dep_type"] for r in rows], ["discovered-from"])
        self.assertEqual(out["link_hints"], [])

    def test_create_missing_source_does_not_create(self) -> None:
        before = self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        with self.assertRaises(KeyError):
            mcp.call_tool("listik_create", {"title": "Плохая",
                                            "discovered_from": "demo-zzzz"}, conn=self.conn)
        after = self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        self.assertEqual(after, before)

    def test_create_returns_link_hints(self) -> None:
        out = mcp.call_tool("listik_create", {"title": "С упоминанием", "project": "demo",
                                              "description": f"см. {self.source}"}, conn=self.conn)
        self.assertEqual([h["id"] for h in out["link_hints"]], [self.source])

    def test_create_path_mention_has_no_hints(self) -> None:
        out = mcp.call_tool("listik_create", {"title": "Со спекой", "project": "demo",
                                              "description": f"спека: docs/specs/{self.source}.md"},
                            conn=self.conn)
        self.assertEqual(out["link_hints"], [])


class ProtocolMentionsDiscoveredFromTests(TempDbTestCase):
    """Пункт 2: протокол и блоки в AGENTS.md/CLAUDE.md говорят про --discovered-from."""

    def test_protocol_documents_the_flag(self) -> None:
        text = (paths.ROOT_DIR / "docs" / "harness-protocol.md").read_text(encoding="utf-8")
        self.assertIn("--discovered-from", text)

    def test_generated_block_matches_protocol(self) -> None:
        block = migrate.block()
        self.assertIn("--discovered-from", block)
        for name in ("AGENTS.md", "CLAUDE.md"):
            self.assertIn(block, (paths.ROOT_DIR / name).read_text(encoding="utf-8"), name)


class LocalCallDiscoveredFromTests(TempDbTestCase):
    """Фолбэк CLI без сервера идёт через `client.local_call` — связь и подсказка те же."""

    def test_local_call_create(self) -> None:
        from listik import client
        source = store.create_task(self.conn, title="Источник", project="demo")["id"]
        with mock.patch.object(client.db_mod, "init", return_value=self.conn):
            out = client.local_call("create", title="Находка", project="demo",
                                    description=f"см. {source}", issue_type="task",
                                    discovered_from=source, hints=True)
        rows = _dep_rows(self.conn, out["id"], source)
        self.assertEqual([r["dep_type"] for r in rows], ["discovered-from"])
        self.assertEqual(out["link_hints"], [])


if __name__ == "__main__":
    import unittest
    unittest.main()

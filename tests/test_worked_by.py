"""«Кто выполнял» — вычисляемое поле `worked_by`.

У закрытой карточки не видно, кто по ней работал: `listik done` держателя не
снимает, а `stage --to done` — снимает, поэтому у одной закрытой карточки
держатель есть, а у другой пусто; `assignee` же помнит только самый первый
claim. `worked_by` собирается по событиям `claim`/`heartbeat`, сделанным от
своего имени: выдача оркестратором (`stage --holder кому`) и heartbeat чужой
рукой в список не идут.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

from listik import actors as actors_mod
from listik import store
from tests.helpers import TempDbTestCase

LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"


class WorkedByTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task = store.create_task(self.conn, title="Порция", project="demo")["id"]

    def card(self) -> dict:
        return store.get_task(self.conn, self.task)

    def test_own_claim_is_worked_by(self) -> None:
        """C1: свой claim — выполнял."""
        store.claim(self.conn, self.task, holder="claude", actor="agent:claude",
                    harness="claude")
        card = self.card()
        self.assertEqual(card["worked_by"], ["agent:claude"])
        self.assertEqual(card["worked_by_title"], "Claude")

    def test_closed_card_without_holder_keeps_worked_by(self) -> None:
        """C2: форма listik-ybjo — claim на s1, handoff, закрытие через `stage --to done`."""
        store.claim(self.conn, self.task, holder="claude", actor="agent:claude",
                    harness="claude")
        for stage in ("s2-review", "s3-impl", "s4-judge", "done"):
            store.next_stage(self.conn, self.task, to_stage=stage, actor="agent:claude",
                             harness="claude")
        card = self.card()
        self.assertEqual(card["status"], "done")
        self.assertEqual(card["holder"], "")
        self.assertEqual(card["worked_by"], ["agent:claude"])
        self.assertEqual(card["worked_by_title"], "Claude")
        self.assertFalse(card["abandoned"])
        self.assertFalse(card["not_taken"])
        self.assertFalse(card["not_taken_warn"])
        self.assertFalse(card["stale"])

    def test_issue_by_orchestrator_is_not_worked_by(self) -> None:
        """C3: выдача оркестратором — не выполнял, пока нет своего claim."""
        store.next_stage(self.conn, self.task, to_stage="s3-impl", holder="dsh",
                         actor="agent:claude", harness="claude")
        card = self.card()
        self.assertEqual(card["holder"], "dsh")
        self.assertEqual(card["worked_by"], [])
        self.assertEqual(card["worked_by_title"], "")
        store.claim(self.conn, self.task, holder="dsh", actor="agent:dsh", harness="dsh")
        card = self.card()
        # Свой claim исполнителя — выполнял он; оркестратор, который выдал карточку,
        # в списке не появляется, как и `assignee` (что бы там ни было).
        self.assertEqual(card["worked_by"], ["agent:dsh"])
        self.assertNotIn("agent:claude", card["worked_by"])
        self.assertNotIn(card["assignee"], [k for k in card["worked_by"] if k != "agent:dsh"])

    def test_heartbeat_by_other_hand_is_not_worked_by(self) -> None:
        """C4: heartbeat чужой рукой не считается, своей — считается."""
        store.next_stage(self.conn, self.task, to_stage="s3-impl", holder="dsh",
                         actor="agent:claude", harness="claude")
        store.heartbeat(self.conn, self.task, holder="dsh", actor="agent:claude",
                        min_interval_min=0)
        self.assertEqual(self.card()["worked_by"], [])
        store.heartbeat(self.conn, self.task, holder="dsh", actor="agent:dsh",
                        min_interval_min=0)
        self.assertEqual(self.card()["worked_by"], ["agent:dsh"])

    def test_several_actors_in_order_of_first_event(self) -> None:
        """C5: порядок — по первому своему событию, написание — канонический ключ."""
        store.claim(self.conn, self.task, holder="claude", actor="agent:claude")
        store.heartbeat(self.conn, self.task, holder="agent:claude", actor="agent:claude",
                        min_interval_min=0)
        store.update_task(self.conn, self.task, holder="")
        store.claim(self.conn, self.task, holder="dsh", actor="agent:dsh")
        store.heartbeat(self.conn, self.task, holder="dsh", harness="dsh",
                        min_interval_min=0)
        card = self.card()
        self.assertEqual(card["worked_by"], ["agent:claude", "agent:dsh"])
        self.assertEqual(card["worked_by_title"],
                         ", ".join(actors_mod.display(k) for k in card["worked_by"]))

    def test_claim_without_actor_counts_by_harness(self) -> None:
        """C6: старый клиент без `--actor` — событие всё равно своё (по `harness`)."""
        store.claim(self.conn, self.task, holder="dsh")
        self.assertEqual(self.card()["worked_by"], ["agent:dsh"])

    def test_card_without_claim_events(self) -> None:
        """C7: без событий claim/heartbeat — пустой список, не None."""
        card = self.card()
        self.assertEqual(card["worked_by"], [])
        self.assertEqual(card["worked_by_title"], "")
        store.update_task(self.conn, self.task, status="in_progress")
        card = self.card()
        self.assertEqual(card["worked_by"], [])
        self.assertEqual(card["worked_by_title"], "")

    def test_done_keeping_holder(self) -> None:
        """C8: `listik done` снимает держателя (listik-ugw8) — поле от этого не зависит."""
        store.claim(self.conn, self.task, holder="grok", actor="agent:grok")
        store.update_task(self.conn, self.task, status="done", stage="done")
        card = self.card()
        self.assertFalse(card["holder"])
        self.assertEqual(card["worked_by"], ["agent:grok"])

    def test_claim_older_than_hundred_events(self) -> None:
        """C9: поле считается не по срезу `events[]` из 100 строк."""
        store.claim(self.conn, self.task, holder="claude", actor="agent:claude")
        for i in range(120):
            store.event(self.conn, self.task, "note", note=f"шаг {i}",
                        ts=f"2999-01-01T00:{i // 60:02d}:{i % 60:02d}Z")
        self.conn.commit()
        self.assertEqual(self.card()["worked_by"], ["agent:claude"])


class WorkedByCliTests(TempDbTestCase):
    """CLI `show`/`list` через `bin/listik --local` (оба пути печатают одной `show_task`)."""

    def setUp(self) -> None:
        super().setUp()
        self.cfg = self.tmp_path / "config.toml"
        self.cfg.write_text("", encoding="utf-8")
        self.task = store.create_task(self.conn, title="Порция", project="demo")["id"]

    def run_cli(self, *args):
        env = {**os.environ, "LISTIK_DB": str(self.db_path), "LISTIK_CONFIG": str(self.cfg),
               "LISTIK_LOG": str(self.tmp_path / "cli.log")}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))

    def show(self) -> str:
        p = self.run_cli("show", self.task)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout

    def test_show_prints_single_actor(self) -> None:
        """C10: один актор — строка `  выполнял:    Claude`."""
        store.claim(self.conn, self.task, holder="claude", actor="agent:claude",
                    harness="claude")
        self.assertIn("  выполнял:    Claude", self.show())

    def test_show_prints_several_actors(self) -> None:
        """C10: два и больше — `  выполняли:   …`."""
        store.claim(self.conn, self.task, holder="claude", actor="agent:claude")
        store.update_task(self.conn, self.task, holder="")
        store.claim(self.conn, self.task, holder="dsh", actor="agent:dsh")
        line = next((ln for ln in self.show().splitlines()
                     if ln.startswith("  выполняли:   ")), None)
        self.assertIsNotNone(line)
        self.assertIn("Claude, ", line)

    def test_show_without_worked_by_has_no_line(self) -> None:
        """C10: пустой список — строки нет вовсе."""
        out = self.show()
        self.assertNotIn("выполнял", out)
        self.assertNotIn("выполняли", out)

    def test_json_has_fields_in_show_and_list(self) -> None:
        """C11: ключи есть и в `show --json`, и в `list --json`."""
        store.claim(self.conn, self.task, holder="claude", actor="agent:claude")
        p = self.run_cli("show", self.task, "--json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        card = json.loads(p.stdout)
        self.assertEqual(card["worked_by"], ["agent:claude"])
        self.assertEqual(card["worked_by_title"], "Claude")
        p = self.run_cli("list", "--project", "demo", "--json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        listed = json.loads(p.stdout)["tasks"][0]
        self.assertIn("worked_by", listed)
        self.assertIn("worked_by_title", listed)

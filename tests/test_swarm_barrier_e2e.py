"""Сквозная приёмка барьера волны роя (`bin/listik-swarm`) на живом сервере Listik,
настоящем `bin/listik`, настоящем git, поддельных воркере и арбитре (listik-dzf0,
порция f). Юнит-тесты барьера (порции a–e) гоняют `runBarrier`/`decide` на подставном
`listik`; здесь — процесс роя реально общается с реальным сервером, реально заводит
git worktree/веток и реально сливает их в `main`.

Обвязка (`SwarmE2ECase`, из `tests.test_swarm_e2e`) не копируется и не меняется; этот
модуль только наследует её и добавляет свой шаблон воркера (плюс запись `shared.txt`
при `FAKE_SHARED=1`) и хелперы барьера (маркеры журнала, `swarm.json`, ручное закрытие
задачи с готовым деревом).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock
from pathlib import Path

from listik import deps as deps_mod
from listik import store
from listik import worktree as worktree_mod
from tests.test_swarm_e2e import LISTIK_BIN, REPO_DIR, SwarmE2ECase, _WORKER_SRC

FAKE_ARBITER = REPO_DIR / "swarm" / "test" / "fixtures" / "fake-arbiter.mjs"

MERGED_MARK = "рой: влито:"
UNFROZEN_MARK = "рой: разморожена:"
ARBITER_MARK = "рой: арбитр:"
HALT_LABEL = "swarm:halt"
SWARM_AUTHOR = "agent:listik-swarm"

GREEN_INTEGRATION = [[sys.executable, "-c", "raise SystemExit(0)"]]
RED_INTEGRATION = [[sys.executable, "-c", "print('boom'); raise SystemExit(1)"]]
ARBITER_CMD = ["node", str(FAKE_ARBITER), "{prompt}", "{files}", "m/{model}"]

# Воркер сценария барьера: поведение `_WORKER_SRC` (9hcc.d) плюс перезапись
# `shared.txt` единственной строкой `<id>`, когда в окружении `FAKE_SHARED=1` --
# перед `git add -A`, чтобы попасть в тот же коммит.
_BARRIER_WORKER_SRC = _WORKER_SRC.replace(
    'git("add", "-A")',
    'if os.environ.get("FAKE_SHARED") == "1":\n'
    '    with open("shared.txt", "w", encoding="utf-8") as fh:\n'
    '        fh.write(task_id + "\\n")\n\n'
    'git("add", "-A")',
    1,
)
assert _BARRIER_WORKER_SRC != _WORKER_SRC


class SwarmBarrierE2ECase(SwarmE2ECase):
    """Общая обвязка барьера: свой воркер (плюс `shared.txt`), `swarm.json`,
    ручное закрытие задачи с готовым деревом, разбор маркеров журнала барьера."""

    def setUp(self) -> None:
        super().setUp()
        self.worker_py.write_text(
            _BARRIER_WORKER_SRC.replace("__LISTIK_BIN__", str(LISTIK_BIN).replace("\\", "\\\\")),
            encoding="utf-8")

    # ------------------------------------------------------------ уборка интеграции/арбитра
    #
    # `SwarmE2ECase.tearDown` снимает только процесс самого роя (`launch_pid` карточек и
    # `node bin/listik-swarm`); команды интеграции и арбитра барьер запускает `detached:
    # true` (`swarm/barrier.mjs:runIntegrationCommand`, `swarm/arbiter.mjs:runArbiter`) --
    # своя, независимая группа процессов, которую унаследованная уборка не видит. В штатном
    # прогоне они уже завершились к моменту выхода роя (`wait_swarm` дожидается кода
    # возврата), но если `wait_swarm` упрётся в дедлайн и убьёт рой посреди интеграции или
    # работы арбитра, их группа осталась бы жить -- досюда достаём её сами: по `cwd`
    # (интеграция — `self.project_dir`, арбитр — дерево задачи под `.worktrees`) и по pid,
    # если он вдруг попал в `integration-*.log`/`arbiter-*.log`.
    def tearDown(self) -> None:
        try:
            self._cleanup_barrier_processes()
        finally:
            super().tearDown()

    def _cleanup_barrier_processes(self) -> None:
        pids: set[int] = set()
        pids |= self._pids_with_open_files_under(self.project_dir)
        pids |= self._pids_with_open_files_under(self.project_dir / ".worktrees")
        for pattern in ("integration-*.log", "arbiter-*.log"):
            for log_path in self.swarm_log_dir.glob(pattern):
                pids |= self._pids_mentioned_in(log_path)
        for pid in pids:
            self._kill_process_group(pid)

    def _pids_with_open_files_under(self, directory: Path) -> set[int]:
        if shutil.which("lsof") is None or not directory.exists():
            return set()
        proc = subprocess.run(["lsof", "-t", "+D", str(directory)],
                              capture_output=True, text=True)
        return {int(line) for line in proc.stdout.split() if line.isdigit()}

    def _pids_mentioned_in(self, log_path: Path) -> set[int]:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        return {int(n) for n in re.findall(r"(?i)\bpid[:=\s]+(\d+)", text)}

    def _kill_process_group(self, pid: int) -> None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig)
            except (ProcessLookupError, PermissionError):
                try:
                    os.kill(pid, sig)
                except (ProcessLookupError, PermissionError):
                    return
            deadline = time.monotonic() + (5 if sig == signal.SIGTERM else 1)
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return
                time.sleep(0.1)

    # ------------------------------------------------------------ swarm.json

    def write_swarm_config(self, config: dict) -> None:
        (self.tmp_path / "swarm.json").write_text(json.dumps(config), encoding="utf-8")

    # ------------------------------------------------------------ git

    def git(self, *args, cwd=None) -> str:
        proc = subprocess.run(["git", "-C", str(cwd or self.project_dir), *args],
                              capture_output=True, text=True, check=True)
        return proc.stdout

    def commit_subjects(self, ref="main") -> list[str]:
        return self.git("log", ref, "--format=%s").splitlines()

    def head_sha(self, ref="main") -> str:
        return self.git("rev-parse", ref).strip()

    def is_ancestor(self, ancestor_ref: str, ref: str) -> bool:
        proc = subprocess.run(
            ["git", "-C", str(self.project_dir), "merge-base", "--is-ancestor", ancestor_ref, ref],
            capture_output=True, text=True)
        return proc.returncode == 0

    def branch_exists(self, branch: str) -> bool:
        proc = subprocess.run(
            ["git", "-C", str(self.project_dir), "rev-parse", "--verify", "--quiet", branch],
            capture_output=True, text=True)
        return proc.returncode == 0

    # ------------------------------------------------------------ карточки сценария

    def scenario_task(self, title: str, *, route: str | None) -> dict:
        """Как `make_scenario_task`, но `write_scope` ставится после создания как
        `[f"{id}.txt"]` -- ровно то, что пишет воркер, поэтому `outside` пуст, если
        задача не пишет ещё и `shared.txt`."""
        task = self.make_scenario_task(title, route=route)
        store.update_task(self.conn, task["id"], write_scope=[f"{task['id']}.txt"])
        return self.row(task["id"])

    def close_task_manually(self, task_id: str, *, port_label: str, dirty=False) -> dict:
        """Закрытая руками задача с готовым деревом (без запуска роя): `worktree.ensure`
        + коммит `<id>.txt` + `write_scope`/метка/`done`. `dirty=True` оставляет
        незакоммиченный файл в дереве после этого (гейт «грязное дерево»)."""
        info = worktree_mod.ensure(str(self.project_dir), task_id)
        tree = Path(info["path"])
        (tree / f"{task_id}.txt").write_text(task_id, encoding="utf-8")
        self.git("add", "-A", cwd=tree)
        self.git("commit", "-q", "-m", task_id, cwd=tree)
        store.update_task(self.conn, task_id, worktree=info["path"], branch=info["branch"],
                          labels=[port_label], write_scope=[f"{task_id}.txt"])
        store.update_task(self.conn, task_id, status="done")
        self.conn.commit()
        if dirty:
            (tree / "untracked.txt").write_text("грязь\n", encoding="utf-8")
        return info

    def prepare_frozen_task(self, task_id: str, *, owner_id: str, port_label: str) -> dict:
        """Открытая задача с уже заведённым деревом (от текущей базы), меткой
        `frozen-by:<owner_id>` и незакоммиченным `note.txt` -- как заморозка
        роя `listik watch` (swarm-5) её оставила бы."""
        info = worktree_mod.ensure(str(self.project_dir), task_id)
        tree = Path(info["path"])
        (tree / "note.txt").write_text("незакоммиченная правка\n", encoding="utf-8")
        row = self.row(task_id)
        labels = [l for l in json.loads(row["labels"] or "[]")] + [port_label, f"frozen-by:{owner_id}"]
        store.update_task(self.conn, task_id, worktree=info["path"], branch=info["branch"], labels=labels)
        self.conn.commit()
        return info

    # ------------------------------------------------------------ маркеры журнала барьера

    def marked_comments(self, task_id: str, mark: str) -> list[dict]:
        return [c for c in self.comments(task_id, "journal")
                if c["author"] == SWARM_AUTHOR and c["text"].startswith(mark)]

    def marked_records(self, task_id: str, mark: str) -> list[dict]:
        return [json.loads(c["text"][len(mark):].strip()) for c in self.marked_comments(task_id, mark)]

    OPEN_STATUSES = {"open", "in_progress", "blocked", "review"}

    def halt_card_ids(self) -> list[str]:
        out = []
        for tid in self._all_task_ids_in_project():
            row = self.row(tid)
            if row is None or row["status"] not in self.OPEN_STATUSES:
                continue
            if HALT_LABEL in json.loads(row["labels"] or "[]"):
                out.append(tid)
        return out

    def _all_task_ids_in_project(self) -> list[str]:
        rows = self.conn.execute("SELECT id FROM tasks WHERE project = 'p'").fetchall()
        return [r["id"] for r in rows]


# ------------------------------------------------------------------ сценарий 1 (п.3)

class WaveMergesThenNextFromNewHeadTests(SwarmBarrierE2ECase):
    """п.3: волна вливается по одной, следующая — от нового HEAD."""

    def test_wave_merges_then_next_from_new_head(self):
        a = self.scenario_task("A", route="fake-low")
        b = self.scenario_task("B", route="fake-low")
        c = self.scenario_task("C", route="fake-low")
        store.add_dep(self.conn, c["id"], a["id"], dep_type="blocks", created_by="dmitry")
        self.write_swarm_config({"integration": GREEN_INTEGRATION})

        proc = self.start_swarm(parallel=2, interval=1)
        code = self.wait_swarm(proc, deadline=90)
        self.assertEqual(code, 0, self.swarm_log_tail(proc, 200))

        subjects = self.commit_subjects("main")
        self.assertEqual(subjects[0], c["id"], subjects)
        self.assertEqual(set(subjects[1:3]), {a["id"], b["id"]}, subjects)
        self.assertEqual(subjects[3], "старт", subjects)
        self.assertEqual(self.git("log", "main", "--merges", "--format=%s"), "")

        recs = {}
        for tid in (a["id"], b["id"], c["id"]):
            records = self.marked_records(tid, MERGED_MARK)
            self.assertEqual(len(records), 1, (tid, records))
            recs[tid] = records[0]
            self.assertEqual(recs[tid]["files"], [f"{tid}.txt"], (tid, recs[tid]))
            self.assertEqual(recs[tid]["outside"], [], (tid, recs[tid]))
            row = self.row(tid)
            self.assertFalse(row["worktree"], tid)
            self.assertFalse(row["branch"], tid)
            self.assertFalse(self.branch_exists(f"task/{tid}"), tid)

        self.assertTrue(self.is_ancestor(recs[a["id"]]["sha"], recs[c["id"]]["sha"]))
        self.assertTrue(self.is_ancestor(recs[b["id"]]["sha"], recs[c["id"]]["sha"]))
        self.assertEqual(recs[c["id"]]["sha"], self.head_sha("main"))

        self.assertFalse(list((self.project_dir / ".worktrees").iterdir()))
        self.assertEqual(self.halt_card_ids(), [])

        log_text = "\n".join(self.swarm_log_lines(proc))
        self.assertLess(log_text.index(f"влито {a['id']}"), log_text.index(f"запуск {c['id']}"))
        self.assertLess(log_text.index(f"влито {b['id']}"), log_text.index(f"запуск {c['id']}"))
        self.assertIn("интеграция:", log_text)
        self.assertIn("→ код 0", log_text)
        self.assertIn(f"убрано дерево {a['id']}", log_text)

        integration_logs = list(self.swarm_log_dir.glob("integration-p-*.log"))
        self.assertTrue(integration_logs, list(self.swarm_log_dir.iterdir()))


# ------------------------------------------------------------------ сценарий 2 (п.4)

class ArbiterResolvesConflictTests(SwarmBarrierE2ECase):
    """п.4: конфликт при слиянии второй задачи разруливает арбитр."""

    def test_arbiter_resolves_conflict(self):
        a = self.scenario_task("A", route="fake-low")
        b = self.scenario_task("B", route="fake-low")
        self.write_swarm_config({"integration": GREEN_INTEGRATION, "arbiter": ARBITER_CMD})

        argv_file = self.tmp_path / "arbiter-argv.json"
        with mock.patch.dict(os.environ, {"LISTIK_SWARM_MODEL": "e2e-model"}):
            proc = self.start_swarm(parallel=2, interval=1,
                                    extra_env={"FAKE_SHARED": "1", "FAKE_ARBITER_MODE": "ok",
                                               "FAKE_ARBITER_ARGV_FILE": str(argv_file)})
            code = self.wait_swarm(proc, deadline=90)
        self.assertEqual(code, 0, self.swarm_log_tail(proc, 200))
        self.assertEqual(json.loads(argv_file.read_text(encoding="utf-8"))[2], "m/e2e-model")

        shared = self.git("show", f"main:shared.txt").splitlines()
        self.assertEqual(set(shared), {a["id"], b["id"]}, shared)

        arbiter_side = None
        clean_side = None
        for tid in (a["id"], b["id"]):
            merged = self.marked_records(tid, MERGED_MARK)
            self.assertEqual(len(merged), 1, (tid, merged))
            self.assertEqual(merged[0]["outside"], ["shared.txt"], (tid, merged[0]))
            if merged[0].get("arbiter"):
                arbiter_side = tid
            else:
                clean_side = tid
            self.assertFalse(self.row(tid)["needs_owner"], tid)

        self.assertIsNotNone(arbiter_side, "ни одна сторона не отмечена arbiter: true")
        self.assertIsNotNone(clean_side)
        arb_records = self.marked_records(arbiter_side, ARBITER_MARK)
        self.assertEqual(len(arb_records), 1, arb_records)
        self.assertEqual(arb_records[0]["files"], ["shared.txt"], arb_records[0])
        self.assertEqual(self.marked_records(clean_side, ARBITER_MARK), [])

        prompts = list(self.swarm_log_dir.glob(f"arbiter-{arbiter_side}-*.prompt.md"))
        self.assertTrue(prompts, list(self.swarm_log_dir.iterdir()))
        prompt_text = prompts[0].read_text(encoding="utf-8")
        self.assertIn(a["id"], prompt_text)
        self.assertIn(b["id"], prompt_text)

        self.assertEqual(self.halt_card_ids(), [])
        self.assertFalse(list((self.project_dir / ".worktrees").iterdir()))


# ------------------------------------------------------------------ сценарий 3 (п.5)

class RedIntegrationHaltThenResumeTests(SwarmBarrierE2ECase):
    """п.5: красная интеграция — стоп-карточка, после починки — продолжение."""

    def test_red_integration_halt_then_resume(self):
        a = self.scenario_task("A", route="fake-low")
        b = self.scenario_task("B", route="fake-low")
        c = self.scenario_task("C", route="fake-low")
        store.add_dep(self.conn, c["id"], a["id"], dep_type="blocks", created_by="dmitry")
        self.write_swarm_config({"integration": RED_INTEGRATION})

        proc1 = self.start_swarm(parallel=2, interval=1)
        code1 = self.wait_swarm(proc1, deadline=90)
        self.assertEqual(code1, 2, self.swarm_log_tail(proc1, 200))

        for tid in (a["id"], b["id"]):
            row = self.row(tid)
            self.assertEqual(row["status"], "done", tid)
            self.assertEqual(len(self.marked_records(tid, MERGED_MARK)), 1, tid)
            self.assertTrue(row["worktree"], tid)
            self.assertTrue(self.branch_exists(f"task/{tid}"), tid)

        halts = self.halt_card_ids()
        self.assertEqual(len(halts), 1, halts)
        h = halts[0]
        h_row = self.row(h)
        self.assertEqual(h_row["issue_type"], "question")
        self.assertIn(HALT_LABEL, json.loads(h_row["labels"] or "[]"))
        self.assertTrue(h_row["needs_owner"])
        questions = self.question_texts(h)
        self.assertEqual(len(questions), 1, questions)
        self.assertIn("boom", questions[0])
        self.assertIn("код: 1", questions[0])
        self.assertIn(sys.executable, questions[0])
        integration_logs = list(self.swarm_log_dir.glob("integration-p-*.log"))
        self.assertTrue(integration_logs, list(self.swarm_log_dir.iterdir()))
        self.assertTrue(any(str(p) in questions[0] for p in integration_logs), questions[0])

        soft = {link["id"] for link in deps_mod.soft_links(self.conn, h)
                if link["dep_type"] == "discovered-from"}
        self.assertTrue(soft, "нет связи discovered-from у стоп-карточки")
        self.assertTrue(soft <= {a["id"], b["id"]}, soft)

        row_c = self.row(c["id"])
        self.assertFalse(row_c["launched_by"], "C")
        self.assertEqual(row_c["generation"], 0, "C")

        log1 = "\n".join(self.swarm_log_lines(proc1))
        self.assertIn(f"стоп: карточка {h}", log1)
        self.assertIn(f"стоп {h}", log1)

        # Починка: swarm.json на зелёный, стоп-карточка закрыта не актором роя.
        self.write_swarm_config({"integration": GREEN_INTEGRATION})
        store.update_task(self.conn, h, status="done")
        self.conn.commit()

        proc2 = self.start_swarm(parallel=2, interval=1)
        code2 = self.wait_swarm(proc2, deadline=90)
        self.assertEqual(code2, 0, self.swarm_log_tail(proc2, 200))

        log2 = "\n".join(self.swarm_log_lines(proc2))
        self.assertIn("→ код 0", log2)
        self.assertLess(log2.index("→ код 0"), log2.index(f"запуск {c['id']}"))

        for tid in (a["id"], b["id"], c["id"]):
            row = self.row(tid)
            self.assertFalse(row["worktree"], tid)
            self.assertFalse(row["branch"], tid)

        c_records = self.marked_records(c["id"], MERGED_MARK)
        self.assertEqual(len(c_records), 1, c_records)
        self.assertTrue(self.is_ancestor(self.marked_records(a["id"], MERGED_MARK)[0]["sha"],
                                         c_records[0]["sha"]))
        self.assertTrue(self.is_ancestor(self.marked_records(b["id"], MERGED_MARK)[0]["sha"],
                                         c_records[0]["sha"]))

        self.assertEqual(self.halt_card_ids(), [])


# ------------------------------------------------------------------ сценарий 4 (п.6)

class UnfreezeAfterOwnerMergeTests(SwarmBarrierE2ECase):
    """п.6: разморозка открытой задачи после слияния владельца."""

    def test_unfreeze_after_owner_merge(self):
        a_task = self.make_scenario_task("A", route=None)
        b_task = self.make_scenario_task("B", route="fake-low")
        store.update_task(self.conn, b_task["id"], write_scope=[f"{b_task['id']}.txt"])
        self.close_task_manually(a_task["id"], port_label="port:5170")
        self.prepare_frozen_task(b_task["id"], owner_id=a_task["id"], port_label="port:5171")
        self.write_swarm_config({"integration": GREEN_INTEGRATION})

        proc = self.start_swarm(parallel=2, interval=1)
        code = self.wait_swarm(proc, deadline=90)
        self.assertEqual(code, 0, self.swarm_log_tail(proc, 200))

        b_id = b_task["id"]
        unfrozen = self.marked_records(b_id, UNFROZEN_MARK)
        self.assertEqual(len(unfrozen), 1, unfrozen)
        self.assertEqual(unfrozen[0]["owner"], a_task["id"], unfrozen[0])
        self.assertTrue(unfrozen[0]["rebased"], unfrozen[0])
        snapshot_sha = unfrozen[0]["snapshot"]
        self.assertTrue(snapshot_sha, unfrozen[0])
        # Коммит-снимок незакоммиченной правки (note.txt) -- обычный коммит, автор
        # `listik-swarm` (swarm/git.mjs:snapshotCommit), часть истории задачи. Ребейз
        # переписывает его sha (родитель меняется), поэтому в `main` ищем по автору и
        # сообщению, а не по исходному `snapshot_sha` из записи разморозки.
        snapshot_log = self.git("log", "main", "--format=%H%x09%an%x09%s")
        snapshot_lines = [line.split("\t") for line in snapshot_log.splitlines() if line]
        snapshot_commits = [sha for sha, author, subject in snapshot_lines
                            if author == "listik-swarm" and
                            subject == "рой: снимок незакоммиченных правок перед rebase"]
        self.assertEqual(len(snapshot_commits), 1, snapshot_log)

        row_b = self.row(b_id)
        labels = json.loads(row_b["labels"] or "[]")
        self.assertFalse(any(l.startswith("frozen-by:") for l in labels), labels)
        self.assertEqual(row_b["status"], "done")
        self.assertEqual(row_b["generation"], 1)

        content_a = self.git("show", f"main:{a_task['id']}.txt").strip()
        self.assertEqual(content_a, a_task["id"])
        content_b = self.git("show", f"main:{b_id}.txt").strip()
        self.assertEqual(content_b, "5171")
        self.assertIn("незакоммиченная правка", self.git("show", "main:note.txt"))

        history = self.commit_subjects("main")
        self.assertEqual(self.git("log", "main", "--merges", "--format=%s"), "")
        self.assertTrue(len(history) >= 3, history)

        self.assertEqual(len(self.marked_records(a_task["id"], MERGED_MARK)), 1)
        self.assertEqual(len(self.marked_records(b_id, MERGED_MARK)), 1)
        self.assertFalse(list((self.project_dir / ".worktrees").iterdir()))

        log_text = "\n".join(self.swarm_log_lines(proc))
        self.assertLess(log_text.index(f"разморожена {b_id} (владелец {a_task['id']}): rebase чистый"),
                        log_text.index(f"запуск {b_id}"))


# ------------------------------------------------------------------ сценарий 5 (п.7)

class UnfreezeConflictThenArbiterOnMergeTests(SwarmBarrierE2ECase):
    """п.7: разморозка с конфликтом ребейза -- исполнитель правит сам, а конфликт
    при финальном слиянии закрытой задачи разруливает арбитр."""

    def test_unfreeze_conflict_then_arbiter_on_merge(self):
        a_task = self.make_scenario_task("A", route=None)
        b_task = self.make_scenario_task("B", route="fake-low")
        store.update_task(self.conn, b_task["id"], write_scope=[f"{b_task['id']}.txt"])

        info_a = worktree_mod.ensure(str(self.project_dir), a_task["id"])
        tree_a = Path(info_a["path"])
        (tree_a / "README.md").write_text("A\n", encoding="utf-8")
        self.git("add", "-A", cwd=tree_a)
        self.git("commit", "-q", "-m", a_task["id"], cwd=tree_a)
        store.update_task(self.conn, a_task["id"], worktree=info_a["path"], branch=info_a["branch"],
                          labels=["port:5170"], write_scope=[f"{a_task['id']}.txt"])
        store.update_task(self.conn, a_task["id"], status="done")
        self.conn.commit()

        info_b = worktree_mod.ensure(str(self.project_dir), b_task["id"])
        tree_b = Path(info_b["path"])
        (tree_b / "README.md").write_text("B\n", encoding="utf-8")
        row_b = self.row(b_task["id"])
        labels_b = json.loads(row_b["labels"] or "[]") + ["port:5171", f"frozen-by:{a_task['id']}"]
        store.update_task(self.conn, b_task["id"], worktree=info_b["path"], branch=info_b["branch"],
                          labels=labels_b)
        self.conn.commit()

        self.write_swarm_config({"integration": GREEN_INTEGRATION, "arbiter": ARBITER_CMD})

        proc = self.start_swarm(parallel=2, interval=1, extra_env={"FAKE_ARBITER_MODE": "ok"})
        code = self.wait_swarm(proc, deadline=90)
        self.assertEqual(code, 0, self.swarm_log_tail(proc, 200))

        b_id = b_task["id"]
        unfrozen = self.marked_records(b_id, UNFROZEN_MARK)
        self.assertEqual(len(unfrozen), 1, unfrozen)
        self.assertFalse(unfrozen[0]["rebased"], unfrozen[0])
        self.assertEqual(unfrozen[0]["conflicts"], ["README.md"], unfrozen[0])

        row_b = self.row(b_id)
        self.assertEqual(row_b["status"], "done")
        self.assertEqual(row_b["generation"], 1)

        arb_records = self.marked_records(b_id, ARBITER_MARK)
        self.assertEqual(len(arb_records), 1, arb_records)
        merged_records = self.marked_records(b_id, MERGED_MARK)
        self.assertEqual(len(merged_records), 1, merged_records)

        readme = self.git("show", "main:README.md")
        self.assertIn("A", readme)
        self.assertIn("B", readme)

        self.assertEqual(self.halt_card_ids(), [])
        self.assertFalse(list((self.project_dir / ".worktrees").iterdir()))


# ------------------------------------------------------------------ сценарий 6 (п.8)

class DirtyClosedTreeGatesWithoutHaltTests(SwarmBarrierE2ECase):
    """п.8: грязное дерево закрытой задачи -- гейт запусков без карточки-стоп."""

    def test_dirty_closed_tree_gates_without_halt(self):
        a_task = self.make_scenario_task("A", route=None)
        c_task = self.scenario_task("C", route="fake-low")
        store.add_dep(self.conn, c_task["id"], a_task["id"], dep_type="blocks", created_by="dmitry")
        self.close_task_manually(a_task["id"], port_label="port:5170", dirty=True)
        self.write_swarm_config({"integration": GREEN_INTEGRATION})

        start_head = self.head_sha("main")

        proc = self.start_swarm(parallel=2, interval=1)
        code = self.wait_swarm(proc, deadline=90)
        self.assertEqual(code, 2, self.swarm_log_tail(proc, 200))

        row_a = self.row(a_task["id"])
        self.assertTrue(row_a["needs_owner"])
        questions = self.question_texts(a_task["id"])
        self.assertEqual(len(questions), 1, questions)
        self.assertIn("рой: не влита — в дереве", questions[0])

        self.assertEqual(self.head_sha("main"), start_head)

        row_c = self.row(c_task["id"])
        self.assertEqual(row_c["generation"], 0)
        self.assertFalse(row_c["launched_by"])

        log_text = "\n".join(self.swarm_log_lines(proc))
        self.assertIn("не влиты 1", log_text)
        self.assertEqual(self.halt_card_ids(), [])


if __name__ == "__main__":
    unittest.main()

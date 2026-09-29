#!/usr/bin/env python3
"""Стенд судей: собирает деревья кейсов с подложенными дефектами и сводит отчёты судей.

`prepare` — по отдельному git-репозиторию на пару (судья, кейс): база, реализация порции и
мутация незакоммичены поверх одного коммита, бумаги шага и пакет диффа — как у настоящего этапа
приёмки. `summarize` — таблица вердиктов по `runs/<судья>/<кейс>.md`. Регламент — README.md рядом.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = (HERE / "../../skills/low-pipeline/SKILL.md").resolve()
CORE = (HERE / "../pipeline-core.md").resolve()

TASK_ANCHOR = "Задание, которое уходит в `/grok:delegate`:"
TASK_TAIL = "Listik, карточка"
DIFF_HEAD = "## Пакет диффа для приёмки"
STEPS_REL = "docs/specs/steps"
NAME_RE = re.compile(r"case-\d{2}")
JUDGE_RE = re.compile(r"[a-z0-9.-]+")
PROBE_TIMEOUT = 300
TESTS_TIMEOUT = 1200

GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "judge-bench", "GIT_AUTHOR_EMAIL": "judge-bench@localhost",
    "GIT_COMMITTER_NAME": "judge-bench", "GIT_COMMITTER_EMAIL": "judge-bench@localhost",
}

BENCH_TAIL = """Стенд проверки судей: карточки Listik нет — в Listik ничего не пиши и установленный `listik`
не вызывай; `bin/listik` из дерева запускай только с `LISTIK_HOME` во временном каталоге.
Читай и запускай только внутри дерева порции.
Дерево порции (рабочий каталог: там гоняешь чек-лист и коммитишь): {tree}
Чек-лист приёмки: {tree}/{steps}/{step}.check-{portion}.md
Файл порции: {tree}/{steps}/{step}.{portion}.md
Пакет диффа (заход r1): {tree}/.git/feature-pipeline/{step}.diff-{portion}.r1.txt
"""


class Fail(Exception):
    """Кейс не готов; текст — причина из закрытого списка."""


def git(*args, cwd=None, input=None, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, input=input, env=GIT_ENV, capture_output=True)
    if check and r.returncode:
        raise subprocess.CalledProcessError(r.returncode, r.args, r.stdout, r.stderr)
    return r


def fenced_after(lines, start):
    """Строки первой ограды ``` после индекса start или None."""
    inside = None
    for line in lines[start:]:
        if line.startswith("```"):
            if inside is not None:
                return inside
            inside = []
        elif inside is not None:
            inside.append(line)
    return None


def judge_task_head():
    """Задание судьи из low-pipeline без хвоста про карточку Listik; None — не вынулось."""
    try:
        lines = SKILL.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    idx = next((i for i, s in enumerate(lines) if s.startswith(TASK_ANCHOR)), None)
    if idx is None:
        return None
    body = fenced_after(lines, idx + 1)
    if body is None:
        return None
    tail = next((i for i, s in enumerate(body) if s.startswith(TASK_TAIL)), None)
    if tail is None:
        return None
    return "\n".join(body[:tail]).rstrip()


def diff_block():
    """Bash-блок раздела «Пакет диффа для приёмки» с `add -A -N` (как core_block() в тестах)."""
    try:
        text = CORE.read_text(encoding="utf-8")
    except OSError:
        return None
    if DIFF_HEAD not in text:
        return None
    inside = None
    for line in text.split(DIFF_HEAD, 1)[1].splitlines():
        if line.startswith("```"):
            if inside is None:
                inside = []
            else:
                if any("add -A -N" in s for s in inside):
                    return "\n".join(inside)
                inside = None
        elif inside is not None:
            inside.append(line)
    return None


def run_env(home):
    env = {k: v for k, v in GIT_ENV.items() if k not in ("LISTIK_DB", "LISTIK_CONFIG")}
    env["LISTIK_HOME"] = home
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # __pycache__ не попадает в дерево и пакет диффа
    return env


def run_isolated(argv, tree, timeout):
    """Запуск в дереве со свежим LISTIK_HOME; None — таймаут."""
    with tempfile.TemporaryDirectory(prefix="judge-bench-home-") as home:
        env = run_env(home)
        env["PYTHONPATH"] = str(tree)
        try:
            return subprocess.run(argv, cwd=tree, env=env, capture_output=True, text=True,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            return None


def probe(path, tree):
    """'верно' | 'дефект' | 'ошибка пробы' | 'таймаут' — по контракту пробы."""
    r = run_isolated([sys.executable, str(path)], tree, PROBE_TIMEOUT)
    if r is None:
        return "таймаут"
    one_line = len([s for s in r.stdout.splitlines() if s.strip()]) == 1
    if r.returncode == 0 and one_line:
        return "верно"
    if r.returncode == 1 and one_line and "Traceback" not in r.stderr:
        return "дефект"
    return "ошибка пробы"


def papers_of(case):
    step, portion = case["step"], case["portion"]
    return [f"{step}.md", f"{step}.{portion}.md", f"{step}.check-{portion}.md"]


def build(case, case_dir, tree, repo, task_head, block, no_mutation):
    """Шаги 1–12 требования 2; Fail — причина провала."""
    name, commit = case["name"], case["commit"]
    if not NAME_RE.fullmatch(name):
        raise Fail("имя кейса не нейтральное")
    for f in papers_of(case):
        if not (case_dir / "papers" / f).is_file():
            raise Fail(f"нет бумаг {f}")
    mutation, probe_py = case_dir / "mutation.patch", case_dir / "probe.py"
    if mutation.exists() != probe_py.exists():
        raise Fail("мутация без пробы")
    if os.path.lexists(tree):
        raise Fail(f"дерево уже есть {tree}")

    # 4. Отдельный репозиторий: архив базы, один коммит на main.
    archive = git("-C", str(repo), "archive", "--format=tar", f"{commit}^").stdout
    tree.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(tree, filter="data")
    git("init", "-q", "-b", "main", cwd=tree)
    git("add", "-A", "-f", cwd=tree)
    git("commit", "-q", "--allow-empty", "--no-verify", "-m", "база", cwd=tree)

    # 5–6. Исключения и node_modules главного дерева.
    with open(tree / ".git/info/exclude", "a", encoding="utf-8") as fh:
        fh.write("\ndocs/specs/\nweb/node_modules\n")
    common = git("-C", str(repo), "rev-parse", "--path-format=absolute",
                 "--git-common-dir").stdout.decode().strip()
    modules = Path(common).parent / "web/node_modules"
    if modules.exists():
        (tree / "web").mkdir(exist_ok=True)
        (tree / "web/node_modules").symlink_to(modules)

    # 7. Реализация незакоммиченной.
    impl = git("-C", str(repo), "diff", "--binary", f"{commit}^", commit).stdout
    if not impl.strip():
        raise Fail("реализация пуста")
    if git("apply", "-", cwd=tree, input=impl, check=False).returncode:
        raise Fail("реализация не накладывается")

    # 8–9. Проба на реализации, мутация, проба на мутации.
    if probe_py.exists() and not no_mutation:
        seen = probe(probe_py, tree)
        if seen == "дефект":
            raise Fail("проба красная на реализации")
        if seen != "верно":
            raise Fail(seen)
        if git("apply", str(mutation), cwd=tree, check=False).returncode:
            raise Fail("мутация не накладывается")
        seen = probe(probe_py, tree)
        if seen == "верно":
            raise Fail("проба не видит дефекта")
        if seen != "дефект":
            raise Fail(seen)

    # 10. Тесты порции.
    r = run_isolated([sys.executable, "-m", "unittest", *case["tests"]], tree, TESTS_TIMEOUT)
    if r is None:
        raise Fail("таймаут")
    if r.returncode:
        raise Fail("тесты порции красные")

    # 11. Бумаги шага.
    steps = tree / STEPS_REL
    steps.mkdir(parents=True, exist_ok=True)
    for f in papers_of(case):
        text = (case_dir / "papers" / f).read_text(encoding="utf-8")
        (steps / f).write_text(text.replace("{{TREE}}", str(tree)), encoding="utf-8")

    # 12. Пакет диффа — блок pipeline-core.md как есть.
    script = block
    for k, v in {"<steps>": STEPS_REL, "<трек>": case["step"], "<X>": case["portion"],
                 "<R>": "1"}.items():
        script = script.replace(k, v)
    r = subprocess.run(["bash", "-c", script], cwd=tree, env=GIT_ENV, capture_output=True)
    dump = tree / f".git/feature-pipeline/{case['step']}.diff-{case['portion']}.r1.txt"
    if r.returncode or not dump.is_file():
        raise Fail("пакет диффа пуст")

    return task_head + "\n\n" + BENCH_TAIL.format(
        tree=tree, steps=STEPS_REL, step=case["step"], portion=case["portion"])


def judge_label(value):
    if not JUDGE_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"метка судьи — [a-z0-9.-]+, не {value!r}")
    return value


def load_cases(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_prepare(args, parser):
    if not shutil.which("bash"):
        parser.exit(2, "нет bash\n")
    task_head = judge_task_head()
    if task_head is None:
        parser.exit(2, f"нет задания судьи в {SKILL}\n")
    block = diff_block()
    if block is None:
        parser.exit(2, f"нет блока пакета диффа в {CORE}\n")
    if args.repo:
        repo = Path(args.repo).resolve()
    else:
        repo = Path(git("-C", str(HERE), "rev-parse", "--show-toplevel").stdout.decode().strip())
    cases_path = Path(args.cases).resolve()
    cases = load_cases(cases_path)
    by_name = {c["name"]: c for c in cases}
    wanted = args.case or [c["name"] for c in cases]
    out = Path(args.out).resolve() / args.judge
    runs = Path(args.runs).resolve() / args.judge
    ok = True
    for name in wanted:
        case = by_name.get(name)
        if case is None:
            print(f"{name}: нет такого кейса", flush=True)
            ok = False
            continue
        tree = out / name
        try:
            task = build(case, cases_path.parent / "cases" / name, tree, repo, task_head, block,
                         args.no_mutation)
        except Fail as e:
            print(f"{name}: {e}", flush=True)
            ok = False
            continue
        task_file = out / f"{name}.task.md"
        task_file.write_text(task, encoding="utf-8")
        mark = " (без мутации)" if args.no_mutation else ""
        print(f"{name}: готов{mark}\nдерево: {tree}\nзадание: {task_file}\n"
              f"отчёт: {runs / (name + '.md')}\n---\n{task}---", flush=True)
    return 0 if ok else 1


VERDICT_STRIP = re.compile(r"[*_#\s]")
# Первая строка, склеенная с комментариями харнесса (вывод /grok:result): вердикт — в её конце,
# в начале строки или сразу после знака конца фразы, дальше только пробелы и необязательный хеш.
VERDICT_TAIL = re.compile(r"(?:^|(?<=[.!?…)`]))(зелёный|красный)(?:\s+[0-9a-f]{7,40})?\s*$",
                          re.IGNORECASE)


def parse_report(path):
    """(вердикт, секунд, ответ) из отчёта; файла нет — ('нет отчёта', '?', '')."""
    if not path.is_file():
        return "нет отчёта", "?", ""
    lines = path.read_text(encoding="utf-8").splitlines()
    cut = next((i for i, s in enumerate(lines) if s.strip() == "---"), len(lines))
    head, body = lines[:cut], lines[cut + 1:]
    seconds = "?"
    for s in head:
        key, _, val = s.partition(":")
        if key.strip() == "секунд" and re.fullmatch(r"\d+", val.strip()):
            seconds = val.strip()
    verdict = "нет вердикта"
    first = next((s for s in body if s.strip()), None)
    if first is not None:
        v = VERDICT_STRIP.sub("", first).lower()
        if v.startswith("вердикт:"):
            v = v[len("вердикт:"):]
        if v.startswith("зелёный"):
            verdict = "зелёный"
        elif v.startswith("красный"):
            verdict = "красный"
        elif m := VERDICT_TAIL.search(re.sub(r"[*_#]", "", first).strip()):
            verdict = m.group(1).lower()
    return verdict, seconds, "\n".join(body)


def cmd_summarize(args):
    cases = load_cases(args.cases)
    runs = Path(args.runs)
    print("| судья | кейс | класс | ожидается | вердикт | секунд | файл дефекта назван |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    judges = sorted(p.name for p in runs.iterdir() if p.is_dir()) if runs.is_dir() else []
    if not judges:
        print("отчётов нет")
        return 0
    names = {c["name"] for c in cases}
    bad, good = [c for c in cases if c.get("defect")], [c for c in cases if not c.get("defect")]
    totals, extra = [], []
    for judge in judges:
        red_bad = red_good = 0
        secs = []
        for c in cases:
            verdict, seconds, body = parse_report(runs / judge / f"{c['name']}.md")
            expect = "красный" if c.get("defect") else "зелёный"
            named = ("да" if c["defect_path"] in body else "нет") if c.get("defect") else "—"
            if verdict == "красный":
                if c.get("defect"):
                    red_bad += 1
                else:
                    red_good += 1
            if seconds != "?":
                secs.append(int(seconds))
            print(f"| {judge} | {c['name']} | {c['class']} | {expect} | {verdict} | {seconds} "
                  f"| {named} |")
        med = f"{statistics.median(secs):g}" if secs else "?"
        totals.append(f"{judge}: красный на дефектах {red_bad} из {len(bad)}, ложный красный на "
                      f"чистых {red_good} из {len(good)}, медиана {med} с")
        extra += [p for p in sorted((runs / judge).glob("*.md")) if p.stem not in names]
    print()
    for line in totals:
        print(line)
    for p in extra:
        print(f"лишний отчёт: {p}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cases", default=str(HERE / "cases.json"),
                        help="путь к cases.json; каталог кейсов — cases/ рядом с ним")
    common.add_argument("--runs", default=str(HERE / "runs"), help="каталог отчётов судей")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", parents=[common], help="собрать деревья кейсов для судьи")
    p.add_argument("--judge", required=True, type=judge_label, help="метка судьи, [a-z0-9.-]+")
    p.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "listik-judge-bench"),
                   help="куда класть деревья")
    p.add_argument("--repo", help="исходный git-репозиторий (по умолчанию — репозиторий скрипта)")
    p.add_argument("--no-mutation", action="store_true",
                   help="служебно, для подбора мутации: без мутации и пробы")
    p.add_argument("case", nargs="*", help="имена кейсов (по умолчанию все)")
    sub.add_parser("summarize", parents=[common], help="таблица вердиктов по runs/")
    args = parser.parse_args(argv)
    if args.cmd == "prepare":
        return cmd_prepare(args, parser)
    return cmd_summarize(args)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""SVG-схемы пресетов конвейера для README плагинов `pipeline-*`.

Каждая запись `routes.json` с `kind == "pipeline"` и непустым `plugin` даёт файл
`plugins/<plugin>/docs/<skill>.svg`: этапы слева направо столбцами из раскладки ролей `roles`.

    python3 scripts/pipeline_schemes.py           # записать схемы
    python3 scripts/pipeline_schemes.py --check   # сверить с тем, что лежит; 1 — есть расхождения
    python3 scripts/pipeline_schemes.py --root <каталог>
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys
from xml.sax.saxutils import escape, quoteattr

ROOT = pathlib.Path(__file__).resolve().parent.parent

FONT = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
INK, GREY, LINE = "#111", "#6b6b6b", "#c8c8c8"
MARGIN, TOP = 24, 76            # поле слева/справа/снизу, верх первого узла
NODE_H, NODE_GAP = 44, 16       # высота узла (заголовок + подпись), зазор в столбце
COL_GAP, COL_PAD = 56, 12       # зазор между столбцами под стрелки, поле внутри столбца
HEAD_CH, CAPTION_CH = 8.5, 7.5  # оценка ширины символа заголовка и подписи, px
LENSES = ("Линза scope", "Линза holes", "Линза intent")
LOOP_LABEL = "красный вердикт"


def role_caption(label: str, title: str) -> str:
    """Подпись роли: `title`, а `label` дописывается, если его нет в `title` целым словом (как `effortOf`)."""
    if label and label.lower() not in re.split(r"[\s·—\-:,]+", title.lower()):
        return f"{title} · {label}"
    return title


def skill_of(route: dict) -> str:
    """Ключ маршрута без префикса плагина: `full-high` у `pipeline-full` → `high`."""
    prefix = route["plugin"].removeprefix("pipeline-") + "-"
    return route["key"].removeprefix(prefix)


def columns_of(roles: dict) -> list[list[tuple[str, str]]]:
    """Столбцы узлов `(заголовок, подпись)` слева направо; последний — итог «Коммит порции»."""
    cols: list[list[tuple[str, str]]] = []
    if "spec" in roles:
        cols.append([("ТЗ", role_caption(roles["spec"]["label"], roles["spec"]["title"]))])
    if "critic" in roles:
        cols.append([("Критик", part) for part in roles["critic"]["title"].split(" + ")])
    cols.append([("Код", role_caption(roles["impl"]["label"], roles["impl"]["title"]))])
    judge = roles.get("judge")
    if judge is None:
        who = "исполнитель сам"
    else:
        left, arrow, right = judge["title"].partition(" → ")
        if arrow and "линз" in left:
            model = left.split(" · ")[0]
            cols.append([(lens, model) for lens in LENSES])
            cols.append([("Судья по находке", right)])
            who = "оркестратор при чистых линзах, иначе судья"
        else:
            cols.append([("Приёмка", role_caption(judge["label"], judge["title"]))])
            who = "судья при зелёном вердикте"
    cols.append([("Коммит порции", who)])
    return cols


def _text(x: int, y: int, body: str, *, head: bool, anchor: str = "middle") -> str:
    style = f'font-size="15" font-weight="700" fill="{INK}"' if head else f'font-size="12" fill="{GREY}"'
    return f'  <text x="{x}" y="{y}" text-anchor="{anchor}" {style}>{escape(body)}</text>'


def render(route: dict) -> str:
    """SVG одной записи конвейера; одинаковый вход — одинаковая строка."""
    name = f"{route['plugin']}:{skill_of(route)}"
    subtitle = f"{route['title']} · {route['hint']}"
    cols = columns_of(route["roles"])
    widths = [math.ceil(max(max(len(h) * HEAD_CH, len(c) * CAPTION_CH) for h, c in col)) + 2 * COL_PAD
              for col in cols]
    heights = [len(col) * NODE_H + (len(col) - 1) * NODE_GAP for col in cols]
    content_h = max(heights)
    center = TOP + content_h // 2

    xs, x = [], MARGIN
    for w in widths:
        xs.append(x)
        x += w + COL_GAP
    width = max(x - COL_GAP + MARGIN, math.ceil(max(len(name) * 9.5, len(subtitle) * CAPTION_CH)) + 2 * MARGIN)

    # верх каждого узла: столбец центрирован по вертикали
    tops = [[center - h // 2 + i * (NODE_H + NODE_GAP) for i in range(len(col))] for col, h in zip(cols, heights)]

    body: list[str] = []
    for c in range(len(cols) - 1):  # стрелки: от каждого узла столбца к каждому узлу следующего
        x1, x2 = xs[c] + widths[c], xs[c + 1]
        for t1 in tops[c]:
            for t2 in tops[c + 1]:
                body.append(f'  <line x1="{x1}" y1="{t1 + 22}" x2="{x2}" y2="{t2 + 22}" '
                            f'stroke="{LINE}" stroke-width="1.2" marker-end="url(#arrow)"/>')
    for c, col in enumerate(cols):
        cx = xs[c] + widths[c] // 2
        for (head, caption), top in zip(col, tops[c]):
            if head == "Коммит порции":
                body.append(f'  <rect x="{xs[c]}" y="{top - 2}" width="{widths[c]}" height="{NODE_H + 2}" rx="8" '
                            f'fill="none" stroke="{INK}" stroke-width="1"/>')
            body.append(_text(cx, top + 18, head, head=True))
            body.append(_text(cx, top + 36, caption, head=False))

    bottom = TOP + content_h
    heads = [[h for h, _ in col] for col in cols]
    judge_col = next((c for c, hs in enumerate(heads) if hs[0] in ("Приёмка", "Судья по находке")), None)
    if judge_col is not None:  # петля «красный вердикт»: дуга под схемой от приёмки назад к коду
        code_col = next(c for c, hs in enumerate(heads) if hs[0] == "Код")
        jx, kx = xs[judge_col] + widths[judge_col] // 2, xs[code_col] + widths[code_col] // 2
        y0 = center + NODE_H // 2 + 4
        ctrl = bottom + 48
        low = (y0 + 3 * ctrl) // 4  # нижняя точка кубической дуги с обеими опорами на ctrl
        body.append(f'  <path d="M{jx} {y0} C{jx} {ctrl} {kx} {ctrl} {kx} {y0}" fill="none" stroke="{LINE}" '
                    f'stroke-width="1.2" stroke-dasharray="5 4" marker-end="url(#arrow)"/>')
        body.append(_text((jx + kx) // 2, low + 18, LOOP_LABEL, head=False))
        bottom = low + 18
    height = bottom + MARGIN

    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family={quoteattr(FONT)}>',
        f"  <title>{escape(name)}</title>",
        "  <defs>",
        '    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        'orient="auto-start-reverse">',
        f'      <path d="M0 0L10 5L0 10z" fill="{LINE}"/>',
        "    </marker>",
        "  </defs>",
        '  <rect width="100%" height="100%" fill="#fff"/>',
        _text(MARGIN, 32, name, head=True, anchor="start"),
        _text(MARGIN, 54, subtitle, head=False, anchor="start"),
        *body,
        "</svg>",
        "",
    ])


def expected(root: pathlib.Path) -> dict[str, bytes]:
    """Путь от корня → байты SVG для каждой записи конвейера с `plugin`, в порядке `routes.json`."""
    routes = json.loads((root / "routes.json").read_text(encoding="utf-8"))["routes"]
    return {f"plugins/{r['plugin']}/docs/{skill_of(r)}.svg": render(r).encode("utf-8")
            for r in routes if r.get("kind") == "pipeline" and r.get("plugin")}


def check(root: pathlib.Path) -> list[str]:
    """Строки расхождений: отсутствующий, отличающийся или лишний файл; пусто — всё совпадает."""
    want = expected(root)
    problems = []
    for rel, data in want.items():
        path = root / rel
        if not path.is_file():
            problems.append(f"{rel}: нет файла")
        elif path.read_bytes() != data:
            problems.append(f"{rel}: отличается от сгенерированного")
    for path in sorted(root.glob("plugins/pipeline-*/docs/*.svg")):
        rel = path.relative_to(root).as_posix()
        if rel not in want:
            problems.append(f"{rel}: лишний файл, записи в routes.json нет")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="ничего не писать, сверить файлы")
    parser.add_argument("--root", type=pathlib.Path, default=ROOT, help="корень с routes.json и plugins/")
    args = parser.parse_args(argv)
    if args.check:
        problems = check(args.root)
        for line in problems:
            print(line, file=sys.stderr)
        return 1 if problems else 0
    for rel, data in expected(args.root).items():
        path = args.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Снимок рейтингов Artificial Analysis без API-ключа.

Любая страница https://artificialanalysis.ai/models/<slug> встраивает в RSC-пейлоад
(self.__next_f.push) ПОЛНЫЙ каталог моделей — все варианты effort, цены, бенчмарки,
скорость. Страница /models отдаёт только ~26 моделей, поэтому ходим на карточку модели.

Использование:
    python3 fetch_aa.py                 # снимок в models.json + models.csv
    python3 fetch_aa.py --slug grok-4-6 # взять пейлоад с другой карточки
"""
import argparse, csv, json, re, sys, urllib.request
from datetime import date

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"

def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")

def flight(html: str) -> str:
    chunks = re.findall(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', html, re.S)
    return "".join(json.loads(c) for c in chunks)

def objects(buf: str, key: str = '"intelligenceIndex"'):
    """Вытаскивает сбалансированные JSON-объекты, содержащие ключ."""
    out = {}
    for m in re.finditer(re.escape(key), buf):
        i, depth, start = m.start(), 0, None
        while i >= 0:                       # назад до открывающей скобки объекта
            c = buf[i]
            if c == "}":
                depth += 1
            elif c == "{":
                if depth == 0:
                    start = i
                    break
                depth -= 1
            i -= 1
        if start is None:
            continue
        depth, k, instr, esc = 0, start, False, False
        while k < len(buf):                 # вперёд до парной закрывающей
            c = buf[k]
            if instr:
                if esc: esc = False
                elif c == "\\": esc = True
                elif c == '"': instr = False
            elif c == '"': instr = True
            elif c == "{": depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        o = json.loads(buf[start:k + 1])
                        out[o.get("slug") or o.get("id")] = o
                    except Exception:
                        pass
                    break
            k += 1
    return list(out.values())

def _perf(m, key):
    """Скорость/задержки лежат в performanceByPromptType, срез medium (fallback: timescaleData)."""
    p = m.get("performanceByPromptType") or {}
    for bucket in ("medium", "long", "hundredK"):
        v = (p.get(bucket) or {}).get(key)
        if v is not None:
            return round(v, 2)
    v = (m.get("timescaleData") or {}).get(key)
    return round(v, 2) if v is not None else None


COLS = [
    ("slug", lambda m: m.get("slug")),
    ("name", lambda m: m.get("shortName") or m.get("name")),
    ("creator", lambda m: (m.get("creator") or {}).get("name")),
    ("effort", lambda m: (m.get("effort") or {}).get("level")),
    ("intelligence", lambda m: m.get("intelligenceIndex")),
    ("in_$1m", lambda m: m.get("price1mInputTokens")),
    ("out_$1m", lambda m: m.get("price1mOutputTokens")),
    ("cache_hit_$1m", lambda m: m.get("cacheHitPrice")),
    ("blended_3to1", lambda m: m.get("price1mBlended0To3To1")),
    ("speed_tok_s", lambda m: _perf(m, "medianOutputSpeed")),
    ("ttft_s", lambda m: _perf(m, "medianTimeToFirstChunk")),
    ("e2e_s", lambda m: _perf(m, "medianEndToEndResponseTime")),
    ("ctx", lambda m: m.get("contextWindowTokens")),
    ("reasoning", lambda m: m.get("isReasoning")),
    ("open_weights", lambda m: m.get("isOpenWeights")),
    ("released", lambda m: m.get("releaseDate")),
    ("deprecated", lambda m: m.get("deprecated")),
    # агентные/кодовые бенчи — то, что важно для пайплайна разработки
    ("terminalbench_v40", lambda m: m.get("terminalbenchV40")),
    ("terminalbench_v21", lambda m: m.get("terminalbenchV21")),
    ("scicode", lambda m: m.get("scicode")),
    ("livecodebench", lambda m: m.get("livecodebench")),
    ("lcr", lambda m: m.get("lcr")),
    ("tau2", lambda m: m.get("tau2")),
    ("gpqa", lambda m: m.get("gpqa")),
    ("hle", lambda m: m.get("hle")),
    ("aime25", lambda m: m.get("aime25")),
    ("omniscience", lambda m: m.get("omniscience")),
    # $ на одну задачу Intelligence Index — реальная «цена интеллекта», а не цена токена
    ("cost_per_task_$", lambda m: round(v, 3) if (v := ((m.get("intelligenceIndexCostPerTask") or {}).get("cost") or {}).get("total")) is not None else None),
    # ВНИМАНИЕ: intelligenceIndexCost.* — это ДОЛЛАРЫ за полный прогон индекса, не токены.
    # Проверено тождеством cost.output == canonicalIntelligenceIndexTokenCount.output * price1mOutputTokens/1e6
    # (сходится на 126 моделях из 132; исключение — линейка Fable, ~2%, похоже на смену цены внутри снимка).
    ("index_run_cost_$", lambda m: (m.get("intelligenceIndexCost") or {}).get("total")),
    # Настоящие токены на задачу — вот это:
    ("out_tok_task", lambda m: round(v) if (v := (m.get("intelligenceIndexOutputTokensPerTask") or {}).get("output")) else None),
    ("reason_tok_task", lambda m: round(v) if (v := (m.get("intelligenceIndexOutputTokensPerTask") or {}).get("reasoning")) else None),
    ("sec_per_task", lambda m: m.get("intelligenceIndexTimePerTask")),
]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="claude-opus-5", help="с чьей карточки тянуть пейлоад")
    ap.add_argument("--out", default=None, help="префикс файлов (по умолчанию рядом со скриптом)")
    a = ap.parse_args()

    import os
    base = a.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    html = fetch(f"https://artificialanalysis.ai/models/{a.slug}")
    models = objects(flight(html))
    if len(models) < 100:
        sys.exit(f"подозрительно мало моделей ({len(models)}) — вёрстка сайта могла поменяться")

    models.sort(key=lambda m: (m.get("intelligenceIndex") or -1), reverse=True)
    snap = {"source": f"https://artificialanalysis.ai/models/{a.slug}",
            "fetched": date.today().isoformat(), "count": len(models), "models": models}
    with open(base + ".json", "w") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    with open(base + ".csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([c for c, _ in COLS])
        for m in models:
            w.writerow([g(m) for _, g in COLS])
    print(f"{len(models)} моделей → {base}.json, {base}.csv")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Расчёт пресетов пайплайна из снимка Artificial Analysis.

Все числа в ROLES.md порождаются этим скриптом, а не вписаны руками.
Перегенерировать таблицы:  python3 presets.py
Проверить целостность:     python3 presets.py --check
"""
import argparse, csv, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = json.load(open(os.path.join(HERE, "models.json")))
D = {m["slug"]: m for m in SNAP["models"]}
R = {r["slug"]: r for r in csv.DictReader(open(os.path.join(HERE, "models.csv")))}

# Вендоры, доступные для разработки (харнессы: Codex, dsh, grok CLI, Claude Code, GLM)
VENDORS = {"Cognition", "OpenAI", "Anthropic", "DeepSeek", "SpaceXAI", "Z AI"}
# Всё от Anthropic идёт по подписке Claude Max x20 → внешняя цена 0, расходуется квота
SUBSCRIPTION = "Anthropic"
EFFORTS = {"low", "medium", "high", "xhigh", "max"}

PRESETS = {
    "xhigh":  dict(spec="claude-opus-5-5-xhigh", critic="glm-5-3-flash",
                   dev="claude-opus-5-5-xhigh", judge="grok-4-7"),
    "high":   dict(spec="claude-opus-5-5-high", critic="glm-5-3-flash",
                   dev="claude-opus-5-5-high", judge="grok-4-7"),
    "medium": dict(spec="claude-opus-5-5-medium", critic="glm-5-3-flash",
                   dev="claude-opus-5-5-medium", judge="grok-4-7-high"),
    "low":    dict(spec="claude-opus-5-5-low", critic="glm-5-3-flash",
                   dev="swe-2-max", judge="grok-4-7-high"),
    "xlow":   dict(dev="swe-2-max", judge="grok-4-7-high"),
    "nano":   dict(dev="swe-2-high", judge="glm-5-3-flash"),
}
# ponytail: SWE-2 (devin) в AA не замерен — заглушка без метрик, все числа по нему «-»
UNRATED = {"swe-2-max", "swe-2-high"}
for _s in UNRATED:
    D.setdefault(_s, {"slug": _s, "creator": {"name": "Cognition"}})
ROLES = [("spec", "Писатель ТЗ"), ("critic", "Критик"), ("dev", "Разработчик"), ("judge", "Судья")]

def num(slug, col):
    v = (R.get(slug) or {}).get(col)
    return float(v) if v else None

def deep(m, *path):
    v = m
    for k in path:
        v = (v or {}).get(k) if isinstance(v, dict) else None
    return v

def vendor(slug):
    return (D[slug].get("creator") or {}).get("name")

def tb4(m):
    v = m.get("terminalBench40")
    if v is None:
        v = m.get("terminalbenchV40")
    return v

def hal(m):
    v = m.get("omniscienceHallucinationRate")
    if isinstance(v, (int, float)):
        return v
    return deep(m, "omniscienceBreakdown", "hallucinationRate")

def acc(m):
    v = m.get("omniscienceAccuracy")
    if isinstance(v, (int, float)):
        return v
    return deep(m, "omniscienceBreakdown", "accuracy")

def rub(m):
    return deep(m, "briefcaseBreakdown", "rubricPassRate")

def elo(m):
    return deep(m, "briefcaseBreakdown", "analyticalQuality", "elo")

def family(slug):
    """Семейство без суффикса effort. `grok-4-7` само по себе xhigh, хвост `7` — не effort."""
    base, _, tail = slug.rpartition("-")
    if tail in EFFORTS and base:
        return base
    return slug

def same_family(slug, fam):
    if slug == fam:
        return True
    if not slug.startswith(fam + "-"):
        return False
    return slug[len(fam) + 1:] in EFFORTS

def paid(slug):
    """Внешние деньги за задачу: 0 для того, что покрыто подпиской."""
    return 0.0 if vendor(slug) == SUBSCRIPTION else (num(slug, "cost_per_task_$") or 0.0)

def quota(slug):
    """Расход квоты подписки в условных единицах = cost_per_task_$ по прайсу вендора.

    Точная формула лимитов Max x20 неизвестна и здесь НЕ воспроизводится. Вес модели
    в лимите связан с ценой токена: Opus 5.5 стоит $4/$20 за 1M, Opus 5 — $5/$25,
    Fable — $10/$50. Считать квоту в чистых токенах нельзя: дешёвый по токенам
    Fable оказался бы «экономнее» более дешёвого по деньгам Opus 5.5. Чистые токены
    остаются в снимке колонкой out_tok_task (справочно).
    """
    return (num(slug, "cost_per_task_$") or 0.0) if vendor(slug) == SUBSCRIPTION else 0.0

def f(v, d=2):
    return f"%.{d}f" % v if isinstance(v, (int, float)) else "-"

def _row(slug):
    m = D[slug]
    return (f"| `{slug}` | {vendor(slug)} | {f(m.get('intelligenceIndex'),1)} | {f(tb4(m))} | "
            f"{f(rub(m))} | {f(elo(m),0)} | {f(hal(m))} | {f(num(slug,'cost_per_task_$'))} | "
            f"{f(num(slug,'out_tok_task'),0)} | {f(num(slug,'sec_per_task'),0)} |")

def metrics_table():
    print("\n### Метрики кандидатов\n")
    print("| slug | вендор | II | tb4.0 | rubric | analElo | hal↓ | $/задача | вых.ток/задача | с/задача |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    seen = []
    for p in PRESETS.values():
        for s in p.values():
            if s not in seen:
                seen.append(s)
    for s in sorted(seen, key=lambda x: -(D[x].get("intelligenceIndex") or 0)):
        print(_row(s))

def presets_table():
    print("\n### Пресеты: внешние деньги и расход квоты\n")
    print("| Пресет | Писатель ТЗ | Критик | Разработчик | Судья | внешних $ | квота, усл.ед | с/задача |")
    print("|---|---|---|---|---|---|---|---|")
    for name, p in PRESETS.items():
        cells = [f"`{p[k]}`" if k in p else "—" for k, _ in ROLES]
        cash = sum(paid(s) for s in p.values())
        q = sum(quota(s) for s in p.values())
        secs = sum(num(s, "sec_per_task") or 0 for s in p.values())
        print(f"| **{name}** | {' | '.join(cells)} | {f(cash)} | {f(q)} | {f(secs,0)} |")

def anthropic_pool():
    pool = []
    for m in SNAP["models"]:
        if (m.get("creator") or {}).get("name") != SUBSCRIPTION or m.get("deprecated"):
            continue
        if not num(m["slug"], "cost_per_task_$"):
            continue
        if not isinstance(m.get("intelligenceIndex"), (int, float)):
            continue
        if not isinstance(tb4(m), (int, float)):
            continue
        pool.append(m)
    return pool

def dominated_by(slug, pool):
    base = D[slug]
    return [m["slug"] for m in pool if m["slug"] != slug
            and num(m["slug"], "cost_per_task_$") <= num(slug, "cost_per_task_$")
            and m["intelligenceIndex"] >= base["intelligenceIndex"]
            and tb4(m) >= tb4(base)
            and (num(m["slug"], "cost_per_task_$") < num(slug, "cost_per_task_$")
                 or m["intelligenceIndex"] > base["intelligenceIndex"])]

def frontier_table():
    """Недоминированные модели Anthropic с II >= 40. Хвост дешевле этого порога
    (Sonnet low, Haiku) формально тоже на фронте — только потому, что дешевле, —
    и в таблицу не входит."""
    print("\n### Фронт Парето Anthropic\n")
    print("| модель | квота, усл.ед | вых.ток | II | tb4.0 | analElo |")
    print("| --- | --- | --- | --- | --- | --- |")
    pool = anthropic_pool()
    front = [m for m in pool if not dominated_by(m["slug"], pool) and m["intelligenceIndex"] >= 40]
    for m in sorted(front, key=lambda m: num(m["slug"], "cost_per_task_$")):
        s = m["slug"]
        print(f"| `{s}` | {f(num(s,'cost_per_task_$'))} | {f(num(s,'out_tok_task'),0)} | "
              f"{f(m['intelligenceIndex'],1)} | {f(tb4(m))} | {f(elo(m),0)} |")

def checks():
    """Утверждения из ROLES.md, проверяемые прямо по снимку."""
    out = []
    def claim(text, ok, detail=""):
        out.append((ok, text, detail))

    # 1. Sonnet под подпиской бессмыслен: даже low у Opus 5.5 сильнее и дешевле xhigh у Sonnet
    om, sx = D["claude-opus-5-5-medium"], D["claude-sonnet-5-xhigh"]
    lo = D["claude-opus-5-5-low"]
    claim("opus-5-5-medium тратит меньше квоты, чем sonnet-5-xhigh (и по $, и по токенам)",
          num("claude-opus-5-5-medium","out_tok_task") < num("claude-sonnet-5-xhigh","out_tok_task")
          and num("claude-opus-5-5-medium","cost_per_task_$") < num("claude-sonnet-5-xhigh","cost_per_task_$"),
          f"ток {num('claude-opus-5-5-medium','out_tok_task'):.0f}<{num('claude-sonnet-5-xhigh','out_tok_task'):.0f}, "
          f"$ {num('claude-opus-5-5-medium','cost_per_task_$'):.2f}<{num('claude-sonnet-5-xhigh','cost_per_task_$'):.2f}")
    claim("...и при этом умнее по II и tb4.0",
          om["intelligenceIndex"] > sx["intelligenceIndex"] and tb4(om) > tb4(sx),
          f"II {om['intelligenceIndex']:.1f}>{sx['intelligenceIndex']:.1f}, tb4 {tb4(om):.2f}>{tb4(sx):.2f}")
    claim("opus-5-5-low тоже обгоняет sonnet-5-xhigh по II, tb4 и квоте",
          lo["intelligenceIndex"] > sx["intelligenceIndex"] and tb4(lo) > tb4(sx)
          and num("claude-opus-5-5-low","cost_per_task_$") < num("claude-sonnet-5-xhigh","cost_per_task_$"),
          f"II {lo['intelligenceIndex']:.1f}>{sx['intelligenceIndex']:.1f}, "
          f"$ {num('claude-opus-5-5-low','cost_per_task_$'):.2f}<{num('claude-sonnet-5-xhigh','cost_per_task_$'):.2f}")

    # 2. Вся линейка DeepSeek непригодна для ролей проверки
    ds = [m for m in SNAP["models"] if (m.get("creator") or {}).get("name") == "DeepSeek"
          and not m.get("deprecated") and isinstance(hal(m), (int, float))]
    worst = min(ds, key=hal)
    claim("у всех не-deprecated DeepSeek hallucinationRate >= 0.87",
          hal(worst) >= 0.87, f"минимум {hal(worst):.4f} на {worst['slug']} ({len(ds)} моделей)")

    # 3. Судья — Grok 4.7: рубрика выше, чем у критика, и это старший Grok.
    #    Размен против 4.6 xhigh признан: рубрика лучше, hal хуже, цена выше.
    claim("grok-4-7 выше glm-5-3-flash по rubricPassRate (потому судья — Grok)",
          rub(D["grok-4-7"]) > rub(D["glm-5-3-flash"]),
          f"{rub(D['grok-4-7']):.3f} > {rub(D['glm-5-3-flash']):.3f}")
    claim("grok-4-7 выше grok-4-6-xhigh по рубрике, но хуже по hal и дороже (размен признан)",
          rub(D["grok-4-7"]) > rub(D["grok-4-6-xhigh"])
          and hal(D["grok-4-7"]) > hal(D["grok-4-6-xhigh"])
          and num("grok-4-7","cost_per_task_$") > num("grok-4-6-xhigh","cost_per_task_$"),
          f"rub {rub(D['grok-4-7']):.3f}>{rub(D['grok-4-6-xhigh']):.3f}, "
          f"hal {hal(D['grok-4-7']):.3f}>{hal(D['grok-4-6-xhigh']):.3f}, "
          f"${num('grok-4-7','cost_per_task_$'):.2f}>{num('grok-4-6-xhigh','cost_per_task_$'):.2f}")
    claim("grok-4-7 лучше grok-4-7-high по рубрике и по hal (за это платят xhigh)",
          rub(D["grok-4-7"]) >= rub(D["grok-4-7-high"]) and hal(D["grok-4-7"]) < hal(D["grok-4-7-high"]),
          f"rub {rub(D['grok-4-7']):.3f}>={rub(D['grok-4-7-high']):.3f}, "
          f"hal {hal(D['grok-4-7']):.3f}<{hal(D['grok-4-7-high']):.3f}")

    # 3б. Критик — flash: проигрывает судье по аналитике, выигрывает ценой и hal.
    claim("glm-5-3-flash уступает grok-4-7 по analyticalQuality Elo (размен признан)",
          elo(D["glm-5-3-flash"]) < elo(D["grok-4-7"]),
          f"{elo(D['glm-5-3-flash']):.0f} < {elo(D['grok-4-7']):.0f}")
    claim("glm-5-3-flash осторожнее grok-4-7 и дешевле",
          hal(D["glm-5-3-flash"]) < hal(D["grok-4-7"])
          and num("glm-5-3-flash","cost_per_task_$") < num("grok-4-7","cost_per_task_$"),
          f"hal {hal(D['glm-5-3-flash']):.3f}<{hal(D['grok-4-7']):.3f}, "
          f"${num('glm-5-3-flash','cost_per_task_$'):.2f}<${num('grok-4-7','cost_per_task_$'):.2f}")
    cheaper = [m for m in SNAP["models"]
               if (m.get("creator") or {}).get("name") in VENDORS and not m.get("deprecated")
               and isinstance(elo(m), (int, float))
               and (num(m["slug"], "cost_per_task_$") or 99) <= num("glm-5-3-flash", "cost_per_task_$")]
    best = max(cheaper, key=elo)
    claim("glm-5-3-flash — самый аналитичный критик в своей ценовой категории",
          best["slug"] == "glm-5-3-flash", f"лучший за <= ${num('glm-5-3-flash','cost_per_task_$'):.2f}: {best['slug']}")

    # 3в. Предыдущие линейки автора доминируются Opus 5.5 — поэтому их больше нет в пресетах.
    pool = anthropic_pool()
    for loser, winner in (("claude-fable-5-1-xhigh", "claude-opus-5-5-high"),
                          ("claude-opus-5-medium", "claude-opus-5-5-medium"),
                          ("claude-opus-5-low", "claude-opus-5-5-low")):
        dom = dominated_by(loser, pool)
        claim(f"{loser} доминируется {winner}", winner in dom, ", ".join(dom) or "никем")

    # 4. Формула Omniscience (подтверждает трактовку hallucinationRate «меньше — лучше»)
    bad = 0; tot = 0
    for m in SNAP["models"]:
        a, h, o = acc(m), hal(m), m.get("omniscience")
        if not all(isinstance(x, (int, float)) for x in (a, h, o)):
            continue
        tot += 1
        if abs(100 * (a - (1 - a) * h) - o) > 0.5:
            bad += 1
    claim("omniscience == 100*(accuracy - (1-accuracy)*hallucinationRate)", bad == 0,
          f"сошлось на {tot} моделях, расхождений {bad}")

    # 5. nano не дороже xlow ни по деньгам, ни по времени
    p4, p5 = PRESETS["xlow"], PRESETS["nano"]
    c4, c5 = sum(paid(s) for s in p4.values()), sum(paid(s) for s in p5.values())
    t4, t5 = sum(num(s,"sec_per_task") or 0 for s in p4.values()), sum(num(s,"sec_per_task") or 0 for s in p5.values())
    claim("nano дешевле xlow по внешним деньгам", c5 <= c4, f"${c5:.2f} <= ${c4:.2f}")
    claim("nano быстрее xlow", t5 < t4, f"{t5:.0f}с < {t4:.0f}с")

    # 6. Все выбранные модели — из доступных вендоров и не сняты с поддержки
    used = {s for p in PRESETS.values() for s in p.values()}
    claim("все модели пресетов от доступных вендоров", all(vendor(s) in VENDORS for s in used),
          ", ".join(sorted({vendor(s) for s in used})))
    claim("ни одна модель пресетов не deprecated", not any(D[s].get("deprecated") for s in used))

    # 7. Независимость: проверяющие не от того же вендора, что автор
    for name, p in PRESETS.items():
        if "critic" not in p:
            continue
        authors = {vendor(p["spec"]), vendor(p["dev"])}
        reviewers = {vendor(p["critic"]), vendor(p["judge"])}
        claim(f"[{name}] критик и судья не от вендора автора", not (authors & reviewers),
              f"автор: {'/'.join(sorted(authors))} — проверка: {'/'.join(sorted(reviewers))}")

    # 8. Судья не доминируется родственником, который лучше по rubric и hal и не дороже.
    for name, p in PRESETS.items():
        if "judge" not in p:
            continue
        j = p["judge"]
        fam = family(j)
        rival = [m for m in SNAP["models"] if same_family(m["slug"], fam) and m["slug"] != j
                 and not m.get("deprecated")
                 and isinstance(rub(m), (int, float)) and isinstance(hal(m), (int, float))
                 and rub(m) > rub(D[j]) and hal(m) < hal(D[j])
                 and (num(m["slug"], "cost_per_task_$") or 99) <= (num(j, "cost_per_task_$") or 0)]
        claim(f"[{name}] судья {j} не доминируется роднёй по rubric+hal при той же цене", not rival,
              ", ".join(m["slug"] for m in rival) or "нет доминирующих")

    # 9. Роль на подписке лежит на фронте Парето квота / II / tb4.
    for name, p in PRESETS.items():
        for key, label in ROLES:
            s_ = p.get(key)
            if not s_ or vendor(s_) != SUBSCRIPTION or not num(s_, "cost_per_task_$"):
                continue
            dom = dominated_by(s_, pool)
            claim(f"[{name}] {label} {s_} на фронте Парето (квота-$/II/tb4)", not dom,
                  ", ".join(dom) or "не доминируется")

    # 10. На верхних ступенях писатель и разработчик — одна модель (Opus 5.5 того же усилия).
    for name in ("xhigh", "high", "medium"):
        p = PRESETS[name]
        claim(f"[{name}] писатель и разработчик — одна модель", p["spec"] == p["dev"], p["spec"])

    # 11. Исполнитель с высоким hallucinationRate не может работать без приёмки.
    for name, p in PRESETS.items():
        dv = p.get("dev")
        h = hal(D.get(dv, {}))
        if h is None or h < 0.85:
            continue
        claim(f"[{name}] разработчик {dv} (hal {h:.2f}) прикрыт судьёй", bool(p.get("judge")),
              p.get("judge") or "СУДЬИ НЕТ — самоотчёт такой модели недостоверен")

    ok = sum(1 for o, _, _ in out if o)
    for o, t, d in out:
        print(f"{'OK  ' if o else 'FAIL'} {t}" + (f"   [{d}]" if d else ""))
    print(f"\n{ok}/{len(out)} проверок прошло")
    return 0 if ok == len(out) else 1

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        sys.exit(checks())
    print(f"Снимок от {SNAP['fetched']}, моделей {SNAP['count']}")
    metrics_table()
    presets_table()
    frontier_table()

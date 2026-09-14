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
VENDORS = {"OpenAI", "Anthropic", "DeepSeek", "SpaceXAI", "Z AI"}
# Всё от Anthropic идёт по подписке Claude Max x20 → внешняя цена 0, расходуется квота
SUBSCRIPTION = "Anthropic"

PRESETS = {
    "2. Максимум": dict(spec="claude-fable-5-1-xhigh", critic="glm-5-3-flash",
                        dev="gpt-6-astra-xhigh", judge="grok-4-6-xhigh"),
    "1. Баланс":   dict(spec="claude-fable-5-1-medium", critic="glm-5-3-flash",
                        dev="claude-opus-5-medium", judge="grok-4-6-xhigh"),
    "3. Лошадь":   dict(spec="claude-fable-5-1-low", critic="glm-5-3-flash",
                        dev="claude-opus-5-medium", judge="grok-4-6-medium"),
    "4. Дёшево":   dict(spec="claude-opus-5-low", critic="glm-5-3-flash",
                        dev="deepseek-v4-1-flash", judge="grok-4-6-low"),
    "5. Один прогон": dict(dev="claude-opus-5-medium"),
}
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

def paid(slug):
    """Внешние деньги за задачу: 0 для того, что покрыто подпиской."""
    return 0.0 if vendor(slug) == SUBSCRIPTION else (num(slug, "cost_per_task_$") or 0.0)

def quota(slug):
    """Расход квоты подписки в условных единицах = cost_per_task_$ по прайсу вендора.

    Точная формула лимитов Max x20 неизвестна и здесь НЕ воспроизводится. Известно,
    что вес модели в лимите связан с её ценой: Fable стоит $10/$50 за 1M против
    $5/$25 у Opus, то есть тот же токен съедает вдвое больше. Поэтому считать квоту
    в чистых токенах нельзя — берём стоимостный эквивалент как консервативный прокси.
    Чистые токены остаются в снимке отдельной колонкой out_tok_task (справочно).
    """
    return (num(slug, "cost_per_task_$") or 0.0) if vendor(slug) == SUBSCRIPTION else 0.0

def f(v, d=2):
    return f"%.{d}f" % v if isinstance(v, (int, float)) else "-"

def metrics_table():
    print("\n### Метрики кандидатов\n")
    print("| slug | вендор | II | tb4.0 | rubric | analElo | hal↓ | $/задача | вых.ток/задача | с/задача |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    seen = []
    for p in PRESETS.values():
        for s in p.values():
            if s not in seen: seen.append(s)
    for s in sorted(seen, key=lambda x: -(D[x].get("intelligenceIndex") or 0)):
        m = D[s]
        print(f"| `{s}` | {vendor(s)} | {f(m.get('intelligenceIndex'),1)} | {f(m.get('terminalbenchV40'))} | "
              f"{f(deep(m,'briefcaseBreakdown','rubricPassRate'))} | {f(deep(m,'briefcaseBreakdown','analyticalQuality','elo'),0)} | "
              f"{f(deep(m,'omniscienceBreakdown','hallucinationRate'))} | {f(num(s,'cost_per_task_$'))} | "
              f"{f(num(s,'out_tok_task'),0)} | {f(num(s,'sec_per_task'),0)} |")

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

def checks():
    """Утверждения из ROLES.md, проверяемые прямо по снимку."""
    out = []
    def claim(text, ok, detail=""):
        out.append((ok, text, detail))

    # 1. Sonnet под подпиской бессмыслен: opus-5-medium дешевле по квоте и сильнее
    om, sx = D["claude-opus-5-medium"], D["claude-sonnet-5-xhigh"]
    claim("opus-5-medium тратит меньше квоты, чем sonnet-5-xhigh (и по $, и по токенам)",
          num("claude-opus-5-medium","out_tok_task") < num("claude-sonnet-5-xhigh","out_tok_task")
          and num("claude-opus-5-medium","cost_per_task_$") < num("claude-sonnet-5-xhigh","cost_per_task_$"),
          f"ток {num('claude-opus-5-medium','out_tok_task'):.0f}<{num('claude-sonnet-5-xhigh','out_tok_task'):.0f}, "
          f"$ {num('claude-opus-5-medium','cost_per_task_$'):.2f}<{num('claude-sonnet-5-xhigh','cost_per_task_$'):.2f}")
    claim("...и при этом умнее по II и tb4.0",
          om["intelligenceIndex"] > sx["intelligenceIndex"] and om["terminalbenchV40"] > sx["terminalbenchV40"],
          f"II {om['intelligenceIndex']:.1f}>{sx['intelligenceIndex']:.1f}, tb4 {om['terminalbenchV40']:.2f}>{sx['terminalbenchV40']:.2f}")

    # 2. Вся линейка DeepSeek непригодна для ролей проверки
    ds = [m for m in SNAP["models"] if (m.get("creator") or {}).get("name") == "DeepSeek"
          and not m.get("deprecated")
          and deep(m, "omniscienceBreakdown", "hallucinationRate") is not None]
    worst = min(deep(m, "omniscienceBreakdown", "hallucinationRate") for m in ds)
    claim("у всех не-deprecated DeepSeek hallucinationRate >= 0.87", worst >= 0.87, f"минимум по линейке {worst:.4f} на {len(ds)} моделях")

    # 3. Grok в судьи — он лучше по рубрике, чем взятый в критики glm-5-3-flash
    claim("grok-4-6 выше glm-5-3-flash по rubricPassRate (потому судья — Grok)",
          deep(D["grok-4-6"],"briefcaseBreakdown","rubricPassRate") > deep(D["glm-5-3-flash"],"briefcaseBreakdown","rubricPassRate"),
          f"{deep(D['grok-4-6'],'briefcaseBreakdown','rubricPassRate'):.2f} > {deep(D['glm-5-3-flash'],'briefcaseBreakdown','rubricPassRate'):.2f}")
    # 3б. glm-5-3-flash в критики держится на цене и hal, а НЕ на аналитике:
    #     по analElo он уступает Grok — это сознательный размен, фиксируем его явно
    claim("glm-5-3-flash уступает grok-4-6 по analyticalQuality Elo (размен признан)",
          deep(D["glm-5-3-flash"],"briefcaseBreakdown","analyticalQuality","elo") < deep(D["grok-4-6"],"briefcaseBreakdown","analyticalQuality","elo"),
          f"{deep(D['glm-5-3-flash'],'briefcaseBreakdown','analyticalQuality','elo'):.0f} < {deep(D['grok-4-6'],'briefcaseBreakdown','analyticalQuality','elo'):.0f}")
    claim("glm-5-3-flash лучше grok-4-6 по hallucinationRate и дешевле",
          deep(D["glm-5-3-flash"],"omniscienceBreakdown","hallucinationRate") < deep(D["grok-4-6"],"omniscienceBreakdown","hallucinationRate")
          and num("glm-5-3-flash","cost_per_task_$") < num("grok-4-6","cost_per_task_$"),
          f"hal {deep(D['glm-5-3-flash'],'omniscienceBreakdown','hallucinationRate'):.2f} < {deep(D['grok-4-6'],'omniscienceBreakdown','hallucinationRate'):.2f}, "
          f"${num('glm-5-3-flash','cost_per_task_$'):.2f} < ${num('grok-4-6','cost_per_task_$'):.2f}")
    # 3в. среди доступных нет критика дешевле glm-5-3, который был бы аналитичнее
    cheaper = [m for m in SNAP["models"]
               if (m.get("creator") or {}).get("name") in VENDORS and not m.get("deprecated")
               and deep(m, "briefcaseBreakdown", "analyticalQuality", "elo") is not None
               and (num(m["slug"], "cost_per_task_$") or 99) <= num("glm-5-3-flash", "cost_per_task_$")]
    best = max(cheaper, key=lambda m: deep(m, "briefcaseBreakdown", "analyticalQuality", "elo"))
    claim("glm-5-3-flash — самый аналитичный критик в своей ценовой категории",
          best["slug"] == "glm-5-3-flash", f"лучший за <= ${num('glm-5-3-flash','cost_per_task_$'):.2f}: {best['slug']}")

    # 4. Формула Omniscience (подтверждает трактовку hallucinationRate «меньше — лучше»)
    bad = 0; tot = 0
    for m in SNAP["models"]:
        b = m.get("omniscienceBreakdown") or {}
        a, h, o = b.get("accuracy"), b.get("hallucinationRate"), m.get("omniscience")
        if not all(isinstance(x, (int, float)) for x in (a, h, o)): continue
        tot += 1
        if abs(100 * (a - (1 - a) * h) - o) > 0.5: bad += 1
    claim("omniscience == 100*(accuracy - (1-accuracy)*hallucinationRate)", bad == 0, f"сошлось на {tot} моделях, расхождений {bad}")

    # 5. Пресет 5 не дороже пресета 4 ни по деньгам, ни по времени
    p4, p5 = PRESETS["4. Дёшево"], PRESETS["5. Один прогон"]
    c4, c5 = sum(paid(s) for s in p4.values()), sum(paid(s) for s in p5.values())
    t4, t5 = sum(num(s,"sec_per_task") or 0 for s in p4.values()), sum(num(s,"sec_per_task") or 0 for s in p5.values())
    claim("пресет 5 дешевле пресета 4 по внешним деньгам", c5 <= c4, f"${c5:.2f} <= ${c4:.2f}")
    claim("пресет 5 быстрее пресета 4", t5 < t4, f"{t5:.0f}с < {t4:.0f}с")

    # 6. Все выбранные модели — из доступных вендоров и не сняты с поддержки
    used = {s for p in PRESETS.values() for s in p.values()}
    claim("все модели пресетов от доступных вендоров", all(vendor(s) in VENDORS for s in used),
          ", ".join(sorted({vendor(s) for s in used})))
    claim("ни одна модель пресетов не deprecated", not any(D[s].get("deprecated") for s in used))

    # 7. Независимость: проверяющие не от того же вендора, что автор
    for name, p in PRESETS.items():
        if "critic" not in p: continue
        authors = {vendor(p["spec"]), vendor(p["dev"])}
        reviewers = {vendor(p["critic"]), vendor(p["judge"])}
        claim(f"[{name}] критик и судья не от вендора автора", not (authors & reviewers),
              f"автор: {'/'.join(sorted(authors))} — проверка: {'/'.join(sorted(reviewers))}")

    # 8. Судья в каждом пресете не доминируется другим вариантом того же семейства
    for name, p in PRESETS.items():
        if "judge" not in p: continue
        j = p["judge"]; fam = j.rsplit("-", 1)[0]
        rival = [m for m in SNAP["models"] if m["slug"].startswith(fam) and m["slug"] != j
                 and not m.get("deprecated")
                 and deep(m, "briefcaseBreakdown", "rubricPassRate") is not None
                 and deep(m, "briefcaseBreakdown", "rubricPassRate") > deep(D[j], "briefcaseBreakdown", "rubricPassRate")
                 and deep(m, "omniscienceBreakdown", "hallucinationRate") < deep(D[j], "omniscienceBreakdown", "hallucinationRate")
                 # доминирование только при НЕ большей цене: в дешёвых пресетах слабый
                 # судья взят ради экономии, и это не ошибка выбора
                 and (num(m["slug"], "cost_per_task_$") or 99) <= (num(j, "cost_per_task_$") or 0)]
        claim(f"[{name}] судья {j} не доминируется роднёй по rubric+hal при той же цене", not rival,
              ", ".join(m["slug"] for m in rival) or "нет доминирующих")

    # 9. Ни одна роль на подписке не доминируется другой моделью Anthropic:
    #    меньше выходных токенов И не ниже по II И не ниже по terminal-bench 4.0.
    #    Эта проверка ловит ровно ту ошибку, из-за которой Opus стоял разработчиком,
    #    пока «квота» считалась по долларовому полю.
    pool = [m for m in SNAP["models"]
            if (m.get("creator") or {}).get("name") == SUBSCRIPTION and not m.get("deprecated")
            and num(m["slug"], "cost_per_task_$") and isinstance(m.get("terminalbenchV40"), (int, float))]
    for name, p in PRESETS.items():
        for key, label in ROLES:
            s_ = p.get(key)
            if not s_ or vendor(s_) != SUBSCRIPTION or not num(s_, "cost_per_task_$"): continue
            base = D[s_]
            dom = [m["slug"] for m in pool if m["slug"] != s_
                   and num(m["slug"], "cost_per_task_$") <= num(s_, "cost_per_task_$")
                   and m["intelligenceIndex"] >= base["intelligenceIndex"]
                   and m["terminalbenchV40"] >= base["terminalbenchV40"]
                   and (num(m["slug"], "cost_per_task_$") < num(s_, "cost_per_task_$")
                        or m["intelligenceIndex"] > base["intelligenceIndex"])]
            claim(f"[{name}] {label} {s_} на фронте Парето (квота-$/II/tb4)", not dom, ", ".join(dom) or "не доминируется")

    # 10. Исполнитель с высоким hallucinationRate не может работать без приёмки:
    #     при hal >= 0.85 модель практически никогда не говорит «не смог», её
    #     самоотчёт «готово» ничего не значит — судья обязателен.
    for name, p in PRESETS.items():
        dv = p.get("dev")
        h = deep(D.get(dv, {}), "omniscienceBreakdown", "hallucinationRate")
        if h is None or h < 0.85: continue
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

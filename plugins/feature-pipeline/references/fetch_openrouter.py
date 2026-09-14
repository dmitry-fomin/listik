#!/usr/bin/env python3
"""Цены и лимиты всех моделей OpenRouter — публичный API, ключ не нужен.

Дополняет снимок Artificial Analysis: AA даёт качество и цену «у вендора»,
OpenRouter — реальный прайс доступа и контекст по каждому роутингу.
"""
import csv, json, os, urllib.request
from datetime import date

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "openrouter")
data = json.load(urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=60))["data"]

def per_m(x):
    try:
        return round(float(x) * 1_000_000, 4)
    except (TypeError, ValueError):
        return None

rows = []
for m in data:
    p = m.get("pricing") or {}
    rows.append({
        "id": m.get("id"),
        "name": m.get("name"),
        "ctx": m.get("context_length"),
        "in_$1m": per_m(p.get("prompt")),
        "out_$1m": per_m(p.get("completion")),
        "cache_read_$1m": per_m(p.get("input_cache_read")),
        "cache_write_$1m": per_m(p.get("input_cache_write")),
        "reasoning_$1m": per_m(p.get("internal_reasoning")),
        "img_$": p.get("image"),
        "modalities": ",".join((m.get("architecture") or {}).get("input_modalities") or []),
        "created": m.get("created"),
    })
rows.sort(key=lambda r: r["id"] or "")
json.dump({"fetched": date.today().isoformat(), "count": len(rows), "models": rows},
          open(BASE + ".json", "w"), ensure_ascii=False, indent=1)
with open(BASE + ".csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
print(f"{len(rows)} моделей → {BASE}.json, {BASE}.csv")

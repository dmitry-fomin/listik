"""Ограждение записи по поколению запуска — зомби-процессы не пишут в карточку.

Лаунчер (`listik/launcher.py`) выдаёт каждому запуску `generation`/`dispatch_id`
и кладёт их в окружение процесса (`LISTIK_TASK_ID`, `LISTIK_GENERATION`,
`LISTIK_DISPATCH_ID`). Если карточку перезапустили новым поколением, а старый
процесс всё ещё жив и пишет в неё — это зомби: его запись **не применяется**, но
**сохраняется в карантин** (`quarantine`, событие `rejected`), чтобы человек видел,
от кого и почему остаются зомби. Вызывающему возвращается `errors.Revoked`.

Модуль не импортирует `server`/`client`/`mcp` — только `errors`, `store` и stdlib,
чтобы им можно было пользоваться из всех трёх без цикла импортов.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from . import errors
from . import store

ENV_TASK = "LISTIK_TASK_ID"
ENV_GENERATION = "LISTIK_GENERATION"
ENV_DISPATCH = "LISTIK_DISPATCH_ID"

HEADER_TASK = "X-Listik-Task"
HEADER_GENERATION = "X-Listik-Generation"
HEADER_DISPATCH = "X-Listik-Dispatch"

#: Единственный вид события карантина — им же и фильтруются ленты/`get_task`.
REJECTED_KIND = "rejected"


@dataclass(frozen=True)
class Token:
    task_id: str
    generation: int
    dispatch_id: str | None


def _parse_generation(value) -> int | None:
    text = (value or "").strip() if isinstance(value, str) else value
    if text is None or text == "":
        return None
    try:
        gen = int(text)
    except (TypeError, ValueError):
        return None
    return gen if gen >= 0 else None


def from_env(environ=None) -> Token | None:
    """Токен процесса — окружение, выданное лаунчером. `None`, если его нет."""
    import os
    env = environ if environ is not None else os.environ
    task_id = (env.get(ENV_TASK) or "").strip()
    if not task_id:
        return None
    generation = _parse_generation(env.get(ENV_GENERATION))
    if generation is None:
        return None
    dispatch_id = (env.get(ENV_DISPATCH) or "").strip() or None
    return Token(task_id=task_id, generation=generation, dispatch_id=dispatch_id)


def from_headers(headers) -> Token | None:
    """Токен из HTTP-заголовков — те же правила, что и у `from_env`."""
    task_id = (headers.get(HEADER_TASK) or "").strip()
    if not task_id:
        return None
    generation = _parse_generation(headers.get(HEADER_GENERATION))
    if generation is None:
        return None
    dispatch_id = (headers.get(HEADER_DISPATCH) or "").strip() or None
    return Token(task_id=task_id, generation=generation, dispatch_id=dispatch_id)


def to_headers(token: Token) -> dict[str, str]:
    """Токен → три заголовка запроса (симметрично `from_headers`)."""
    headers = {HEADER_TASK: token.task_id, HEADER_GENERATION: str(token.generation)}
    if token.dispatch_id:
        headers[HEADER_DISPATCH] = token.dispatch_id
    return headers


def from_mapping(value) -> Token | None:
    """Токен из словаря `{"task_id", "generation", "dispatch_id"}` (локальный фолбэк, тесты).

    `Token`, переданный вместо словаря, возвращается как есть.
    """
    if value is None or isinstance(value, Token):
        return value
    task_id = str(value.get("task_id") or "").strip()
    if not task_id:
        return None
    generation = _parse_generation(value.get("generation"))
    if generation is None:
        return None
    dispatch_id = value.get("dispatch_id")
    dispatch_id = str(dispatch_id).strip() if dispatch_id else None
    return Token(task_id=task_id, generation=generation, dispatch_id=dispatch_id or None)


def matches(token: Token, row) -> bool:
    """Токен совпадает с текущим состоянием карточки: поколение и запуск сходятся.

    Поколение должно совпасть строго; запуск (`dispatch_id`) допускает пустоту с
    любой стороны — иначе запуски до появления поколений (`dispatch_id IS NULL`)
    отвергались бы всегда. Поколение из токена больше текущего — тоже несовпадение:
    такого токена сервер никогда не выдавал.
    """
    current_generation = int(row["generation"] or 0)
    if token.generation != current_generation:
        return False
    current_dispatch = row["dispatch_id"] or None
    if not token.dispatch_id or not current_dispatch:
        return True
    return token.dispatch_id == current_dispatch


def _clean_args(args: dict | None) -> dict:
    """Аргументы операции для карантина: без `None`, без служебных `as_owner`/`fence`."""
    if not isinstance(args, dict):
        return {}
    return {k: v for k, v in args.items()
            if v is not None and k not in ("as_owner", "fence")}


def quarantine(conn, task_id: str, token: Token, *, op: str, args: dict,
              current_generation: int, current_dispatch: str | None,
              actor: str | None, harness: str | None) -> None:
    """Сохранить отвергнутую запись зомби-процесса в карантин.

    Единственное место, где отвергнутая запись сохраняется: отключить сохранение —
    убрать этот вызов из `guard`. Пишет событие `rejected` (полное тело операции —
    в `note`, JSON) и коммитит: карантин переживает то, что запись, которую он
    описывает, не применяется вовсе.
    """
    payload = {
        "op": op,
        "dispatch_id": token.dispatch_id,
        "current_dispatch_id": current_dispatch,
        "args": _clean_args(args),
    }
    note = json.dumps(payload, ensure_ascii=False, default=str)
    store.event(conn, task_id, REJECTED_KIND,
               from_value=str(token.generation), to_value=str(current_generation),
               actor=actor, harness=harness, note=note)
    conn.commit()


def guard(conn, task_id: str, token: Token | None, *, op: str, args: dict,
         actor: str | None = None, harness: str | None = None) -> None:
    """Отказать зомби-процессу до записи в store. Чужая карточка не ограждается.

    `token is None` или `token.task_id != task_id` — токена на эту карточку нет,
    возвращаемся, ничего не читая и не меняя. Задачи не существует — тоже
    возвращаемся: 404 `not_found` диагностирует сам store, а `guard` не должен
    подменять его 500-й (`NotFound` из `guard` `server.error_response` не ловит).
    """
    if token is None or token.task_id != task_id:
        return
    row = conn.execute("SELECT generation, dispatch_id FROM tasks WHERE id = ?",
                       (task_id,)).fetchone()
    if row is None:
        return
    if matches(token, row):
        return
    current_generation = int(row["generation"] or 0)
    current_dispatch = row["dispatch_id"] or None
    quarantine(conn, task_id, token, op=op, args=args,
              current_generation=current_generation, current_dispatch=current_dispatch,
              actor=actor, harness=harness)
    if token.generation != current_generation:
        message = (f"полномочия на задачу {task_id} отозваны: запуск поколения "
                  f"{token.generation} устарел, текущее поколение {current_generation}")
    else:
        message = (f"полномочия на задачу {task_id} отозваны: запуск {token.dispatch_id} "
                  f"не текущий (текущий {current_dispatch}), поколение {token.generation}")
    raise errors.Revoked(message)


def list_rejected(conn, task_id: str) -> list[dict]:
    """Карантин задачи — отвергнутые записи, старейшая первой (`ts, id`)."""
    rows = conn.execute(
        "SELECT ts, from_value, to_value, actor, harness, note FROM events "
        "WHERE task_id = ? AND kind = ? ORDER BY ts, id",
        (task_id, REJECTED_KIND)).fetchall()
    out = []
    for row in rows:
        try:
            gen = int(row["from_value"])
        except (TypeError, ValueError):
            gen = 0
        try:
            cur = int(row["to_value"])
        except (TypeError, ValueError):
            cur = 0
        item = {"ts": row["ts"], "generation": gen, "current_generation": cur,
               "actor": row["actor"], "harness": row["harness"]}
        try:
            payload = json.loads(row["note"] or "{}")
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            item["op"] = payload.get("op", "?")
            item["dispatch_id"] = payload.get("dispatch_id")
            item["current_dispatch_id"] = payload.get("current_dispatch_id")
            item["args"] = payload.get("args") or {}
        else:
            item["op"] = "?"
            item["dispatch_id"] = None
            item["current_dispatch_id"] = None
            item["args"] = {}
            item["raw"] = row["note"]
        out.append(item)
    return out

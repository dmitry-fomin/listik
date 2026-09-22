"""Каталог харнессов — таблица `harnesses` (listik-2gry).

Харнесс — исполнитель, которого Listik умеет поднимать процессом: у него ключ
(имя держателя, хвост `agent:<key>`), имя/подпись/иконка для списков и команда
по умолчанию — `argv` плюс `prompt` последним аргументом, с теми же подстановками,
что у команды маршрута (`routes.PLACEHOLDERS`). `kind='manual'` — ручная выдача
(`me`): процесса нет, записи команды нет.

Источник правды — только таблица: сиды (`SEEDS`) ввозятся один раз при `db.init`
(`INSERT … ON CONFLICT DO NOTHING` — правки человека не затираются). Читают
каталог доска (`GET /api/harnesses`), прямой маршрут и рой (держатель `agent:<key>`)
и `routes_store` при проверке ролей `kind=swarm`.
"""
from __future__ import annotations

import json
import sqlite3

from . import actors as actors_mod
from . import errors as errors_mod
from . import routes as routes_mod
from . import store
from .store_helpers import json_list  # noqa: F401  (переэкспорт для читателей)

now_iso = store.now_iso

#: Виды харнесса: `exec` — Listik поднимает процесс по argv; `manual` — ручная
#: выдача человеку, команды нет.
KINDS = ("exec", "manual")

#: Ключи глифов, которые доска умеет рисовать своей иконкой; прочие ключи тоже
#: допустимы — доска покажет «свою букву».
KNOWN_ICONS = ("claude", "dsh", "codex", "grok", "devin",
               "pi", "pi-glm", "pi-deepseek", "user")

#: Промпт роли роя по умолчанию: этап и роль подставляет лаунчер, держателя
#: карточки ставит сам Listik (см. docs/specs/swarm-stage-launch.md).
SWARM_PROMPT = (
    "Задача {task_id} (проект {project}), этап {stage}, роль {role}: карточка уже "
    "взята за тебя — claim, release и stage не вызывай. Контекст этапа: "
    "listik context {task_id} --stage {stage}. Рабочее дерево {worktree}, ветка "
    "{branch}, каталог {cwd}. Ответ — первая строка вывода: «готово», «вопрос» "
    "(далее текст вопроса) или «не смог»; на приёмке — «зелёный» или «красный» "
    "(далее список правок)."
)

#: Промпт прямой выдачи по умолчанию — тот же, что у прямых маршрутов поставки.
DIRECT_PROMPT = (
    "Задача {task_id} (проект {project}) уже выдана тебе: Listik поставил этап "
    "s1-spec и держателя. Работай по протоколу из AGENTS.md. Первое действие, до "
    "чтения кода и show: claim {task_id} --holder {harness}. Потом show, изучение, "
    "heartbeat каждые 10–15 мин. Перед первой правкой кода (не позже): stage "
    "{task_id} --to s3-impl. Итог — comment -k journal, затем done {task_id} "
    "-r \"…\". Все команды с --actor agent:{harness} --harness {harness}. Рабочее "
    "дерево: если в show поле worktree пустое, до правок создай его внутри папки "
    "проекта — cd {cwd} && mkdir -p .worktrees && git worktree add -b "
    "task/{task_id} .worktrees/{task_id} HEAD, запиши в карточку set {task_id} "
    "worktree={cwd}/.worktrees/{task_id} branch=task/{task_id} и работай только "
    "там. Если worktree задан или равен main — дерево не создавай."
)


def _prompt(template: str, harness: str) -> str:
    return template.replace("{harness}", harness)


#: Поставка: ключи — имена держателей (`agent:<key>`), `argv`/`prompt` — шаблон
#: по умолчанию для прямого маршрута и роли роя. `me` — человек: команды нет.
SEEDS: list[dict] = [
    {"key": "claude", "label": "claude", "hint": "Claude Code · claude -p",
     "icon": "claude",
     "argv": ["claude", "--dangerously-skip-permissions", "-p"],
     "prompt": _prompt(DIRECT_PROMPT, "claude"), "kind": "exec", "builtin": 1},
    {"key": "dsh", "label": "dsh", "hint": "DeepSeek Harness · dsh headless",
     "icon": "dsh",
     "argv": ["env", "DSH_PERMISSION_MODE=danger-full-access", "dsh",
              "--profile", "headless"],
     "prompt": _prompt(DIRECT_PROMPT, "dsh"), "kind": "exec", "builtin": 1},
    {"key": "codex", "label": "codex", "hint": "OpenAI Codex CLI · codex exec",
     "icon": "codex",
     "argv": ["codex", "exec", "-C", "{cwd}",
              "--dangerously-bypass-approvals-and-sandbox"],
     "prompt": _prompt(DIRECT_PROMPT, "codex"), "kind": "exec", "builtin": 1},
    {"key": "grok", "label": "grok", "hint": "Grok CLI · grok -p",
     "icon": "grok",
     "argv": ["grok", "--cwd", "{cwd}", "--always-approve", "-p"],
     "prompt": _prompt(DIRECT_PROMPT, "grok"), "kind": "exec", "builtin": 1},
    {"key": "pi-glm", "label": "pi · GLM", "hint": "pi --print · GLM 5.3 Flash",
     "icon": "pi-glm",
     "argv": ["pi", "--print", "--no-session", "--model", "b-ai-glm/glm-5.3-flash"],
     "prompt": _prompt(DIRECT_PROMPT, "pi-glm"), "kind": "exec", "builtin": 1},
    {"key": "pi-deepseek", "label": "pi · DeepSeek",
     "hint": "pi --print · DeepSeek v4.1 Flash", "icon": "pi-deepseek",
     "argv": ["pi", "--print", "--no-session", "--model", "b-ai-deepseek/deepseek-v4.1-flash"],
     "prompt": _prompt(DIRECT_PROMPT, "pi-deepseek"), "kind": "exec", "builtin": 1},
    {"key": "devin", "label": "devin", "hint": "Devin · devin -p",
     "icon": "devin",
     "argv": ["devin", "--permission-mode", "dangerous",
              "--respect-workspace-trust", "false", "-p"],
     "prompt": _prompt(DIRECT_PROMPT, "devin"), "kind": "exec", "builtin": 1},
    {"key": "me", "label": "Человек", "hint": "ручная выдача, без команды",
     "icon": "user", "argv": None, "prompt": None, "kind": "manual", "builtin": 1},
]

#: Поля записи в порядке колонок таблицы.
COLUMNS = ("key", "label", "hint", "icon", "argv", "prompt", "kind", "builtin",
           "enabled", "position", "created_at", "updated_at")

#: Поля, которые принимает PATCH /api/harnesses/<key>. Ключ, kind и builtin
#: неизменны: на ключ ссылаются маршруты и держатели карточек.
UPDATE_FIELDS = ("label", "hint", "icon", "argv", "prompt", "enabled", "position")


def _argv_of(value, where: str) -> list | None:
    """argv записи: NULL или массив непустых строк с известными подстановками."""
    if value is None:
        return None
    return routes_mod.validate_command(value, where)


def _prompt_of(value, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{where}: ожидается строка")
    routes_mod.check_placeholders(value, where)
    return value if value.strip() else None


def _icon_of(value, where: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not routes_mod.KEY_RE.match(value.strip()):
        raise ValueError(f"{where}: ожидается ключ глифа ({routes_mod.KEY_RE.pattern}) или null")
    return value.strip()


def _record(row) -> dict:
    return {
        "key": row["key"],
        "label": row["label"],
        "hint": row["hint"],
        "icon": row["icon"],
        "argv": json.loads(row["argv"]) if row["argv"] else None,
        "prompt": row["prompt"],
        "kind": row["kind"],
        "builtin": bool(row["builtin"]),
        "enabled": bool(row["enabled"]),
        "position": row["position"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get(conn: sqlite3.Connection, key: str) -> dict:
    row = conn.execute(
        f"SELECT {', '.join(COLUMNS)} FROM harnesses WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise errors_mod.NotFound(f"харнесса {key!r} нет в каталоге")
    return _record(row)


def by_key(conn: sqlite3.Connection, key: str | None) -> dict | None:
    """Запись или None — для проверок ссылок (роль роя, держатель прямого)."""
    if not key:
        return None
    row = conn.execute(
        f"SELECT {', '.join(COLUMNS)} FROM harnesses WHERE key = ?", (key,)).fetchone()
    return _record(row) if row else None


def list_harnesses(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        f"SELECT {', '.join(COLUMNS)} FROM harnesses ORDER BY position, key").fetchall()
    return [_record(row) for row in rows]


def used_by(conn: sqlite3.Connection, key: str) -> list[dict]:
    """Где харнесс задействован: прямые маршруты и роли маршрутов роя."""
    out: list[dict] = []
    for row in conn.execute(
            "SELECT key, kind, harness, roles, driver FROM routes "
            "WHERE harness = ? OR roles LIKE ?",
            (key, f'%"{key}"%')).fetchall():
        if row["kind"] == "direct" and row["harness"] == key:
            out.append({"route": row["key"], "kind": "direct", "role": None})
            continue
        swarm = row["kind"] == "swarm" or row["driver"] == "swarm"
        if swarm and row["roles"]:
            try:
                roles = json.loads(row["roles"])
            except ValueError:
                continue
            for role, cell in roles.items():
                if isinstance(cell, dict) and cell.get("harness") == key:
                    out.append({"route": row["key"], "kind": "swarm", "role": role})
    return out


def _normalize(body: dict, *, existing: dict | None = None,
               kind: str = "exec") -> dict:
    """Собрать значения колонок из тела запроса; `existing` — запись для PATCH."""
    base = existing or {}
    out: dict = {}
    if "label" in body or existing is None:
        value = body.get("label", base.get("label"))
        if value is None:
            value = base.get("key") or ""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("label: нужна непустая строка")
        out["label"] = value.strip()
    if "hint" in body or existing is None:
        value = body.get("hint", base.get("hint") or "")
        if not isinstance(value, str):
            raise ValueError("hint: ожидается строка")
        out["hint"] = value.strip()
    if "icon" in body or existing is None:
        out["icon"] = _icon_of(body.get("icon", base.get("icon")), "icon")
    if "enabled" in body or existing is None:
        out["enabled"] = 1 if body.get("enabled", base.get("enabled", True)) else 0
    # `position` ставится только явным полем; без него `create` берёт MAX+1.
    if "position" in body:
        value = body.get("position")
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("position: ожидается целое число")
        out["position"] = value
    if "argv" in body or "prompt" in body or existing is None:
        argv = _argv_of(body.get("argv", base.get("argv")), "argv")
        prompt = _prompt_of(body.get("prompt", base.get("prompt")), "prompt")
        if kind == "manual" and (argv or prompt):
            raise ValueError("argv/prompt: у ручной выдачи (kind=manual) команды нет")
        out["argv"] = json.dumps(argv, ensure_ascii=False) if argv else None
        out["prompt"] = prompt
    return out


def create(conn: sqlite3.Connection, body: dict) -> dict:
    """Завести харнесс: ключ уникален, `kind`/`builtin` снаружи не ставятся."""
    unknown = [k for k in body
               if k not in ("key", "label", "hint", "icon", "argv", "prompt",
                            "enabled", "position", "kind")]
    if unknown:
        raise errors_mod.BadArgument(f"поле нельзя передать: {unknown[0]}")
    key = body.get("key")
    if not isinstance(key, str) or not routes_mod.KEY_RE.match(key.strip()):
        raise errors_mod.BadArgument(
            f"key: ожидается {routes_mod.KEY_RE.pattern} — имя держателя agent:<key>")
    key = key.strip()
    if by_key(conn, key) is not None:
        raise errors_mod.ListikError(f"харнесс {key!r} уже есть",
                                     code=errors_mod.CONFLICT, status=409)
    kind = body.get("kind", "exec")
    if kind not in KINDS:
        raise errors_mod.BadArgument(f"kind: допустимы {', '.join(KINDS)}")
    values = _normalize({**body, "label": body.get("label", key)},
                        existing=None, kind=kind)
    ts = now_iso()
    position = values.pop("position", None)
    if position is None:
        row = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM harnesses").fetchone()
        position = row[0]
    conn.execute(
        "INSERT INTO harnesses(key, label, hint, icon, argv, prompt, kind, builtin, "
        "enabled, position, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (key, values["label"], values["hint"], values["icon"], values["argv"],
         values["prompt"], kind, 0, values["enabled"], position, ts, ts))
    register_holder(conn, key)
    conn.commit()
    return get(conn, key)


def update(conn: sqlite3.Connection, key: str, body: dict) -> dict:
    unknown = [k for k in body if k not in UPDATE_FIELDS]
    if unknown:
        raise errors_mod.BadArgument(f"поле нельзя менять: {unknown[0]}")
    if not body:
        raise errors_mod.BadArgument("нечего менять")
    existing = get(conn, key)
    values = _normalize(body, existing=existing, kind=existing["kind"])
    if not values:
        return existing
    sets = ", ".join(f"{name} = ?" for name in values)
    conn.execute(
        f"UPDATE harnesses SET {sets}, updated_at = ? WHERE key = ?",
        (*values.values(), now_iso(), key))
    conn.commit()
    return get(conn, key)


def register_holder(conn: sqlite3.Connection, key: str) -> None:
    """Привязать имя харнесса к актору `agent:<key>`.

    Без этого `resolve('foo')` считает голое имя человеком, и `claim` с автором
    `agent:foo` не засчитывал бы карточку «взятой» держателем `foo`
    (`holder_claim_state` сравнивает акторов). Ручная выдача `me` алиаса не
    получает: `me` — канонический человек.
    """
    if not key or key == "me":
        return
    actor = f"agent:{key}"
    actors_mod.remember(conn, key, actor, "agent")
    conn.execute(
        "INSERT INTO actors(key, title, kind, kind_hint) VALUES(?,?,?,?) "
        "ON CONFLICT(key) DO NOTHING",
        (actor, key, "agent", key))


def seed(conn: sqlite3.Connection) -> None:
    """Ввезти поставку: недостающие ключи дописываются, существующие не трогаются."""
    ts = now_iso()
    for position, item in enumerate(SEEDS):
        conn.execute(
            "INSERT INTO harnesses(key, label, hint, icon, argv, prompt, kind, "
            "builtin, enabled, position, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,1,?,?,?)"
            " ON CONFLICT(key) DO NOTHING",
            (item["key"], item["label"], item["hint"], item["icon"],
             json.dumps(item["argv"], ensure_ascii=False) if item["argv"] else None,
             item["prompt"], item["kind"], item["builtin"], position, ts, ts))
        register_holder(conn, item["key"])
    # У pi-glm/pi-deepseek поставочной иконкой был общий «pi»: записи, до сих
    # пор стоящие на нём, поднимаем до собственных глифов — разово (meta-флаг),
    # чтобы не откатывать осознанный выбор «pi» в пикере.
    if conn.execute(
            "SELECT value FROM meta WHERE key = 'seed_pi_variant_icons'").fetchone() is None:
        conn.execute(
            "UPDATE harnesses SET icon = key WHERE builtin = 1 AND icon = 'pi' "
            "AND key IN ('pi-glm', 'pi-deepseek')")
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('seed_pi_variant_icons', '1')")
    # gemini убран из поставки: поставочную запись чистим разово (meta-флаг)
    # вместе с её актор-строками; харнесс, заведённый руками (builtin=0), и его
    # актор/алиас не трогаем. Актор `agent:gemini` в `actors.CANONICAL`
    # отсутствует — иначе `seed_actors` на каждом init создавал бы строку
    # заново (подпись в истории даёт `AGENT_HINTS`/сырой ключ).
    if conn.execute(
            "SELECT value FROM meta WHERE key = 'seed_drop_gemini'").fetchone() is None:
        row = conn.execute(
            "SELECT builtin FROM harnesses WHERE key = 'gemini'").fetchone()
        if row is not None and row["builtin"]:
            conn.execute("DELETE FROM harnesses WHERE key = 'gemini'")
            conn.execute("DELETE FROM actors WHERE key = 'agent:gemini'")
            conn.execute(
                "DELETE FROM actor_aliases "
                "WHERE raw = 'gemini' AND actor = 'agent:gemini'")
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('seed_drop_gemini', '1')")
    # Поставочные argv первых редакций не умели headless: `devin exec --task`
    # и `pi --mode rpc` (RPC по stdio, без клиента выходит с пустым stdout).
    # Поднимаем до рабочих одноразово (meta-флаг) и только там, где argv ещё
    # стоит старый сид — осознанно переписанную команду не откатываем.
    if conn.execute(
            "SELECT value FROM meta WHERE key = 'seed_headless_argv'").fetchone() is None:
        upgrades = {
            "devin": (["devin", "exec", "--task", "{task_id}"],
                      ["devin", "--permission-mode", "dangerous",
                       "--respect-workspace-trust", "false", "-p"],
                      "Devin · devin -p"),
            "pi-glm": (["pi", "--mode", "rpc", "--model", "glm-5.3-flash"],
                       ["pi", "--print", "--no-session", "--model",
                        "b-ai-glm/glm-5.3-flash"],
                       "pi --print · GLM 5.3 Flash"),
            "pi-deepseek": (["pi", "--mode", "rpc", "--model", "deepseek-v4.1-flash"],
                            ["pi", "--print", "--no-session", "--model",
                             "b-ai-deepseek/deepseek-v4.1-flash"],
                            "pi --print · DeepSeek v4.1 Flash"),
        }
        for item in SEEDS:
            entry = upgrades.get(item["key"])
            if entry is None:
                continue
            old_argv, new_argv, new_hint = entry
            conn.execute(
                "UPDATE harnesses SET argv = ?, hint = ?, updated_at = ? "
                "WHERE key = ? AND builtin = 1 AND argv = ?",
                (json.dumps(new_argv, ensure_ascii=False), new_hint, ts,
                 item["key"], json.dumps(old_argv, ensure_ascii=False)))
            # У devin промпта в старом сиде не было — докидываем его вместе с
            # argv, но только если поле так и стоит пустым.
            conn.execute(
                "UPDATE harnesses SET prompt = ?, updated_at = ? "
                "WHERE key = ? AND builtin = 1 AND prompt IS NULL AND ? IS NOT NULL",
                (item["prompt"], ts, item["key"], item["prompt"]))
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('seed_headless_argv', '1')")


def default_role_prompt() -> str:
    """Промпт роли роя по умолчанию — с него предзаполняется карточка роли."""
    return SWARM_PROMPT

"""MCP-сервер Listik: агенты (claude, dsh, grok, codex) работают с задачами инструментами.

JSON-RPC 2.0; поддерживаемые версии протокола MCP — 2025-06-18, 2025-03-26, 2024-11-05
(на `initialize` сервер отвечает версией клиента, если знает её, иначе самой свежей).

Транспортов два, разбор сообщений общий (`handle`):
  * stdio — `bin/listik mcp`; работает через локальную базу: сервер Listik может быть и
    не поднят, а задача должна открываться всегда. Пишет stdio мимо сервера, поэтому
    после пишущего инструмента сам зовёт `POST /api/notify` (в фоне и молча, если
    сервера нет) — иначе доска не узнала бы о записи до перезагрузки (listik-hkdp);
  * HTTP — `POST /mcp` сервера Listik (Streamable HTTP: один POST — одно сообщение,
    ответ обычным JSON, без SSE и сессий) — для Listik, стоящего на другом сервере;
    авторизация заголовком `Authorization: Bearer <токен>` или `X-Listik-Token`.
    Здесь событие доске шлёт сам сервер (`server.Handler._mcp`), уведомлять нечего.

Подключение по stdio (claude / dsh):
  claude mcp add listik -- /путь/к/listik/bin/listik mcp

Подключение к удалённому серверу:
  claude mcp add --transport http listik https://<домен>/mcp --header "Authorization: Bearer <токен>"
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request

from . import config as config_mod
from . import db as db_mod
from . import errors as errors_mod
from . import fence as fence_mod
from . import search as search_mod
from . import store

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "listik", "version": "0.1.0"}

# Инструменты, после которых доске нужно событие: она обновляется по SSE, а не по опросу.
WRITE_TOOLS = frozenset({
    "listik_create", "listik_update", "listik_claim", "listik_heartbeat", "listik_stage",
    "listik_comment", "listik_needs_owner", "listik_done", "listik_release", "listik_deps",
    "listik_put_document",
})

TASK_ID = {"type": "string", "description": "ID задачи, например zoloto585-search-a1b2"}
ACTOR = {"type": "string",
         "description": "кто действует: me, agent:claude, agent:dsh, agent:grok, agent:codex"}

TOOLS: list[dict] = [
    {
        "name": "listik_search",
        "description": ("Гибридный поиск по всем задачам и комментариям сразу во всех проектах "
                        "(BM25 + векторный, слияние RRF). Главный инструмент памяти: искать, "
                        "где уже решалась такая задача, кто её делал и чем кончилось."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "запрос словами"},
                "limit": {"type": "integer", "default": 10},
                "project": {"type": "string"},
                "status": {"type": "string"},
                "stage": {"type": "string"},
                "mode": {"type": "string", "enum": ["hybrid", "text", "vector"], "default": "hybrid"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "listik_list",
        "description": "Список задач с фильтрами. Открытые по умолчанию.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "status": {"type": "string",
                           "enum": ["open", "in_progress", "blocked", "review", "done", "cancelled"]},
                "stage": {"type": "string",
                          "enum": ["s1-spec", "s2-review", "s3-impl", "s4-judge", "done"]},
                "assignee": {"type": "string"},
                "holder": {"type": "string"},
                "needs_owner": {"type": "boolean"},
                "type": {"type": "string"},
                "text": {"type": "string"},
                "include_closed": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 50},
                "order": {"type": "string", "enum": ["updated", "created", "priority", "stage"]},
            },
        },
    },
    {
        "name": "listik_show",
        "description": ("Полная карточка задачи: описание, критерии приёмки, этап и сколько на нём, "
                        "кто держит и как давно, комментарии/журнал, события, зависимости, "
                        "documents[] — статус индексируемых документов (spec/checklist/review/decision). "
                        "fields — оставить только эти поля (список или строки с запятыми); "
                        "неизвестное поле — ошибка bad_argument с перечнем доступных."),
        "inputSchema": {"type": "object",
                        "properties": {"id": TASK_ID,
                                       "fields": {"type": "array", "items": {"type": "string"},
                                                  "description": ("только эти поля карточки, "
                                                                  "напр. [\"launch_route\", \"labels\"]")}},
                        "required": ["id"]},
    },
    {
        "name": "listik_create",
        "description": ("Создать задачу в Listik. spec_path/checklist_path/review_path/decision_path — "
                        "пути к markdown-документам задачи (ТЗ, чек-лист приёмки, ревью, решение); "
                        "они индексируются по разделам и доступны через listik_context/поиск. "
                        "parent — ID карточки шага: новая карточка станет её порцией "
                        "(связь parent-child) со своими документами. discovered_from — ID карточки, "
                        "при работе над которой задачу нашли: связь discovered-from появляется сразу, "
                        "и исходная карточка видит находку в связях. Если в тексте упомянуты чужие "
                        "карточки без связи, в ответе будет link_hints — поставь dep link. route — ключ маршрута из "
                        "таблицы routes: сохраняется в launch_route, а карточка получает метки "
                        "маршрута harness:/process: (как форма «Новая задача» на доске)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "project": {"type": "string"},
                "description": {"type": "string"},
                "acceptance": {"type": "string", "description": "критерии приёмки"},
                "type": {"type": "string", "enum": ["task", "bug", "feature", "epic", "chore",
                                                    "decision", "question"]},
                "priority": {"type": "integer", "minimum": 0, "maximum": 4},
                "stage": {"type": "string", "enum": ["s1-spec", "s2-review", "s3-impl", "s4-judge"]},
                "assignee": ACTOR,
                "labels": {"type": "array", "items": {"type": "string"}},
                "spec_path": {"type": "string"},
                "checklist_path": {"type": "string"},
                "review_path": {"type": "string"},
                "decision_path": {"type": "string"},
                "journal_path": {"type": "string"},
                "parent": {"type": "string",
                           "description": "ID родительской карточки (шаг/эпик): связь parent-child"},
                "discovered_from": {"type": "string",
                                    "description": "ID карточки, при работе над которой найдена эта задача: "
                                                   "сразу ставит мягкую связь discovered-from"},
                "route": {"type": "string",
                          "description": "ключ маршрута из таблицы routes (low-pipeline, dsh, …)"},
                "owner": {"type": "string",
                          "description": "владелец-человек, серверный режим; "
                                         "по умолчанию — представившийся"},
                "actor": ACTOR,
            },
            "required": ["title"],
        },
    },
    {
        "name": "listik_update",
        "description": ("Изменить поля задачи. Каждое изменение попадает в историю. "
                        "status, stage, priority, assignee, holder, labels, needs_owner, "
                        "spec_path, checklist_path, review_path, decision_path, journal_path, "
                        "worktree, branch, result, read_scope, write_scope (списки "
                        "относительных путей от корня проекта, без .., абсолютных путей "
                        "и шаблонов) и т.д."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": TASK_ID,
                "fields": {"type": "object", "description": "пары поле: значение"},
                "note": {"type": "string", "description": "зачем меняем — попадёт в историю"},
                "actor": ACTOR,
                "harness": {"type": "string"},
            },
            "required": ["id", "fields"],
        },
    },
    {
        "name": "listik_context",
        "description": ("Компактный, побайтно стабильный контекст задачи под конкретный этап "
                        "конвейера — карточка, критерии приёмки, зависимости и отобранные разделы "
                        "документов (ТЗ/чек-лист/ревью/решение) вместо файлов целиком; на s4 "
                        "ещё и последний вердикт, журнал и состояние рабочего дерева (на s3 "
                        "этих трёх полей нет). Используй "
                        "вместо listik_show, когда нужен именно рабочий срез под этап."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": TASK_ID,
                "stage": {"type": "string", "enum": ["s1-spec", "s2-review", "s3-impl", "s4-judge"]},
                "portion": {"type": "string",
                            "description": "порция (s3/s4): id/название дочерней карточки-порции "
                                           "или заголовок раздела ТЗ/решения"},
                "max_chars": {"type": "integer",
                             "description": "лимит символов на выбранные блоки; без него — дефолт этапа"},
            },
            "required": ["id", "stage"],
        },
    },
    {
        "name": "listik_put_document",
        "description": ("Передать на сервер текст документа задачи (когда файлов проекта на "
                        "сервере нет); повторный вызов с тем же текстом ревизию не меняет. "
                        "kind: spec, checklist, review, decision — как у полей spec_path/"
                        "checklist_path/review_path/decision_path; без path документ привяжется "
                        "к уже указанному пути или к listik://<id>/<kind>.md."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": TASK_ID,
                "kind": {"type": "string", "enum": ["spec", "checklist", "review", "decision"]},
                "content": {"type": "string", "description": "текст документа целиком"},
                "path": {"type": "string", "description": "путь документа (необязательно)"},
                "actor": ACTOR,
            },
            "required": ["id", "kind", "content"],
        },
    },
    {
        "name": "listik_get_document",
        "description": ("Прочитать документ задачи: загруженный через listik_put_document — из "
                        "базы, файловый — с диска сервера. Возвращает текст, а если файла нет — "
                        "status=missing и текст ошибки."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": TASK_ID,
                "kind": {"type": "string", "enum": ["spec", "checklist", "review", "decision"]},
            },
            "required": ["id", "kind"],
        },
    },
    {
        "name": "listik_claim",
        "description": ("Взять задачу в работу: держатель + статус «в работе». "
                        "Отказывает, если у задачи открытый блокер (обход force), чужой "
                        "держатель или занято рабочее дерево — текст ошибки называет причину "
                        "и варианты. Одновременно держать задачу должен один агент. "
                        "Задачу берёт тот, кто по ней работает: `actor` = `agent:<себя>`, "
                        "`holder` — то же имя. Пока своего claim от держателя нет, карточка "
                        "считается выданной, а не взятой. "
                        "В серверном режиме нужен владелец (заголовок X-Listik-Owner у "
                        "HTTP-транспорта, переменная LISTIK_OWNER у stdio): чужую задачу "
                        "взять нельзя. "
                        "После этого регулярно вызывай listik_heartbeat."),
        "inputSchema": {
            "type": "object",
            "properties": {"id": TASK_ID, "holder": ACTOR, "note": {"type": "string"},
                           "harness": {"type": "string"},
                           "actor": ACTOR,
                           "force": {"type": "boolean",
                                     "description": "взять даже заблокированную задачу (крайний случай, попадёт в историю)"}},
            "required": ["id", "holder"],
        },
    },
    {
        "name": "listik_heartbeat",
        "description": ("Отметка «работаю»: без неё через 24 часа задача считается брошенной "
                        "и попадает в линию «нужен ты». Зови каждые 10-15 минут работы. "
                        "Heartbeat от самого держателя (`actor` = `agent:<себя>`) подтверждает, "
                        "что задача взята, а не только выдана."),
        "inputSchema": {
            "type": "object",
            "properties": {"id": TASK_ID, "holder": ACTOR,
                           "note": {"type": "string", "description": "что делаешь прямо сейчас"},
                           "actor": ACTOR,
                           "harness": {"type": "string", "description": "какой harness работает"}},
            "required": ["id", "holder"],
        },
    },
    {
        "name": "listik_stage",
        "description": ("Перевести задачу на следующий этап конвейера "
                        "(s1-spec → s2-review → s3-impl → s4-judge → done) или на конкретный "
                        "этап через to. Сервер сам считает, сколько задача провела на прошлом этапе. "
                        "`holder` — выдача: держатель ставится и пишется назначение (событие `claim` "
                        "от имени выдающего), а свой claim делает сам исполнитель; на handoff-переходе "
                        "без `holder` держателя снимает. `holder` с `to` на тот же этап — повторная "
                        "выдача: этап не меняется, держатель и новое назначение ставятся заново."),
        "inputSchema": {
            "type": "object",
            "properties": {"id": TASK_ID,
                           "to": {"type": "string",
                                  "enum": ["s1-spec", "s2-review", "s3-impl", "s4-judge", "done"]},
                           "holder": ACTOR, "note": {"type": "string"}, "harness": {"type": "string"},
                           "actor": ACTOR},
            "required": ["id"],
        },
    },
    {
        "name": "listik_comment",
        "description": ("Добавить запись в журнал задачи. kind: comment — обычный комментарий, "
                        "journal — строка журнала конвейера, question — вопрос, answer — ответ, "
                        "review — замечания ревью, verdict — вердикт судьи."),
        "inputSchema": {
            "type": "object",
            "properties": {"id": TASK_ID, "text": {"type": "string"},
                           "kind": {"type": "string", "enum": ["comment", "journal", "question",
                                                               "answer", "review", "verdict"]},
                           "author": ACTOR, "harness": {"type": "string"}},
            "required": ["id", "text"],
        },
    },
    {
        "name": "listik_needs_owner",
        "description": ("Поднять флаг «нужен человек» с формулировкой вопроса — задача встанет "
                        "в линию «нужен ты» на доске. value=false снимает флаг. Текст вопроса "
                        "ложится в историю карточки комментарием kind=question (ответ при "
                        "value=false — kind=answer) и находится поиском."),
        "inputSchema": {
            "type": "object",
            "properties": {"id": TASK_ID, "text": {"type": "string", "description": "вопрос автору"},
                           "value": {"type": "boolean", "default": True}, "actor": ACTOR},
            "required": ["id"],
        },
    },
    {
        "name": "listik_done",
        "description": "Закрыть задачу с результатом в одну-две строки.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": TASK_ID, "result": {"type": "string"},
                           "reason": {"type": "string"},
                           "note": {"type": "string", "description": "итог/пояснение — попадёт в историю"},
                           "actor": ACTOR},
            "required": ["id"],
        },
    },
    {
        "name": "listik_ready",
        "description": ("Что можно взять прямо сейчас: задачи без незакрытых блокеров и без держателя, "
                        "отсортированные по приоритету. Вызывай перед тем, как взять работу, "
                        "чтобы не начать то, что ждёт другую задачу."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "stage": {"type": "string"},
                "harness": {"type": "string", "description": "фильтр по routing проекта: только задачи, чей этап разрешён этому harness; задачи без этапа — всем"},
                "include_occupied": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 30},
            },
        },
    },
    {
        "name": "listik_blocked",
        "description": ("Кто кого ждёт: задачи с незакрытыми блокерами и разбор по каждому блокеру — "
                        "статус, держатель, сколько стоит без движения."),
        "inputSchema": {
            "type": "object",
            "properties": {"project": {"type": "string"}, "limit": {"type": "integer", "default": 50}},
        },
    },
    {
        "name": "listik_can_take",
        "description": ("Можно ли брать конкретную задачу: короткий вердикт и причины "
                        "(кого ждём, кто держит, что зависит от неё). Спрашивай перед claim, "
                        "если сомневаешься."),
        "inputSchema": {"type": "object", "properties": {"id": TASK_ID}, "required": ["id"]},
    },
    {
        "name": "listik_dep_tree",
        "description": "Дерево зависимостей задачи: чего она ждёт и кто ждёт её.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": TASK_ID, "depth": {"type": "integer", "default": 3}},
            "required": ["id"],
        },
    },
    {
        "name": "listik_board",
        "description": ("Состояние доски: что в работе, кто держит, что брошено, что ждёт человека, "
                        "что можно взять (поле ready). "
                        "group_by: status | stage | project | holder. Вызывай в начале сессии, "
                        "чтобы понять обстановку."),
        "inputSchema": {
            "type": "object",
            "properties": {"group_by": {"type": "string", "enum": ["status", "stage", "project",
                                                                   "holder"]},
                           "project": {"type": "string"},
                           "include_closed": {"type": "boolean", "default": False}},
        },
    },
    {
        "name": "listik_stats",
        "description": "Сводка: счётчики по статусам/этапам/проектам/исполнителям, что в работе сейчас.",
        "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}}},
    },
    {
        "name": "listik_deps",
        "description": "Добавить или снять связь между задачами (blocks, related, parent-child). "
                        "Жёсткая связь от агента без confirm записывается как предложение "
                        "(suggested-blocks) до подтверждения человеком; без actor вызов считается "
                        "агентским. confirm — только по решению человека. resource-blocks — "
                        "ресурсный блокер, ставит только планировщик; через этот инструмент не "
                        "принимается.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": TASK_ID, "depends_on": TASK_ID,
                           "dep_type": {"type": "string", "default": "blocks"},
                           "action": {"type": "string", "enum": ["add", "rm"], "default": "add"},
                           "actor": ACTOR,
                           "confirm": {"type": "boolean", "description": "подтвердить жёсткую зависимость"}},
            "required": ["id", "depends_on"],
        },
    },
    {
        "name": "listik_release",
        "description": ("Освободить задачу: снять держателя, не закрывая её. То же, что "
                        "`listik release` — так бросают задачу или передают её другому."),
        "inputSchema": {
            "type": "object",
            "properties": {"id": TASK_ID, "actor": ACTOR,
                           "note": {"type": "string", "description": "почему отпускаешь"}},
            "required": ["id"],
        },
    },
    {
        "name": "listik_inbox",
        "description": ("Что требует человека: вопросы к автору (флаг «нужен ты») и задачи, "
                        "висящие без движения. Две линии доски одним ответом."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "listik_memory",
        "description": ("Долговременная память (заметки вне задач). С query — гибридный поиск, "
                        "без query — последние заметки, свежие сверху."),
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "что искать"},
                           "project": {"type": "string", "description": "точный slug проекта"},
                           "limit": {"type": "integer", "default": 20}},
        },
    },
    {
        "name": "listik_remember",
        "description": ("Записать заметку в долговременную память. Повтор с тем же key "
                        "перезаписывает заметку; без key ключ генерируется."),
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "текст заметки"},
                           "key": {"type": "string"},
                           "project": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "listik_projects",
        "description": "Проекты доски: slug, путь, счётчики задач; скрытые — по флагу.",
        "inputSchema": {
            "type": "object",
            "properties": {"include_archived": {"type": "boolean", "default": False}},
        },
    },
    {
        "name": "listik_actors",
        "description": "Исполнители и агенты: кто есть в базе и сколько задач на ком.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "listik_timeline",
        "description": "Лента последних событий по всем задачам: этапы, статусы, комментарии.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 100}},
        },
    },
    {
        "name": "listik_deps_suggested",
        "description": ("Предложенные блокеры (suggested-blocks): агент предложил, человек ещё "
                        "не подтвердил. Что именно ждёт решения по зависимостям."),
        "inputSchema": {
            "type": "object",
            "properties": {"project": {"type": "string"},
                           "limit": {"type": "integer", "default": 100}},
        },
    },
    {
        "name": "listik_cycles",
        "description": "Циклы в графе зависимостей: задача ждёт саму себя по кругу — разрывать руками.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "listik_waves",
        "description": ("Волны запуска проекта: какие задачи можно делать одновременно. "
                        "Разложение по жёстким зависимостям (Кан) плюс разведение по волнам "
                        "задач с пересекающимся write_scope или одним рабочим деревом. Без "
                        "apply ничего не пишет. unroutable — без маршрута (нужен человек), "
                        "unscoped — без write_scope (нужен rescope), blocked — стоят за "
                        "задачей вне плана, cycles — цикл в графе: волны не считаются, "
                        "разрывать руками."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "stage": {"type": "string"},
                "apply": {"type": "boolean", "default": False,
                         "description": "записать ресурсные рёбра resource-blocks под этот "
                         "расчёт (устаревшие снять, недостающие поставить, автор ребра всегда "
                         "agent:listik-swarm); при цикле — отказ, ничего не пишется"},
            },
            "required": ["project"],
        },
    },
]


class _FromEnv:
    """Метка «транспорт владельца не передавал» — только у stdio."""

    def __repr__(self) -> str:  # pragma: no cover — для отладочного вывода
        return "FROM_ENV"


FROM_ENV = _FromEnv()


def _conn():
    return db_mod.init()


#: Инструменты, ограждаемые по поколению запуска (см. `listik/fence.py`): `guard`
#: зовётся до store, `op` — имя инструмента без префикса `listik_`. `listik_deps`
#: ограждается только в ветке добавления связи (см. ниже, отдельно).
FENCED_TOOLS = {
    "listik_update": "update", "listik_claim": "claim", "listik_heartbeat": "heartbeat",
    "listik_stage": "stage", "listik_comment": "comment",
    "listik_needs_owner": "needs_owner", "listik_done": "done",
    "listik_release": "release", "listik_put_document": "put_document",
}


def call_tool(name: str, args: dict, conn=None, owner=FROM_ENV, fence=FROM_ENV) -> object:
    """`owner` — владелец-человек, от чьего имени идёт вызов (серверный режим).

    Источник имени определяет транспорт, а не пустота значения. У HTTP владелец
    приходит только заголовком `X-Listik-Owner` (его передаёт `server.Handler._mcp`),
    и `owner=None` там значит «клиент не представился» — окружение сервера в это
    место не подставляется. У stdio транспорт имени не несёт: вызов идёт без
    аргумента (`FROM_ENV`), и имя берётся из `LISTIK_OWNER` — там же, где `LISTIK_ACTOR`.

    `fence` — тот же принцип для токена поколения запуска: stdio без аргумента
    берёт его из окружения (`fence.from_env`), HTTP передаёт значение заголовков
    (может быть `None`, если их нет). `listik_show` карантин не отдаёт никогда —
    для него ограждение не нужно, у чтения нечего отвергать.
    """
    if conn is None:
        conn = _conn()
    if owner is FROM_ENV:
        owner = (os.environ.get("LISTIK_OWNER") or "").strip() or None
    if fence is FROM_ENV:
        fence = fence_mod.from_env()
    guard_op = FENCED_TOOLS.get(name)
    if guard_op is None and name == "listik_deps" and args.get("action") != "rm":
        guard_op = "deps"
    if guard_op is not None:
        fence_mod.guard(conn, args.get("id"), fence, op=guard_op, args=args,
                        actor=args.get("actor") or owner, harness=args.get("harness"))
    if name == "listik_search":
        return search_mod.search(conn, args["query"], limit=int(args.get("limit", 10)),
                                 project=args.get("project"), status=args.get("status"),
                                 stage=args.get("stage"), mode=args.get("mode", "hybrid"))
    if name == "listik_list":
        return store.list_tasks(
            conn, project=args.get("project"), status=args.get("status"),
            stage=args.get("stage"), assignee=args.get("assignee"), holder=args.get("holder"),
            needs_owner=bool(args.get("needs_owner")), issue_type=args.get("type"),
            text=args.get("text"), include_closed=bool(args.get("include_closed")),
            limit=int(args.get("limit", 50)), order=args.get("order", "updated"),
            as_owner=owner)
    if name == "listik_show":
        from . import deps as deps_mod
        task = store.get_task(conn, args["id"])
        task["deps_state"] = deps_mod.ready(conn, args["id"])
        return store.select_task_fields(task, args.get("fields"))
    if name == "listik_create":
        return store.create_task(
            conn, title=args["title"], project=args.get("project"),
            description=args.get("description", ""), acceptance=args.get("acceptance", ""),
            issue_type=args.get("type", "task"), priority=int(args.get("priority", 2)),
            stage=args.get("stage"), assignee=_norm_actor(args.get("assignee")),
            labels=args.get("labels") or [], spec_path=args.get("spec_path"),
            checklist_path=args.get("checklist_path"), review_path=args.get("review_path"),
            decision_path=args.get("decision_path"), journal_path=args.get("journal_path"),
            parent=args.get("parent"), discovered_from=args.get("discovered_from"),
            route=args.get("route"), hints=True, created_by=args.get("actor"),
            owner=args.get("owner"), as_owner=owner)
    if name == "listik_update":
        return store.update_task(conn, args["id"], actor=args.get("actor"),
                                 harness=args.get("harness"), note=args.get("note"),
                                 as_owner=owner,
                                 # `route` — алиас колонки launch_route, как в POST /api/tasks.
                                 **{k: v for k, v in (args.get("fields") or {}).items()
                                    if k in store.UPDATABLE or k == store.ROUTE_ALIAS})
    if name == "listik_context":
        from . import documents as documents_mod
        return documents_mod.context(conn, args["id"], args["stage"],
                                     portion=args.get("portion"),
                                     max_chars=args.get("max_chars"))
    if name == "listik_put_document":
        from . import documents as documents_mod
        return documents_mod.put_document(conn, args["id"], args["kind"], args["content"],
                                          path=args.get("path"), actor=args.get("actor"))
    if name == "listik_get_document":
        from . import documents as documents_mod
        return documents_mod.get_document(conn, args["id"], args["kind"])
    if name == "listik_claim":
        return store.claim(conn, args["id"], holder=_norm_actor(args["holder"]),
                           harness=args.get("harness"), note=args.get("note"),
                           actor=args.get("actor"), as_owner=owner,
                           force=bool(args.get("force")))
    if name == "listik_ready":
        from . import deps as deps_mod
        return {"tasks": deps_mod.ready_tasks(
                    conn, project=args.get("project"), stage=args.get("stage"),
                    harness=args.get("harness"),
                    include_occupied=bool(args.get("include_occupied")),
                    limit=int(args.get("limit", 30)), as_owner=owner),
                "cycles": deps_mod.cycles(conn)}
    if name == "listik_blocked":
        from . import deps as deps_mod
        return {"tasks": deps_mod.blocked_tasks(conn, project=args.get("project"),
                                                limit=int(args.get("limit", 50)))}
    if name == "listik_can_take":
        from . import deps as deps_mod
        state = deps_mod.ready(conn, args["id"])
        return {k: state[k] for k in ("task_id", "title", "status", "stage", "ready", "claimable",
                                      "can_finish", "verdict", "reasons", "holder_title",
                                      "holder_age", "stale_holder", "blocked_by", "children_open")}
    if name == "listik_dep_tree":
        from . import deps as deps_mod
        return deps_mod.graph(conn, args["id"], depth=int(args.get("depth", 3)))
    if name == "listik_heartbeat":
        return store.heartbeat(conn, args["id"], holder=_norm_actor(args["holder"]),
                               note=args.get("note"), harness=args.get("harness"),
                               actor=args.get("actor"), as_owner=owner)
    if name == "listik_stage":
        if args.get("to"):
            return store.next_stage(conn, args["id"], holder=_norm_actor(args.get("holder")),
                                    harness=args.get("harness"), note=args.get("note"),
                                    actor=args.get("actor"), as_owner=owner,
                                    to_stage=args["to"])
        return store.next_stage(conn, args["id"], holder=_norm_actor(args.get("holder")),
                                note=args.get("note"), harness=args.get("harness"),
                                actor=args.get("actor"), as_owner=owner)
    if name == "listik_comment":
        return store.add_comment(conn, args["id"], args["text"],
                                 author=args.get("author"), kind=args.get("kind", "comment"),
                                 harness=args.get("harness"))
    if name == "listik_needs_owner":
        # Без явного actor вопрос/ответ не должен остаться без автора: MCP —
        # транспорт агентов, поэтому имя берём у того, кто представился
        # транспортом (owner: X-Listik-Owner у HTTP, LISTIK_OWNER у stdio), затем
        # из LISTIK_ACTOR и, наконец, агентским фолбэком — как у listik_deps.
        # `_norm_actor` приводит имя к каноническому ключу, чтобы комментарий и
        # событие question/answer подписывались одним и тем же актором.
        actor = _norm_actor(args.get("actor") or owner
                            or os.environ.get("LISTIK_ACTOR") or "agent:mcp")
        return store.set_needs_owner(conn, args["id"], value=bool(args.get("value", True)),
                                     text=args.get("text"), actor=actor)
    if name == "listik_done":
        return store.update_task(conn, args["id"], actor=args.get("actor"), status="done",
                                 stage="done", result=args.get("result", ""),
                                 close_reason=args.get("reason") or args.get("result", ""),
                                 note=args.get("note"))
    if name == "listik_release":
        return store.update_task(conn, args["id"], actor=args.get("actor"), holder="",
                                 note=args.get("note") or "освободил")
    if name == "listik_inbox":
        res = store.board(conn, group_by="status", include_closed=False)
        items = res.get("needs_you") or []
        return {"questions": [t for t in items if t.get("needs_owner")],
                "dropped": [t for t in items if not t.get("needs_owner")]}
    if name == "listik_memory":
        query = args.get("query")
        limit = int(args.get("limit", 20))
        project = args.get("project")
        if query:
            return {"items": search_mod.search_memories(conn, query, limit=limit,
                                                        project=project)}
        rows = conn.execute(
            "SELECT key, project, body, updated_at FROM memories "
            + ("WHERE project = ? " if project else "")
            + "ORDER BY updated_at DESC LIMIT ?",
            ([project] if project else []) + [limit]).fetchall()
        return {"items": [dict(r) for r in rows]}
    if name == "listik_remember":
        return store.remember(conn, args["text"], key=args.get("key"),
                              project=args.get("project"))
    if name == "listik_board":
        return store.board(conn, group_by=args.get("group_by", "status"),
                           project=args.get("project"),
                           include_closed=bool(args.get("include_closed")),
                           as_owner=owner)
    if name == "listik_stats":
        return store.stats(conn, project=args.get("project"))
    if name == "listik_projects":
        return {"projects": store.list_projects(conn,
                                                include_archived=bool(args.get("include_archived")))}
    if name == "listik_actors":
        return {"actors": store.list_actors(conn)}
    if name == "listik_timeline":
        return {"items": store.task_timeline(conn, limit=int(args.get("limit", 100)))}
    if name == "listik_deps":
        # MCP — транспорт только для агентов: без явного actor вызов всё равно
        # должен считаться агентским, а не тихо превращаться в «человека».
        actor = args.get("actor") or os.environ.get("LISTIK_ACTOR") or "agent:mcp"
        if args.get("action") == "rm":
            return store.remove_dep(conn, args["id"], args["depends_on"],
                                    args.get("dep_type"))
        return store.add_dep(conn, args["id"], args["depends_on"],
                             args.get("dep_type", "blocks"), actor,
                             confirm=bool(args.get("confirm")))
    if name == "listik_deps_suggested":
        from . import deps as deps_mod
        return {"items": deps_mod.suggested(conn, project=args.get("project"),
                                            limit=int(args.get("limit", 100)))}
    if name == "listik_cycles":
        from . import deps as deps_mod
        return {"cycles": deps_mod.cycles(conn)}
    if name == "listik_waves":
        from . import deps as deps_mod
        if args.get("apply"):
            return deps_mod.apply_resource_blocks(
                conn, project=args.get("project") or "", stage=args.get("stage"))
        return deps_mod.waves(conn, project=args.get("project") or "", stage=args.get("stage"))
    raise ValueError(f"неизвестный инструмент: {name}")


def _norm_actor(value: str | None) -> str | None:
    if not value:
        return None
    from . import actors as actors_mod
    key, _ = actors_mod.resolve(value, None)
    return key


def _result(payload: object) -> dict:
    if isinstance(payload, list):
        payload = {"items": payload}
    text = errors_mod.json_dumps(payload, indent=2)
    return {"content": [{"type": "text", "text": text}]}


def request_parts(request: dict) -> tuple[str | None, object, dict]:
    """Достаёт метод, id и параметры JSON-RPC одинаково для обоих транспортов."""
    return request.get("method"), request.get("id"), request.get("params") or {}


def rpc_error(rid, code: int, message: str) -> dict:
    """Формирует JSON-RPC ошибку; HTTP-транспорт только добавляет статус."""
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle(request: dict, conn=None, owner=FROM_ENV, fence=FROM_ENV) -> dict | None:
    """`owner`/`fence` — см. `call_tool`: HTTP всегда передаёт значение заголовков
    (в том числе `None`, если их нет), stdio вызывает без аргумента."""
    method, rid, params = request_parts(request)

    if method == "initialize":
        # Согласование версии: клиент получает свою же версию, если Listik её знает,
        # иначе — самую свежую из поддерживаемых. Работает и для stdio, и для HTTP.
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }}
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            payload = call_tool(name, args, conn, owner, fence)
        except errors_mod.NotFound as exc:
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": errors_mod.mcp_error_text(exc)}],
                               "isError": True}}
        except Exception as exc:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text",
                                            "text": errors_mod.mcp_error_text(exc)}],
                               "isError": True}}
        return {"jsonrpc": "2.0", "id": rid, "result": _result(payload)}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if rid is None:
        return None
    return rpc_error(rid, -32601, f"неизвестный метод: {method}")


def _result_id(result: dict) -> str | None:
    """id задачи из ответа `listik_create`: доска ждёт именно его."""
    try:
        payload = errors_mod.json_loads(result["content"][0]["text"])
    except Exception:  # noqa: BLE001 — ответ не той формы: события не будет
        return None
    task_id = payload.get("id") if isinstance(payload, dict) else None
    return task_id if isinstance(task_id, str) and task_id else None


def notify_event(request: dict, response: dict | None) -> tuple[str, str] | None:
    """Событие доске по вызову инструмента — `(task_id, action)` или None.

    Правила те же, что у HTTP-транспорта (`server.Handler._mcp`): пишущий
    инструмент из `WRITE_TOOLS`, успешный ответ и известный id задачи. Чтения и
    ответы `isError` доску не будят.
    """
    if not isinstance(request, dict) or request.get("method") != "tools/call":
        return None
    _method, _rid, params = request_parts(request)
    name = params.get("name")
    if name not in WRITE_TOOLS:
        return None
    result = (response or {}).get("result") or {}
    if result.get("isError"):
        return None
    args = params.get("arguments") or {}
    task_id = _result_id(result) if name == "listik_create" else args.get("id")
    return (task_id, name) if isinstance(task_id, str) and task_id else None


#: Сколько ждём сервер, пока сообщаем ему о записи. Ответ инструмента этой
#: отправки не ждёт — поток фоновый, поэтому таймаут короткий: лучше потерять
#: событие, чем копить висящие потоки у сервера, который не отвечает.
NOTIFY_TIMEOUT = 1.5

#: Незавершённые отправки: их дожидается `wait_pending_notifies` на выходе.
_pending_lock = threading.Lock()
_pending: list[threading.Thread] = []


def _post_notify(task_id: str, action: str) -> None:
    """Один POST /api/notify; любая ошибка — тихий пропуск.

    stdio-MCP работает с локальной базой (`paths.DB_PATH`), поэтому и сервер
    ищется локальный — по `[server]`/`[auth]` из config.toml, как ходит CLI.
    В try — весь путь, включая чтение конфига: битый config.toml не должен
    ронять фоновый поток трейсбеком в stderr.
    """
    try:
        cfg = config_mod.load()
        host = str(cfg["server"]["host"] or "127.0.0.1")
        if host in ("0.0.0.0", "::", "*"):
            host = "127.0.0.1"  # «слушать везде» — не адрес, по которому ходят
        body = errors_mod.json_dumps({"task_id": task_id, "action": action}).encode("utf-8")
        req = urllib.request.Request(
            f"http://{host}:{int(cfg['server']['port'])}/api/notify",
            data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        token = (cfg.get("auth") or {}).get("token") or ""
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=NOTIFY_TIMEOUT):
            pass
    except Exception:  # noqa: BLE001 — сервер не поднят или отказал: доска не при чём
        pass


def notify_board(request: dict, response: dict | None) -> None:
    """Сказать серверу о записи — в фоне и только если он отвечает.

    Зовётся из stdio-цикла после ответа инструмента: сервер разошлёт кадр
    подписчикам SSE (`GET /api/stream`), и доска перечитает карточку без
    перезагрузки. Сервера нет, запрос не прошёл — инструмент всё равно ответил.
    """
    event = notify_event(request, response)
    if event is None:
        return
    thread = threading.Thread(target=_post_notify, args=event, name="listik-notify",
                              daemon=True)
    with _pending_lock:
        _pending[:] = [t for t in _pending if t.is_alive()]
        _pending.append(thread)
    thread.start()


def wait_pending_notifies(timeout: float = NOTIFY_TIMEOUT + 0.5) -> None:
    """Дождаться фоновых уведомлений перед выходом из stdio-цикла.

    Клиент может закрыть stdin сразу после записи (`echo … | listik mcp`) — без
    этой паузы процесс убил бы фоновый поток вместе с неотправленным событием.
    Пауза ограничена таймаутом самого запроса: висящий сервер задержит выход не
    дольше, чем задержал бы один POST.
    """
    deadline = time.monotonic() + timeout
    while True:
        with _pending_lock:
            alive = [t for t in _pending if t.is_alive()]
        if not alive:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        alive[0].join(remaining)


def run() -> int:
    # Одно соединение на весь stdio-процесс: без него call_tool открывал бы
    # новое соединение на каждый вызов и не закрывал его (listik-sxcd).
    # Маршруты `listik_create` берёт из таблицы `routes` через это же соединение —
    # метки те же, что у формы на доске (см. routes.labels_for).
    conn = db_mod.init()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = errors_mod.json_loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(request, conn=conn)
        if response is None:
            continue
        sys.stdout.write(errors_mod.json_dumps(response) + "\n")
        sys.stdout.flush()
        # Событие — строго после ответа и в фоне: доска не должна задерживать
        # инструмент, а сервер, который не отвечает, — ломать его (listik-hkdp).
        notify_board(request, response)
    wait_pending_notifies()
    conn.close()
    return 0

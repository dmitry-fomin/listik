"""MCP-сервер Listik: агенты (claude, dsh, grok, codex) работают с задачами инструментами.

JSON-RPC 2.0; поддерживаемые версии протокола MCP — 2025-06-18, 2025-03-26, 2024-11-05
(на `initialize` сервер отвечает версией клиента, если знает её, иначе самой свежей).

Транспортов два, разбор сообщений общий (`handle`):
  * stdio — `bin/listik mcp`; работает через локальную базу: сервер Listik может быть и
    не поднят, а задача должна открываться всегда. Пишет stdio мимо сервера, поэтому
    после пишущего инструмента сам зовёт `POST /api/notify` (в фоне и молча, если
    сервера нет) — иначе доска не узнала бы о записи до перезагрузки (listik-hkdp).
    Если `.listik.toml` каталога агента ведёт на общий сервер (`client.resolve_target`),
    stdio становится прокси: каждое сообщение уходит `POST <сервер>/mcp`, локальная
    база не открывается (listik-r69k, порция d);
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

import http.client
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request

from . import __version__
from . import client as client_mod
from . import config as config_mod
from . import db as db_mod
from . import errors as errors_mod
from . import fence as fence_mod
from . import search as search_mod
from . import store
from .statuses import ALL_STATUSES

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "listik", "version": __version__}

# Инструменты, после которых доске нужно событие: она обновляется по SSE, а не по опросу.
WRITE_TOOLS = frozenset({
    "listik_create", "listik_update", "listik_claim", "listik_heartbeat", "listik_stage",
    "listik_comment", "listik_needs_owner", "listik_done", "listik_release", "listik_deps",
    "listik_put_document", "listik_waves", "listik_delete", "listik_portions",
    "listik_restart", "listik_mentions",
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
                "status": {"type": "string", "enum": list(ALL_STATUSES)},
                "stage": {"type": "string",
                          "enum": ["s1-spec", "s2-review", "s3-impl", "s4-judge", "done"]},
                "orchestrator": {"type": "string",
                                 "description": "кто ведёт карточку по маршруту: listik | claude"
                                                " | none (без оркестратора)"},
                "holder": {"type": "string"},
                "needs_owner": {"type": "boolean"},
                "type": {"type": "string"},
                "text": {"type": "string"},
                "include_closed": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 50,
                          "description": "сколько задач; 0 — страница по умолчанию (200)"},
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
                "type": {"type": "string", "enum": list(store.ISSUE_TYPES)},
                "priority": {"type": "integer", "minimum": 0, "maximum": 4},
                "stage": {"type": "string", "enum": ["s1-spec", "s2-review", "s3-impl", "s4-judge"]},
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
                          "description": "ключ маршрута из таблицы routes (full-low, dsh, …)"},
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
                        "status, stage, priority, holder, labels, needs_owner, "
                        "spec_path, checklist_path, review_path, decision_path, journal_path, "
                        "worktree, branch, result, read_scope, write_scope (списки "
                        "относительных путей от корня проекта, без .., абсолютных путей "
                        "и шаблонов) и т.д. Значение null в fields — ошибка "
                        "(bad_argument); очистить поле — пустой строкой \"\"."),
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
                        "review — замечания ревью, verdict — вердикт судьи. Без author автором "
                        "считается агент (LISTIK_ACTOR, иначе agent:mcp); владелец транспорта "
                        "(X-Listik-Owner/LISTIK_OWNER) автором не становится. verdict от агента "
                        "принимается только на этапе s4-judge, на другом этапе сохраняется "
                        "обычным комментарием с verdict_accepted=false."),
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
                           "limit": {"type": "integer", "default": 20,
                                     "description": "сколько заметок; 0 — страница по умолчанию (20)"}},
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
                           "limit": {"type": "integer", "default": 100,
                                     "description": "сколько предложений; 0 — страница по умолчанию (100)"}},
        },
    },
    {
        "name": "listik_cycles",
        "description": "Циклы в графе зависимостей: задача ждёт саму себя по кругу — разрывать руками.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "listik_waves",
        "description": ("Волны запуска проекта: какие карточки роя можно делать "
                        "одновременно (план считается только по карточкам роя, остальные "
                        "задачи в него не входят). "
                        "Разложение по жёстким зависимостям (Кан) плюс разведение по волнам "
                        "задач с пересекающимся write_scope или одним рабочим деревом. Без "
                        "apply ничего не пишет. "
                        "unscoped — без write_scope на s3-impl/s4-judge (нужен rescope; "
                        "ТЗ и критика идут без области), blocked — стоят за "
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
    {
        "name": "listik_delete",
        "description": ("Удалить задачу целиком: комментарии, события, документы, связи — "
                        "то же, что DELETE /api/tasks/{id}. Необратимо. В серверном режиме "
                        "чужую задачу удалить нельзя. Через stdio без поднятого сервера "
                        "задача удаляется прямо из базы, и доска узнает об удалении только "
                        "при следующей загрузке."),
        "inputSchema": {
            "type": "object",
            "properties": {"id": TASK_ID},
            "required": ["id"],
        },
    },
    {
        "name": "listik_mentions",
        "description": ("Задачи, упомянутые в тексте этой, но не связанные с ней. Без link "
                        "только читает (ответ items). С link — как `listik dep link --json`: "
                        "ставит связь dep_type (по умолчанию relates-to) с каждой упомянутой "
                        "(с only — только с ней); жёсткая связь от агента записывается "
                        "предложением. Ответ: candidates, linked, made, skipped (кандидат, "
                        "которого нет или с которым связь невозможна)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": TASK_ID,
                "limit": {"type": "integer", "default": 50},
                "link": {"type": "boolean", "default": False,
                         "description": "связать с упомянутыми задачами"},
                "dep_type": {"type": "string", "default": "relates-to"},
                "only": {"type": "string",
                         "description": "ID одной упомянутой задачи: связать только с ней"},
                "actor": ACTOR,
            },
            "required": ["id"],
        },
    },
    {
        "name": "listik_portions",
        "description": ("Порции шага. sync — завести карточки порций по файлам "
                        "`<id>.<X>.md` рядом со spec_path шага (идемпотентно, как "
                        "`listik portions sync`); adopt — принять нарезку карточки роя "
                        "(`listik portions adopt`). В серверном режиме чужую задачу "
                        "нарезать нельзя."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": TASK_ID,
                "action": {"type": "string", "enum": ["sync", "adopt"]},
                "actor": ACTOR,
                "harness": {"type": "string"},
            },
            "required": ["id", "action"],
        },
    },
    {
        "name": "listik_restart",
        "description": ("Перезапустить карточку роя с этапа (`listik restart`): этап "
                        "выбран, держателя нет, запуска нет — рой запустит роль этапа "
                        "на следующем тике. route — сменить маршрут. В серверном режиме "
                        "чужую задачу перезапустить нельзя."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": TASK_ID,
                "stage": {"type": "string", "enum": ["s1-spec", "s2-review", "s3-impl", "s4-judge"]},
                "route": {"type": "string", "description": "ключ маршрута из таблицы routes"},
                "note": {"type": "string", "description": "зачем перезапускаем"},
                "actor": ACTOR,
                "harness": {"type": "string"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "listik_revoke",
        "description": ("Отозвать полномочия текущего запуска задачи (`listik revoke`): "
                        "поколение поднимается, записи старого процесса уходят в карантин; "
                        "kill (по умолчанию true) снимает и сам процесс. Выполняет только "
                        "сервер Listik: процесс задачи держит он. В серверном режиме чужую "
                        "задачу отозвать нельзя."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": TASK_ID,
                "note": {"type": "string", "description": "почему отзываем"},
                "kill": {"type": "boolean", "default": True},
                "actor": ACTOR,
                "harness": {"type": "string"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "listik_launch",
        "description": ("Запустить процесс задачи по её маршруту (`listik launch`). Ответ — "
                        "карточка с launched: true (у карточки роя — с исходом). Отказ — "
                        "ошибка с кодом already_launched (уже запущена) или conflict. "
                        "Выполняет только сервер Listik: процесс задачи держит он. В "
                        "серверном режиме чужую задачу запустить нельзя."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": TASK_ID,
                "env": {"type": "object", "additionalProperties": {"type": "string"},
                        "description": "окружение процесса: только ключи LISTIK_*"},
            },
            "required": ["id"],
        },
    },
]

#: Управляющие инструменты → глагол отказа «чужую задачу <глагол> нельзя»
#: (`store.check_owner_action`, как у HTTP-ручек).
OWNER_TOOLS = {
    "listik_delete": "удалить", "listik_portions": "нарезать на порции",
    "listik_restart": "перезапустить", "listik_revoke": "отозвать",
    "listik_launch": "запустить",
}


class _FromEnv:
    """Метка «транспорт владельца не передавал» — только у stdio."""

    def __repr__(self) -> str:  # pragma: no cover — для отладочного вывода
        return "FROM_ENV"


FROM_ENV = _FromEnv()


def _conn():
    return db_mod.init()


def _int_arg(args: dict, key: str, default: int) -> int:
    """Числовой аргумент инструмента: `null` — то же, что ключ не передан."""
    value = args.get(key)
    return default if value is None else int(value)


def call_tool(name: str, args: dict, conn=None, owner=FROM_ENV, fence=FROM_ENV,
              notify=None) -> object:
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

    `notify` — публикация кадра доски (`server.publish`); её передаёт только процесс
    сервера (HTTP-транспорт). С ней `listik_launch`/`listik_revoke`/`listik_delete`
    выполняются здесь же; без неё (stdio) уходят запросом к своему серверу.
    """
    # `null` в аргументе = ключ не передан (listik-ds5l, listik-ngm2)
    args = {k: v for k, v in args.items() if v is not None}
    if conn is None:
        conn = _conn()
        try:
            return call_tool(name, args, conn, owner, fence, notify)
        except Exception:
            # Соединение открыл сам вызов: его незакоммиченная запись держала бы
            # блокировку записи (listik-mqr6). Переданное снаружи откатывает
            # вызывающий (`handle`), здесь оно доходит только с `conn=None`.
            try:
                conn.rollback()
            except sqlite3.Error:
                pass  # откат не удался — наружу исходная ошибка
            raise
    if owner is FROM_ENV:
        owner = (os.environ.get("LISTIK_OWNER") or "").strip() or None
    if fence is FROM_ENV:
        fence = fence_mod.from_env()
    # Ограждаемые инструменты и имена их операций — `fence.OPS["mcp"]`; `guard` до store.
    fenced = _fence_key(name, args)
    if fenced in fence_mod.OPS["mcp"]:
        fence_mod.guard(conn, args.get("id"), fence, op=fence_mod.OPS["mcp"][fenced], args=args,
                        actor=args.get("actor") or owner, harness=args.get("harness"))
    if name in OWNER_TOOLS:
        # Чужую задачу нельзя удалить, запустить, отозвать, перезапустить и нарезать —
        # до любого эффекта, как у HTTP-ручек.
        store.check_owner_action(conn, args.get("id"), owner, action=OWNER_TOOLS[name])
    if name == "listik_search":
        return search_mod.search(conn, args["query"], limit=_int_arg(args, "limit", 10),
                                 project=args.get("project"), status=args.get("status"),
                                 stage=args.get("stage"), mode=args.get("mode", "hybrid"))
    if name == "listik_list":
        return store.list_tasks(
            conn, project=args.get("project"), status=args.get("status"),
            stage=args.get("stage"), orchestrator=args.get("orchestrator"),
            holder=args.get("holder"),
            needs_owner=bool(args.get("needs_owner")), issue_type=args.get("type"),
            text=args.get("text"), include_closed=bool(args.get("include_closed")),
            limit=_int_arg(args, "limit", 50) or 200, order=args.get("order", "updated"),
            as_owner=owner)
    if name == "listik_show":
        task = store.get_task(conn, args["id"])
        return store.select_task_fields(task, args.get("fields"))
    if name == "listik_create":
        return store.create_task(
            conn, title=args["title"], project=args.get("project"),
            description=args.get("description", ""), acceptance=args.get("acceptance", ""),
            issue_type=args.get("type", "task"), priority=_int_arg(args, "priority", 2),
            stage=args.get("stage"),
            labels=args.get("labels") or [], spec_path=args.get("spec_path"),
            checklist_path=args.get("checklist_path"), review_path=args.get("review_path"),
            decision_path=args.get("decision_path"), journal_path=args.get("journal_path"),
            parent=args.get("parent"), discovered_from=args.get("discovered_from"),
            route=args.get("route"), hints=True, created_by=args.get("actor"),
            owner=args.get("owner"), as_owner=owner)
    if name == "listik_update":
        fields = args.get("fields") or {}
        # actor/harness/note — аргументы инструмента, внутри `fields` это ошибка.
        store.check_update_fields(fields)
        # Вложенный null — отказ до записи: по контракту HTTP он значит «поле не
        # передано», и `{"spec_path": null}` был бы молчаливым no-op (listik-8w15).
        null_keys = sorted(k for k, v in fields.items() if v is None)
        if null_keys:
            raise errors_mod.BadArgument(
                "null в fields не поддерживается: " + ", ".join(null_keys)
                + " (очистить строковое поле можно пустой строкой \"\")")
        return store.update_task(conn, args["id"], actor=args.get("actor"),
                                 harness=args.get("harness"), note=args.get("note"),
                                 as_owner=owner, **fields)
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
                    include_occupied=bool(args.get("include_occupied")),
                    limit=_int_arg(args, "limit", 30), as_owner=owner),
                "cycles": deps_mod.cycles(conn)}
    if name == "listik_blocked":
        from . import deps as deps_mod
        return {"tasks": deps_mod.blocked_tasks(conn, project=args.get("project"),
                                                limit=_int_arg(args, "limit", 50))}
    if name == "listik_can_take":
        from . import deps as deps_mod
        state = deps_mod.ready(conn, args["id"])
        return {k: state[k] for k in ("task_id", "title", "status", "stage", "ready", "claimable",
                                      "can_finish", "verdict", "reasons", "holder_title",
                                      "holder_age", "stale_holder", "blocked_by", "children_open")}
    if name == "listik_dep_tree":
        from . import deps as deps_mod
        return deps_mod.graph(conn, args["id"], depth=_int_arg(args, "depth", 3))
    if name == "listik_heartbeat":
        return store.heartbeat(conn, args["id"], holder=_norm_actor(args["holder"]),
                               note=args.get("note"), harness=args.get("harness"),
                               actor=args.get("actor"), as_owner=owner)
    if name == "listik_stage":
        return store.next_stage(conn, args["id"], holder=_norm_actor(args.get("holder")),
                                harness=args.get("harness"), note=args.get("note"),
                                actor=args.get("actor"), as_owner=owner,
                                to_stage=args.get("to") or None)
    if name == "listik_comment":
        return store.add_comment(conn, args["id"], args["text"],
                                 author=_mcp_actor(args.get("author"), args.get("actor")),
                                 kind=args.get("kind", "comment"),
                                 harness=args.get("harness"))
    if name == "listik_needs_owner":
        # Автор — см. `_mcp_actor`.
        actor = _mcp_actor(args.get("actor"), owner)
        return store.set_needs_owner(conn, args["id"], value=bool(args.get("value", True)),
                                     text=args.get("text"), actor=actor)
    if name == "listik_done":
        return store.close_task(conn, args["id"], actor=args.get("actor"),
                                note=args.get("note"), result=args.get("result"),
                                reason=args.get("reason"), as_owner=owner)
    if name == "listik_release":
        return store.release_task(conn, args["id"], actor=args.get("actor"),
                                  note=args.get("note"), as_owner=owner)
    if name == "listik_inbox":
        res = store.board(conn, group_by="status", include_closed=False)
        items = res.get("needs_you") or []
        return {"questions": [t for t in items if t.get("needs_owner")],
                "dropped": [t for t in items if not t.get("needs_owner")]}
    if name == "listik_memory":
        query = args.get("query")
        limit = _int_arg(args, "limit", 20)
        project = args.get("project")
        if query:
            return {"items": search_mod.search_memories(conn, query, limit=limit,
                                                        project=project)}
        return {"items": store.list_memories(conn, project=project, limit=limit)}
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
        return {"items": store.task_timeline(conn, limit=_int_arg(args, "limit", 100))}
    if name == "listik_deps":
        # Автор — см. `_mcp_actor`.
        actor = _mcp_actor(args.get("author"), args.get("actor"))
        if args.get("action") == "rm":
            return store.remove_dep(conn, args["id"], args["depends_on"],
                                    args.get("dep_type"))
        return store.add_dep(conn, args["id"], args["depends_on"],
                             args.get("dep_type", "blocks"), actor,
                             confirm=bool(args.get("confirm")))
    if name == "listik_deps_suggested":
        from . import deps as deps_mod
        return deps_mod.suggested_page(conn, project=args.get("project"),
                                       limit=_int_arg(args, "limit", 100))
    if name == "listik_cycles":
        from . import deps as deps_mod
        return {"cycles": deps_mod.cycles(conn)}
    if name == "listik_waves":
        from . import deps as deps_mod
        if args.get("apply"):
            return deps_mod.apply_resource_blocks(
                conn, project=args.get("project") or "", stage=args.get("stage"))
        return deps_mod.waves(conn, project=args.get("project") or "", stage=args.get("stage"))
    if name == "listik_mentions":
        return _mentions(conn, args)
    if name == "listik_portions":
        action = args.get("action")
        actor = args.get("actor") or owner
        if action == "sync":
            return store.sync_portions(conn, args["id"], actor=actor, harness=args.get("harness"))
        if action == "adopt":
            from . import stage_launch
            return stage_launch.adopt_portions(conn, args["id"], actor=actor,
                                               harness=args.get("harness"))
        raise errors_mod.BadArgument(f"action: ожидается sync или adopt, а не {action!r}")
    if name == "listik_restart":
        from . import stage_launch
        return stage_launch.restart_task(
            conn, args["id"], stage=args.get("stage"), route=args.get("route"),
            note=args.get("note"), actor=args.get("actor") or owner,
            harness=args.get("harness"))
    if name in ("listik_launch", "listik_revoke", "listik_delete"):
        if notify is None:
            return _via_own_server(conn, name, args, owner, fence)
        return _server_side(conn, name, args, notify)
    raise ValueError(f"неизвестный инструмент: {name}")


def _fence_key(name: str, args: dict):
    """Ключ инструмента в `fence.OPS["mcp"]`: у части инструментов op зависит от аргументов."""
    if name == "listik_deps" and args.get("action") == "rm":
        return (name, "rm")
    if name == "listik_portions":
        return (name, args.get("action"))
    if name == "listik_mentions" and args.get("link"):
        return (name, "link")
    return name


def _mentions(conn, args: dict) -> dict:
    """`listik_mentions`: чтение упоминаний, с `link` — как `listik dep link --json`."""
    from . import deps as deps_mod
    tid = args["id"]
    items = deps_mod.mentioned(conn, tid, limit=_int_arg(args, "limit", 50) or 50)
    if not args.get("link"):
        return {"items": items}
    actor = _mcp_actor(args.get("actor"))
    dep_type = args.get("dep_type") or "relates-to"
    linked, skipped = [], []
    for item in items:
        if args.get("only") and args["only"] != item["id"]:
            continue
        try:
            linked.append(store.add_dep(conn, tid, item["id"], dep_type, actor, confirm=False))
        except (errors_mod.ListikError, errors_mod.NotFound, ValueError) as exc:
            err = errors_mod.as_error(exc)
            # Как `dep link`: кандидата нет или связь с ним невозможна — пропускаем
            # его одного; прочие ошибки касаются всего вызова.
            if err.code not in (errors_mod.NOT_FOUND, errors_mod.CONFLICT):
                raise
            skipped.append({"id": item["id"], "code": err.code, "message": err.message})
    return {"candidates": items, "linked": linked, "made": len(linked), "skipped": skipped}


def _server_side(conn, name: str, args: dict, notify) -> object:
    """`launch`/`revoke`/`delete` в процессе сервера — как HTTP-ручки, с его `notify`."""
    from . import launcher as launcher_mod
    tid = args["id"]
    if name == "listik_delete":
        store.delete_task(conn, tid)
        notify("task", {"id": tid, "action": "deleted"})
        return {"deleted": tid}
    if name == "listik_revoke":
        return launcher_mod.revoke(conn, tid, actor=args.get("actor"),
                                   harness=args.get("harness"), note=args.get("note"),
                                   kill=bool(args.get("kill", True)), notify=notify)
    result = launcher_mod.start(conn, tid, notify=notify, env=args.get("env"))
    if isinstance(result, dict):
        out = store.get_task(conn, tid)
        out.update(result)
        return out
    if result is not None:
        code = (errors_mod.ALREADY_LAUNCHED if result == launcher_mod.ALREADY_STARTED
                else errors_mod.CONFLICT)
        raise errors_mod.ListikError(result, code=code, status=409)
    out = store.get_task(conn, tid)
    out["launched"] = True
    return out


def _own_server(cfg: dict) -> tuple[str, int]:
    """Адрес своего сервера по `[server]`: «слушать везде» — не адрес, по которому ходят."""
    host = str(cfg["server"]["host"] or "127.0.0.1")
    if host in ("0.0.0.0", "::", "*"):
        host = "127.0.0.1"
    return host, int(cfg["server"]["port"])


def _via_own_server(conn, name: str, args: dict, owner, fence) -> object:
    """stdio: `launch`/`revoke`/`delete` — запросом к своему серверу (процесс задачи
    держит он). Сервера нет: `launch`/`revoke` — отказ, `delete` — прямо в базе."""
    from . import client as client_mod
    tid = args["id"]
    host, port = _own_server(config_mod.load())
    op = {"listik_launch": "launch", "listik_revoke": "revoke"}.get(name)
    if client_mod.is_up(host, port):
        if op == "launch":
            method, path, body = "POST", f"/api/tasks/{tid}/launch", {"env": args.get("env")}
        elif op == "revoke":
            method, path = "POST", f"/api/tasks/{tid}/revoke"
            body = {k: args[k] for k in ("note", "kill", "actor", "harness") if k in args}
        else:
            method, path, body = "DELETE", f"/api/tasks/{tid}", None
        try:
            return client_mod.request(method, path, body=body, host=host, port=port,
                                      owner=owner, fence=fence)
        except client_mod.ApiDown:
            pass  # сервер ушёл между проверкой и запросом — как «сервера нет»
    if op is not None:
        raise client_mod.server_only_error(op)
    store.delete_task(conn, tid)
    return {"deleted": tid}


def _norm_actor(value: str | None) -> str | None:
    if not value:
        return None
    from . import actors as actors_mod
    key, _ = actors_mod.resolve(value, None)
    return key


def _mcp_actor(*candidates: str | None) -> str:
    """Автор записи по MCP: первый непустой из `candidates`, затем `LISTIK_ACTOR`,
    затем `agent:mcp` — в каноническом ключе (`claude` → `agent:claude`).

    MCP — транспорт агентов: вызов без автора не должен стать «человеческим»
    (иначе агентский verdict проходит мимо s4-judge, связь — мимо suggested-blocks).
    Владелец (`X-Listik-Owner`/`LISTIK_OWNER`) — это «на кого работает агент»
    (видимость задач, проверки claim), а не автор записи, поэтому кандидатом его
    передаёт только `listik_needs_owner`: вопрос к человеку подписывается тем, от
    чьего имени идёт работа. `listik_comment`/`listik_deps` его не передают.
    """
    for value in (*candidates, os.environ.get("LISTIK_ACTOR"), "agent:mcp"):
        if value and value.strip():
            return _norm_actor(value)


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


def handle(request: dict, conn=None, owner=FROM_ENV, fence=FROM_ENV,
           notify=None) -> dict | None:
    """`owner`/`fence`/`notify` — см. `call_tool`: HTTP всегда передаёт значение
    заголовков (в том числе `None`, если их нет) и `publish`, stdio вызывает без них."""
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
            payload = call_tool(name, args, conn, owner, fence, notify)
        except Exception as exc:  # noqa: BLE001
            # Соединение живёт дольше вызова (stdio — процесс, HTTP — поток): частичная
            # запись упавшего инструмента ушла бы в базу со следующим (listik-mqr6).
            if conn is not None:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass  # откат не удался — ответ всё равно про исходную ошибку
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


def _waves_ids(result: dict) -> list[str]:
    """Уникальные id задач из пар `added`/`removed` ответа `listik_waves` apply."""
    try:
        payload = errors_mod.json_loads(result["content"][0]["text"])
        pairs = payload["added"] + payload["removed"]
        if not all(isinstance(pair, list) for pair in pairs):
            return []
        ids = {tid for pair in pairs for tid in pair}
    except Exception:  # noqa: BLE001 — ответ не той формы: событий не будет
        return []
    return sorted(tid for tid in ids if isinstance(tid, str) and tid)


def notify_event(request: dict, response: dict | None) -> list[tuple[str, str]]:
    """События доске по вызову инструмента — список `(task_id, action)`, пустой — событий нет.

    Правила те же, что у HTTP-транспорта (`server.Handler._mcp`): пишущий
    инструмент из `WRITE_TOOLS`, успешный ответ и известный id задачи — одно
    событие с `action` = имя инструмента. `listik_waves` с `apply` — по событию
    `deps` на каждый id из `added`/`removed` (как `POST /api/waves/apply`), без
    `apply` это чтение. Чтения и ответы `isError` доску не будят.
    """
    if not isinstance(request, dict) or request.get("method") != "tools/call":
        return []
    _method, _rid, params = request_parts(request)
    name = params.get("name")
    if name not in WRITE_TOOLS:
        return []
    result = (response or {}).get("result") or {}
    if result.get("isError"):
        return []
    args = params.get("arguments") or {}
    if name == "listik_waves":
        return [(tid, "deps") for tid in _waves_ids(result)] if args.get("apply") else []
    if name == "listik_mentions" and not args.get("link"):
        return []  # без link — чтение
    task_id = _result_id(result) if name == "listik_create" else args.get("id")
    action = "deleted" if name == "listik_delete" else name  # доска ждёт именно "deleted"
    return [(task_id, action)] if isinstance(task_id, str) and task_id else []


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
        host, port = _own_server(cfg)
        body = errors_mod.json_dumps({"task_id": task_id, "action": action}).encode("utf-8")
        req = urllib.request.Request(
            f"http://{host}:{port}/api/notify",
            data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        token = config_mod.auth_token(cfg)
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
    for event in notify_event(request, response):
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


#: Сколько прокси ждёт ответа общего сервера на одно сообщение, секунд.
PROXY_TIMEOUT = 120

#: Инструменты, которым подставляется проект (аргумент → `LISTIK_PROJECT` → файл), как
#: `--project` у CLI. `listik_memory`/`listik_remember`/`listik_deps_suggested` — нет:
#: у CLI память и предложенные связи проект по каталогу тоже не берут.
PROJECT_TOOLS = frozenset({
    "listik_search", "listik_list", "listik_create", "listik_ready", "listik_blocked",
    "listik_board", "listik_stats", "listik_waves",
})

#: Инструменты, у которых прокси подставляет автора из `LISTIK_ACTOR` агента: иначе
#: `_mcp_actor` на сервере подписал бы запись окружением сервера.
ACTOR_TOOLS = frozenset({"listik_comment", "listik_needs_owner", "listik_deps",
                         "listik_mentions"})

ALL_PROJECTS = "all"


def _given(args: dict, key: str) -> bool:
    """Ключ передан агентом. `null` — не передан (как у `call_tool`), `""` — передан."""
    return args.get(key) is not None


def with_defaults(request: dict, project_file: dict | None, *, proxy: bool) -> dict:
    """Подстановки в `tools/call` с объектом `params.arguments`; остальное — как есть.

    Проект — аргумент → `LISTIK_PROJECT` → `project` из `.listik.toml`; `all` (кроме
    `listik_waves`, у волн «всех проектов» нет) снимает фильтр. Автор (только прокси) —
    `actor = LISTIK_ACTOR`, если агент не передал ни `author`, ни `actor`. Явно
    переданное не заменяется.
    """
    if request.get("method") != "tools/call":
        return request
    params = request.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("arguments"), dict):
        return request
    name = params.get("name")
    args = dict(params["arguments"])
    if name in PROJECT_TOOLS:
        if not _given(args, "project"):
            fallback = ((os.environ.get("LISTIK_PROJECT") or "").strip()
                        or (project_file or {}).get("project"))
            if fallback:
                args["project"] = fallback
        value = args.get("project")
        if (name != "listik_waves" and isinstance(value, str)
                and value.strip().casefold() == ALL_PROJECTS):
            del args["project"]
    if proxy and name in ACTOR_TOOLS and not _given(args, "author") \
            and not _given(args, "actor"):
        actor = (os.environ.get("LISTIK_ACTOR") or "").strip()
        if actor:
            args["actor"] = actor
    return {**request, "params": {**params, "arguments": args}}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """3xx не выполняется: `Authorization` не должен уйти на чужой адрес."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _server_error_text(raw: bytes) -> str:
    """`error` из тела отказа: `{"ok": false, "error": "…"}` или JSON-RPC `error.message`."""
    try:
        body = errors_mod.json_loads(raw)
    except (ValueError, UnicodeDecodeError):
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        error = error.get("message")
    return str(error).strip() if error else ""


def proxy_message(target, request: dict, *, timeout: float = PROXY_TIMEOUT) -> dict | None:
    """Одно сообщение — `POST <target.base_url>/mcp`; ответ для stdout или `None`.

    `None` — писать нечего: 202 (уведомление) или любая неудача уведомления. Ошибки
    запроса с `id` — JSON-RPC `error` `-32603`; токен в их текст не попадает.
    """
    rid = request.get("id")
    notification = "id" not in request
    url = target.base_url

    def fail(message: str) -> dict | None:
        return None if notification else rpc_error(rid, -32603, message)

    req = urllib.request.Request(f"{url}/mcp", method="POST",
                                 data=errors_mod.json_dumps(request).encode("utf-8"))
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {target.token}")
    who = client_mod.owner()
    if who:
        req.add_header("X-Listik-Owner", who)
    token = fence_mod.from_env()
    if token is not None:
        for key, value in fence_mod.to_headers(token).items():
            req.add_header(key, value)
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read()
        except (OSError, http.client.HTTPException):
            raw = b""
    except urllib.error.URLError:
        # Запрос не ушёл (refused, DNS, таймаут соединения): записи точно не было.
        return fail(f"общий сервер {url} не отвечает — проверь сеть и listik status; "
                    "локальная база не тронута")
    except (OSError, http.client.HTTPException):
        # Запрос ушёл, ответа нет: запись могла примениться, повтор дал бы дубль.
        return fail(f"общий сервер {url} не ответил вовремя — результат неизвестен, "
                    "проверь карточку, прежде чем повторять")
    if status == 202:
        return None
    if status == 200:
        if not raw.strip():
            return None
        try:
            return errors_mod.json_loads(raw)
        except (ValueError, UnicodeDecodeError):
            return fail(f"общий сервер {url} ответил не-JSON — проверь адрес в .listik.toml")
    detail = _server_error_text(raw)
    message = f"общий сервер {url} ответил {status}" + (f": {detail}" if detail else "")
    if 300 <= status < 400:
        message += " — сервер перенаправляет: проверь адрес в .listik.toml"
    elif status == 401:
        message += f" — проверь токен: listik remote set {url}"
    return fail(message)


def _write(response: dict) -> None:
    sys.stdout.write(errors_mod.json_dumps(response) + "\n")
    sys.stdout.flush()


def _run_remote(target, failure: str | None) -> int:
    """Прокси на общий сервер (`failure is None`) или отказ ошибкой конфигурации.

    Локальная база не открывается ни в одном из двух режимов.
    """
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = errors_mod.json_loads(line)
        except json.JSONDecodeError as exc:
            _write(rpc_error(None, -32700, f"невалидный JSON: {exc}"))
            continue
        if not isinstance(request, dict):
            _write(rpc_error(None, -32600, "неверный запрос JSON-RPC"))
            continue
        if failure is not None:
            response = None if "id" not in request else rpc_error(request.get("id"), -32603,
                                                                  failure)
        else:
            response = proxy_message(target, with_defaults(request, target.project_file,
                                                           proxy=True))
        if response is not None:
            _write(response)
    return 0


def _resolve() -> tuple[object | None, str | None]:
    """Цель stdio по каталогу агента: `(Target, None)` или `(None, текст ошибки)`.

    cwd удалён — `(None, None)`: прежнее поведение, локальная база.
    """
    try:
        cwd = os.getcwd()
    except OSError:
        return None, None
    try:
        return client_mod.resolve_target(cwd), None
    except (errors_mod.ListikError, OSError, ValueError) as exc:
        return None, errors_mod.mcp_error_text(exc)


def run() -> int:
    target, failure = _resolve()
    if failure is not None or (target is not None and target.kind == "remote"):
        return _run_remote(target, failure)
    project_file = target.project_file if target is not None else None
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
        except json.JSONDecodeError as exc:
            sys.stdout.write(errors_mod.json_dumps(
                rpc_error(None, -32700, f"невалидный JSON: {exc}")) + "\n")
            sys.stdout.flush()
            continue
        if project_file is not None and isinstance(request, dict):
            request = with_defaults(request, project_file, proxy=False)
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

# Приёмка порции 01.d. Контракт `context` по этапам

Тесты — `python3 -m unittest discover tests -v`. Сценарии CLI — временная база, `--local`,
задача с `--spec` = копия `tests/fixtures/long-spec.md` (>= 25 000 символов) и `--checklist`.

## Форма ответа

1. Тест: для s1, s2, s3, s4 набор верхнеуровневых ключей одинаков и равен
   `task, card, stage, portion, documents, chunks, acceptance, dependencies, reviews, verdict,
   journal, worktree, limits, reasons, generated_at`.
2. `documents[]` не содержит поля `chunks`, содержит `status`, `revision`, `chunk_count`, `path`,
   `kind`; у каждого элемента `chunks[]` есть `id, document_id, kind, path, ordinal, heading,
   breadcrumb, text, start_line, end_line, reason`.
3. `reasons[]` содержит записи с `block` ∈ `card, acceptance, dependencies` на всех этапах и по
   одной `chunk`-записи на каждый элемент `chunks[]` (тест).
4. Задача без документов: все четыре этапа отвечают без исключений, `documents == []`,
   `chunks == []` (тест; сценарий CLI — `new` без `--spec`, затем `context`).

## Лимиты

5. s1 без `--max-chars` на файле 29 000 символов: все чанки на месте, `limits.truncated` ложь,
   `limits.default_for_stage == 150000`. До правки этот же вызов давал `truncated: true` и
   `used_chars: 24000` — тест красный до правки.
6. s3 без `--max-chars`: `limits.default_for_stage == 24000`. `--max-chars 5000` на s1:
   `truncated` истина, `dropped_chunks > 0`, `limits.reason` содержит путь spec и строку
   обреза (`truncated_chunks[0].start_line`).

## s1 / s2

7. s1: `reviews == []`, `verdict is None`, `journal == []`, `worktree is None`; чанков —
   сумма `chunk_count` по документам; reason каждого чанка начинается с «полный документ».
7а. `task` без деталей (тест; до правки `get_task` шёл с `with_details=True` — красный): на
   всех этапах в `task` нет ключей `comments`, `events`, `documents`, `dependencies`,
   `dependents`, `deps_state`; на s1 текст verdict-комментария и тексты review не встречаются
   нигде в `json.dumps(ответ, ensure_ascii=False)`; на s3 текст первого review не встречается
   нигде в сериализованном ответе (только последний).
8. s2: `reviews` — все review-комментарии (тест: два).

## s3 — слои (тесты; до правки все чанки шли с reason «релевантный чанк документа»)

9. Без порции: все чанки `checklist` с reason «чек-лист целиком»; чанки `spec` только с reason
   «лексическое совпадение…», среди них раздел со словами acceptance; общее число чанков
   меньше, чем на s1.
10. С существующей порцией: чанки `spec` только с heading/breadcrumb, содержащими порцию
    (регистр не важен), reason «совпадение с порцией».
11. С несуществующей порцией: чанки `spec` с reason «лексическое совпадение…».
12. Пустой acceptance и заголовок без совпадений: чанки `spec` с reason «начало документа…».
13. `reviews` — ровно один (последний по `created_at`); `dependencies.hard` содержит
    подтверждённую связь, `dependencies.suggested == []`, `suggested-blocks` в `hard` нет.

## s4

14. `verdict` — последний verdict-комментарий; у каждого элемента `journal` есть ключи
    `ts, kind, actor, text, id, from, to`; среди них есть `kind == "journal"` (с `id`
    комментария) и `kind == "stage"` (с `from`/`to` и `id`, начинающимся с `event:`); список
    равен `sorted(journal, key=lambda j: (j["ts"], j["kind"], j["id"]))` (тест).
15. `worktree` для временного git-репозитория **с одним коммитом** (подготовка из ТЗ:
    `git init`, коммит файла `a.txt` с `-c user.email/-c user.name`, затем изменение `a.txt`):
    `exists: true`, `git: true`, `head` — 40 hex-символов, `branch` непустой, `changed_files`
    содержит строку с `a.txt`, `diff_stat` — непустая строка с `a.txt` (тест).
15а. Репозиторий без коммитов (`git init` + незакоммиченный файл): `exists: true`, `git: true`,
    `head is None`, `diff_stat == ""`, `changed_files` содержит имя файла,
    `reason == "нет коммитов"`, исключений нет (тест).
16. `worktree` без `tasks.worktree`: `{"exists": false, "reason": …}`; с несуществующим
    каталогом: `exists: false`; в каталоге без git: `exists: true, git: false`. Исключений нет.
17. CLI: `set $TID worktree=<git-каталог>` → `context --stage s4-judge --format json` печатает
    `worktree.exists == true`.

## Стабильность и текст

18. Два подряд вызова `context` для каждого этапа дают побайтно одинаковый JSON
    (`cmp` в CLI; `json.dumps(sort_keys=True)` в тесте).
18а. Стабильность по построению (тест; до правки `dependencies = deps_state` с возрастами —
    красный): на задаче с незакрытым жёстким блокером, у которого проставлен держатель
    (`store.claim`), рекурсивный обход ответа не находит ключей, оканчивающихся на `_age`/
    `_hours`, и ключей `stale`, `stale_holder`, `abandoned`; в `dependencies` нет ключа
    `reasons`; при этом `dependencies.blocked_by` непуст (проверка не на пустом объекте) и
    `dependencies.verdict` — строка.
18б. Пункт 18 сам по себе не доказывает стабильность во времени (два вызова подряд не дадут
    возрасту измениться) — засчитывается только вместе с 18а и 7а.
19. `--format text` на s3 печатает карточку, acceptance, зависимости, документы со статусом,
    чанки с заголовком вида `### <breadcrumb> (<path>:<start>-<end>) — <reason>`; на s4 —
    блок worktree; при обрезке — строку лимитов.
20. `listik context $TID --stage s1-spec --json` и `--format json` дают одинаковый вывод.

## Границы

21. `git diff --stat HEAD` не содержит `listik/search.py`, `listik/embed.py`, `listik/db.py`,
    `alembic/`, `listik/mcp.py`, `API.md`, `README.md`; в `documents.py` не изменены
    `split_markdown`, `index_document`, `refresh_all`.
22. В `documents.context` нет вызовов `embed.*`/Ollama и `datetime.now`/`time.time`
    (чтение диффа); git вызывается только для пути `tasks.worktree`.
23. Тесты порций a–c зелёные.

"""Единый формат ошибок Listik — общий для CLI, сервера и локального режима.

У ошибки всегда три поля: `code` (машинный код из словаря ниже), `message`
(что случилось, по-русски) и `hint` (как исправить). CLI печатает их человеку
строкой «ошибка: <message> — <hint>», а с `--json` — объектом
`{"error": {"code", "message", "hint"}}` в stdout. Сервер кладёт тот же `code`
в тело ответа (`{"ok": false, "error": ..., "code": ...}`), поэтому агент
видит одну и ту же причину и через HTTP, и когда сервер не поднят и CLI
работает с базой напрямую.

Коды фиксированные — по ним агент ветвится, поэтому произвольных строк здесь
не заводим:

    bad_argument        команда, аргументы или тело запроса не годятся
    not_found           нет задачи, проекта, документа
    conflict            состояние не даёт выполнить (занято, заблокировано, закрыто)
    unauthorized        токен не принят
    forbidden           токена нет или прав не хватает
    method_not_allowed  метод не поддерживается этим путём
    rate_limited        слишком часто
    server_error        сервер упал (HTTP 5xx)
    http_error          ответ сервера, который не отнесли к известным
    unsupported         локальный режим не умеет эту операцию
    internal            непойманное исключение: трейсбек только в listik.log
"""
from __future__ import annotations

BAD_ARGUMENT = "bad_argument"
NOT_FOUND = "not_found"
CONFLICT = "conflict"
UNAUTHORIZED = "unauthorized"
FORBIDDEN = "forbidden"
METHOD_NOT_ALLOWED = "method_not_allowed"
RATE_LIMITED = "rate_limited"
SERVER_ERROR = "server_error"
HTTP_ERROR = "http_error"
UNSUPPORTED = "unsupported"
INTERNAL = "internal"

#: HTTP-статус → код. Нужен, когда сервер ответил без поля `code` (старая версия,
#: прокси, ошибка вне обработчика) — код всё равно должен быть машинным.
CODE_BY_STATUS = {
    400: BAD_ARGUMENT,
    401: UNAUTHORIZED,
    403: FORBIDDEN,
    404: NOT_FOUND,
    405: METHOD_NOT_ALLOWED,
    409: CONFLICT,
    429: RATE_LIMITED,
}

#: HTTP-статус → «как исправить». Подсказка общая: конкретику обычно несёт message.
#: У 400 подсказки нет намеренно: сервер в тексте уже пишет, что именно не так и
#: какие есть варианты, а общее «проверь аргументы» только сбивало бы с толку.
HINT_BY_STATUS = {
    401: "проверь токен: listik token (и config.toml, [auth].token)",
    403: "проверь токен: listik token (и config.toml, [auth].token)",
    404: "проверь идентификатор: listik list (проекты: listik projects)",
    405: "этот метод у эндпоинта не поддерживается",
    409: "посмотри состояние карточки: listik show <id>",
    429: "повтори позже",
    500: "подробности в listik.log на сервере; состояние: listik status",
    503: "подробности в listik.log на сервере; состояние: listik status",
}


def code_for_status(status: int) -> str:
    """Код по HTTP-статусу: 404 → not_found, 409 → conflict, 5xx → server_error."""
    if status in CODE_BY_STATUS:
        return CODE_BY_STATUS[status]
    return SERVER_ERROR if status >= 500 else HTTP_ERROR


def hint_for_status(status: int) -> str:
    return HINT_BY_STATUS.get(status, "")


class ListikError(Exception):
    """Ошибка с кодом для машины, текстом для человека и подсказкой «как исправить»."""

    def __init__(self, message: str, *, code: str = INTERNAL, hint: str = "",
                 exit_code: int = 1, status: int | None = None):
        super().__init__(message)
        self.message = str(message)
        self.code = code
        self.hint = hint
        self.exit_code = exit_code
        self.status = status


def message_of(exc: BaseException) -> str:
    """Текст исключения без кавычек: `str(KeyError('нет'))` даёт `"'нет'"`."""
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    text = str(exc)
    return text or type(exc).__name__


def code_of(exc: BaseException) -> str:
    """Код по типу исключения.

    В store/documents `KeyError` значит «не найдено», `ValueError` — «нельзя
    выполнить в текущем состоянии» (занято, заблокировано, закрыто). Сервер шлёт
    этот же код в теле ответа (`server.api_error`), а CLI берёт его оттуда, —
    поэтому HTTP и локальный режим не расходятся.
    """
    if isinstance(exc, ListikError):
        return exc.code
    if isinstance(exc, KeyError):
        return NOT_FOUND
    if isinstance(exc, ValueError):
        return CONFLICT
    return INTERNAL


def _split(message: str) -> tuple[str, str]:
    """Первая строка сообщения и остаток: у store длинные «Варианты: …» — это подсказка."""
    lines = [line.strip() for line in (message or "").splitlines() if line.strip()]
    if not lines:
        return "неизвестная ошибка", ""
    return lines[0], " ".join(lines[1:])


def hint_of(exc: BaseException) -> str:
    if isinstance(exc, ListikError) and exc.hint:
        return exc.hint
    _, rest = _split(message_of(exc))
    if rest:
        return rest
    if isinstance(exc, KeyError):
        return "проверь идентификатор: listik list (проекты: listik projects)"
    return ""


def as_error(exc: BaseException) -> ListikError:
    """Приводит любое исключение к общему виду — дальше его печатает CLI."""
    if isinstance(exc, ListikError):
        return exc
    code = code_of(exc)
    message = message_of(exc)
    if code == INTERNAL:
        message = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    return ListikError(message, code=code, hint=hint_of(exc))


def payload(err: ListikError) -> dict:
    """Тело JSON-ошибки для stdout: одинаковое у argparse, API и локального режима."""
    return {"error": {"code": err.code, "message": err.message, "hint": err.hint}}


def text_lines(err: ListikError) -> list[str]:
    """Одна-две строки для человека: «ошибка: <что> — <как исправить>»."""
    first, rest = _split(err.message)
    hint = err.hint or rest
    line = f"ошибка: {first}"
    if hint:
        line += f" — {hint}"
    if hint and len(line) > 200:
        # Длинную подсказку переносим на вторую строку: так читается лучше, а
        # «одна-две строки» остаётся.
        return [f"ошибка: {first}", f"  подсказка: {hint}"]
    return [line]

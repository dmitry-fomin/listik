#!/bin/sh
# install.sh — установка и обновление Listik одной строкой.
#
#   curl -fsSL https://github.com/dmitry-fomin/listik/releases/latest/download/install.sh | sh
#
# Скрипт рассчитан и на `curl … | sh`: stdin — это он сам, поэтому ни данных, ни
# ответов на вопросы из stdin он не читает. Единственный источник ответа — /dev/tty.
#
# Раскладка установки:
#   <home>/app/<версия>/   код этой версии (старые версии не удаляются)
#   <home>/app/current     ссылка на текущую версию (относительная)
#   <bin-dir>/listik       обёртка: exec python3 <home>/app/current/bin/listik "$@"
# Данные (<home>/listik.db, config.toml, listik.log, logs/) установщик не создаёт,
# не перезаписывает и не удаляет; что нужно, создаст сам Listik (`listik init`).
set -eu

prog=install.sh

DEFAULT_RELEASES_API=https://api.github.com/repos/dmitry-fomin/listik/releases/latest
DEFAULT_DOWNLOAD_BASE=https://github.com/dmitry-fomin/listik/releases/download
DEFAULT_ROUTES_POLICY=ask

die() {
    printf '%s: ошибка: %s\n' "$prog" "$1" >&2
    exit 1
}

note() {
    printf '%s\n' "$1"
}

# ------------------------------------------------------------------ вид и ввод
#
# Весь интерактив — только при живом /dev/tty и stty; иначе скрипт печатает то же,
# что и раньше, и ни одной escape-последовательности в выводе не появляется.

esc=$(printf '\033')
cr=$(printf '\r')
etx=$(printf '\003')
ui_enabled=0
ui_anim=0
ui_cols=80
tty_saved=
c_crown=; c_leaf=; c_ok=; c_err=; c_text=; c_dim=; c_off=; c_sel=

ui_probe() {
    # 1 — интерактивный вид уместен, 0 — нет (причина не важна: везде один и тот же
    # запасной путь — прежние строчные вопросы и обычный вывод).
    [ "$assume_yes" = 1 ] && return 1
    [ -n "${NO_COLOR:-}" ] && return 1
    [ -n "${CI:-}" ] && return 1
    case ${LISTIK_PLAIN:-} in "" | 0) ;; *) return 1 ;; esac
    [ -t 1 ] || return 1
    command -v stty >/dev/null 2>&1 || return 1
    { : >/dev/tty; } 2>/dev/null || return 1
    tty_saved=$(stty -g </dev/tty 2>/dev/null) || return 1
    [ -n "$tty_saved" ] || return 1
    cols=$(stty size </dev/tty 2>/dev/null | awk '{print $2}')
    case $cols in
        *[!0-9]* | "" | 0) cols=${COLUMNS:-80} ;;
    esac
    case $cols in
        *[!0-9]* | "") cols=80 ;;
    esac
    # Ширину знаем — узкий терминал не мучаем; не знаем — считаем, что 80.
    [ "$cols" -ge 60 ] || return 1
    ui_cols=$cols
    return 0
}

ui_init() {
    if ui_probe; then
        ui_enabled=1
        c_crown=$esc'[38;5;65m'
        c_leaf=$esc'[38;5;179m'
        c_ok=$esc'[38;5;107m'
        c_err=$esc'[38;5;167m'
        c_text=$esc'[38;5;247m'
        c_dim=$esc'[38;5;240m'
        c_sel=$esc'[1m'
        c_off=$esc'[0m'
        # Дробная пауза есть и в GNU coreutils, и в BSD, но если её нет — просто
        # не анимируем: статичная заставка лучше шести одинаковых кадров подряд.
        if sleep 0.05 2>/dev/null; then
            ui_anim=1
        fi
    fi
}

ui_tty_raw() {
    stty raw -echo min 1 time 0 </dev/tty 2>/dev/null || return 1
}

ui_tty_restore() {
    [ -n "$tty_saved" ] || return 0
    stty "$tty_saved" </dev/tty 2>/dev/null || true
}

ui_cursor_hide() { [ "$ui_enabled" = 1 ] && printf '%s[?25l' "$esc" || true; }
ui_cursor_show() { [ "$ui_enabled" = 1 ] && printf '%s[?25h' "$esc" || true; }

ui_line() {
    # строка с очисткой до конца — иначе хвост прошлого кадра остаётся на экране
    printf '%s[2K%s\n' "$esc" "$1"
}

tree_h=10

ui_tree() {
    ui_line "${c_crown}              ,@@@@@@@,${c_off}"
    ui_line "${c_crown}      ,,,.   ,@@@@@@/@@,  .oo8888o.${c_off}"
    ui_line "${c_crown}   ,&%%&%&&%,@@@@@/@@@@@@,8888\\88/8o${c_off}"
    ui_line "${c_crown}  ,%&\\%&&%&&%,@@@\\@@@/@@@88\\88888/88'${c_off}"
    ui_line "${c_crown}  %&&%&%&/%&&%@@\\@@/ /@@@88888\\88888'${c_off}"
    ui_line "${c_crown}  %&&%/ %&%%&&@@\\ V /@@' \`88\\8 \`/88'${c_off}"
    ui_line "${c_crown}  \`&%\\ \` /%&'    |.|        \\ '|8'${c_off}"
    ui_line "${c_crown}      |o|        | |         | |${c_off}"
    ui_line "${c_crown}      |.|        | |         | |${c_off}"
    ui_line "${c_crown}   \\\\/ ._\\//_/__/  ,\\_//__\\\\/.  \\_//__${c_off}"
}

ui_leaf_at() {
    # $1 — на сколько строк подняться от конца блока, $2 — колонка
    printf '%s[%dA%s[%dC%s&%s%s[%dB%s' \
        "$esc" "$1" "$esc" "$2" "${c_leaf}" "${c_off}" "$esc" "$1" "$cr"
}

ui_splash() {
    if [ "$ui_enabled" != 1 ]; then
        # Нет интерактива — нет и заставки: вывод остаётся ровно таким, как раньше.
        return
    fi
    ui_cursor_hide
    if [ "$ui_anim" = 1 ]; then
        # кадр: строка снизу вверх и колонка листка
        for frame in '9 41' '8 44' '7 20' '6 26' '5 22' '4 28' '2 24'; do
            row=${frame% *}
            col=${frame#* }
            ui_tree
            ui_leaf_at "$row" "$col"
            sleep 0.12
            printf '%s[%dA' "$esc" "$tree_h"
        done
    fi
    ui_tree
    ui_leaf_at 1 40
    printf '\n'
    ui_line ""
    ui_line "  ${c_sel}Listik${c_off}  ${c_dim}—  задачи и память одним сервером${c_off}"
    ui_line "  ${c_dim}Установщик задаст несколько вопросов; выбор — стрелками.${c_off}"
    ui_line ""
    ui_cursor_show
}

ui_step() {
    # $1 — ok|fail|run, $2 — текст
    [ "$ui_enabled" = 1 ] || return 0
    case $1 in
        ok) printf '  %s✓%s %s\n' "${c_ok}" "${c_off}" "$2" ;;
        fail) printf '  %s✗%s %s\n' "${c_err}" "${c_off}" "$2" ;;
        *) printf '  %s·%s %s\n' "${c_dim}" "${c_off}" "$2" ;;
    esac
}

ui_read_key() {
    # одна клавиша из /dev/tty в $key: up, down, enter, space, esc, yes, no, abort, other
    key=other
    ch=$(dd bs=1 count=1 2>/dev/null </dev/tty; printf x)
    ch=${ch%x}
    case $ch in
        "$esc")
            stty min 0 time 1 </dev/tty 2>/dev/null || true
            rest=$(dd bs=1 count=2 2>/dev/null </dev/tty; printf x)
            rest=${rest%x}
            stty min 1 time 0 </dev/tty 2>/dev/null || true
            case $rest in
                '[A' | 'OA') key=up ;;
                '[B' | 'OB') key=down ;;
                '') key=other ;;
                *) key=other ;;
            esac
            ;;
        "$cr" | '
') key=enter ;;
        ' ') key=space ;;
        k | K) key=up ;;
        j | J) key=down ;;
        y | Y) key=yes ;;
        n | N) key=no ;;
        "$etx" | '') key=abort ;;
        *) key=other ;;
    esac
}

ui_menu_draw() {
    # $1 — номер выбранного пункта
    i=1
    for item in "$menu_1" "$menu_2"; do
        label=${item%%|*}
        hint=${item#*|}
        [ "$hint" = "$item" ] && hint=
        # Перенос строки пункта сбил бы перерисовку меню (курсор ходит по строкам),
        # поэтому на узком терминале пояснение не печатаем вовсе.
        [ "$ui_cols" -lt 80 ] && hint=
        if [ "$i" = "$1" ]; then
            ui_line "  ${c_ok}❯ ●${c_off} ${c_sel}$label${c_off}  ${c_dim}$hint${c_off}"
        else
            ui_line "    ${c_dim}○${c_off} ${c_text}$label${c_off}  ${c_dim}$hint${c_off}"
        fi
        i=$((i + 1))
    done
}

ui_menu2() {
    # $1 — вопрос, $2 — подсказка, $3 и $4 — пункты "текст|пояснение", $5 — выбранный
    # по умолчанию (1 или 2). Ответ — в $menu_choice. Возврат 1 — меню не показано.
    [ "$ui_enabled" = 1 ] || return 1
    ui_tty_raw || return 1
    menu_1=$3
    menu_2=$4
    menu_choice=$5
    ui_cursor_hide
    printf '%s[2K  %s?%s %s%s%s\n' "$esc" "${c_ok}" "${c_off}" "${c_sel}" "$1" "${c_off}"
    if [ -n "$2" ]; then
        printf '%s[2K    %s%s%s\n' "$esc" "${c_dim}" "$2" "${c_off}"
    fi
    ui_menu_draw "$menu_choice"
    printf '%s[2K  %s↑ ↓ выбор · Enter подтвердить · Ctrl+C отмена%s\n' "$esc" "${c_dim}" "${c_off}"
    while :; do
        ui_read_key
        case $key in
            up) menu_choice=1 ;;
            down) menu_choice=2 ;;
            yes) menu_choice=1; key=enter ;;
            no) menu_choice=2; key=enter ;;
            abort)
                ui_cursor_show
                ui_tty_restore
                printf '\n'
                die "установка прервана"
                ;;
        esac
        # перерисовываем два пункта и строку клавиш
        printf '%s[3A' "$esc"
        ui_menu_draw "$menu_choice"
        printf '%s[2K  %s↑ ↓ выбор · Enter подтвердить · Ctrl+C отмена%s\n' "$esc" "${c_dim}" "${c_off}"
        case $key in
            enter) break ;;
        esac
    done
    ui_tty_restore
    ui_cursor_show
    return 0
}

ui_outro() {
    # финальный экран: дерево с уже лежащим листком
    [ "$ui_enabled" = 1 ] || return 0
    printf '\n'
    ui_tree
    ui_leaf_at 1 40
    printf '\n\n'
    printf '  %s✓ Listik %s установлен%s\n' "${c_ok}" "$version" "${c_off}"
}

usage() {
    cat <<'USAGE'
Установка и обновление Listik.

Использование:
  curl -fsSL https://github.com/dmitry-fomin/listik/releases/latest/download/install.sh | sh
  sh install.sh [флаги]

Флаги (флаг важнее переменной окружения):
  --version X.Y.Z       какую версию ставить (LISTIK_VERSION)
  --archive <путь>      поставить из локального архива, без сети (LISTIK_ARCHIVE);
                        версия берётся из файла VERSION внутри архива
  --home <каталог>      каталог установки и данных (LISTIK_HOME), по умолчанию ~/.listik
  --bin-dir <каталог>   куда положить обёртку listik (LISTIK_BIN_DIR),
                        по умолчанию ~/.local/bin
  --routes keep|replace|ask
                        что делать с рабочей копией routes.json, если она отличается
                        от новой (LISTIK_ROUTES_POLICY), по умолчанию ask;
                        keep — не трогать, replace — заменить, сохранив .bak-<время>,
                        ask — спросить в /dev/tty
  --service yes|no      поставить и (пере)запустить автозапуск сервера
                        (launchd/systemd --user), по умолчанию yes
  --mcp yes|no          подключить MCP-сервер (claude mcp add), по умолчанию yes
  --plugins yes|no      поставить плагины Claude (marketplace + listik/feature-pipeline),
                        по умолчанию yes
  --codex-network yes|no|ask
                        если codex установлен, а в его config.toml нет
                        [sandbox_workspace_write] с network_access = true — дописать
                        (LISTIK_CODEX_NETWORK), по умолчанию ask; yes — дописать,
                        сохранив копию конфига рядом (.bak-<время>), no — только
                        предупредить, ask — спросить в /dev/tty
  --yes                 на вопросы без явного флага отвечать значением по умолчанию
                        (для routes это keep, для service/mcp/plugins — yes; вопрос
                        Codex он не закрывает — нужен --codex-network yes)
  --help                эта справка

Переменные окружения:
  LISTIK_HOME           каталог данных (по умолчанию ~/.listik); обёртка ставит его
                        по умолчанию, но заданное пользователем значение важнее
  LISTIK_VERSION, LISTIK_ARCHIVE, LISTIK_BIN_DIR, LISTIK_ROUTES_POLICY,
  LISTIK_CODEX_NETWORK — см. флаги
  CODEX_HOME            каталог настроек Codex (по умолчанию ~/.codex); в нём
                        установщик смотрит config.toml
  LISTIK_PLAIN          1 — без заставки, анимации, цвета и меню: только прежние
                        строчные вопросы (то же самое дают NO_COLOR, CI, --yes,
                        отсутствие /dev/tty и терминал уже 60 колонок)
  LISTIK_ROUTES         рабочая копия routes.json
                        (по умолчанию ~/.config/listik/routes.json)
  LISTIK_RELEASES_API   откуда брать последнюю версию, по умолчанию
                        https://api.github.com/repos/dmitry-fomin/listik/releases/latest
  LISTIK_DOWNLOAD_BASE  откуда качать архивы, по умолчанию
                        https://github.com/dmitry-fomin/listik/releases/download

Нужны: Darwin или Linux, python3 3.11+ и tar; для установки из сети — ещё curl или
wget; для проверки суммы — sha256sum или shasum.
USAGE
}

# ------------------------------------------------------------------ параметры

version=${LISTIK_VERSION:-}
archive=${LISTIK_ARCHIVE:-}
home=${LISTIK_HOME:-}
bin_dir=${LISTIK_BIN_DIR:-}
routes_policy=${LISTIK_ROUTES_POLICY:-}
service_answer=
mcp_answer=
plugins_answer=
codex_network=${LISTIK_CODEX_NETWORK:-}
assume_yes=0

while [ $# -gt 0 ]; do
    case $1 in
        --version)
            [ $# -ge 2 ] || die "--version требует версию вида X.Y.Z"
            version=$2
            shift
            ;;
        --version=*) version=${1#--version=} ;;
        --archive)
            [ $# -ge 2 ] || die "--archive требует путь к архиву"
            archive=$2
            shift
            ;;
        --archive=*) archive=${1#--archive=} ;;
        --home)
            [ $# -ge 2 ] || die "--home требует каталог"
            home=$2
            shift
            ;;
        --home=*) home=${1#--home=} ;;
        --bin-dir)
            [ $# -ge 2 ] || die "--bin-dir требует каталог"
            bin_dir=$2
            shift
            ;;
        --bin-dir=*) bin_dir=${1#--bin-dir=} ;;
        --routes)
            [ $# -ge 2 ] || die "--routes ждёт keep, replace или ask"
            routes_policy=$2
            shift
            ;;
        --routes=*) routes_policy=${1#--routes=} ;;
        --service)
            [ $# -ge 2 ] || die "--service ждёт yes или no"
            service_answer=$2
            shift
            ;;
        --service=*) service_answer=${1#--service=} ;;
        --mcp)
            [ $# -ge 2 ] || die "--mcp ждёт yes или no"
            mcp_answer=$2
            shift
            ;;
        --mcp=*) mcp_answer=${1#--mcp=} ;;
        --plugins)
            [ $# -ge 2 ] || die "--plugins ждёт yes или no"
            plugins_answer=$2
            shift
            ;;
        --plugins=*) plugins_answer=${1#--plugins=} ;;
        --codex-network)
            [ $# -ge 2 ] || die "--codex-network ждёт yes, no или ask"
            codex_network=$2
            shift
            ;;
        --codex-network=*) codex_network=${1#--codex-network=} ;;
        --yes|-y) assume_yes=1 ;;
        -h|--help)
            usage
            exit 0
            ;;
        *) die "неизвестный флаг: $1 (справка: --help)" ;;
    esac
    shift
done

[ -n "${HOME:-}" ] || die "HOME не задан — укажите каталог установки флагом --home"
[ -n "$home" ] || home=$HOME/.listik
[ -n "$bin_dir" ] || bin_dir=$HOME/.local/bin
[ -n "$routes_policy" ] || routes_policy=$DEFAULT_ROUTES_POLICY
case $routes_policy in
    keep|replace|ask) ;;
    *) die "--routes ждёт keep, replace или ask, а не '$routes_policy'" ;;
esac
case $service_answer in
    ""|yes|no) ;;
    *) die "--service ждёт yes или no, а не '$service_answer'" ;;
esac
case $mcp_answer in
    ""|yes|no) ;;
    *) die "--mcp ждёт yes или no, а не '$mcp_answer'" ;;
esac
case $plugins_answer in
    ""|yes|no) ;;
    *) die "--plugins ждёт yes или no, а не '$plugins_answer'" ;;
esac
case $codex_network in
    ""|yes|no|ask) ;;
    *) die "--codex-network ждёт yes, no или ask, а не '$codex_network'" ;;
esac
[ -n "$home" ] || die "--home не может быть пустым"

ui_init
# Заставка начинается раньше, чем появляется временный каталог, а курсор к этому
# моменту уже спрятан: до настоящего cleanup терминал возвращает этот трап.
trap 'ui_cursor_show; ui_tty_restore; exit 130' HUP INT TERM QUIT
trap 'ui_cursor_show; ui_tty_restore' EXIT
ui_splash

# `~` в значении флага оболочка раскрывает сама, а в кавычках и в переменной — нет.
resolve_dir() {
    # shellcheck disable=SC2088  # тильда в кавычках не раскрывается — это и нужно: шаблон
    case $1 in
        "~/"*) printf '%s/%s' "$HOME" "${1#\~/}" ;;
        "~") printf '%s' "$HOME" ;;
        /*) printf '%s' "$1" ;;
        *) printf '%s/%s' "$PWD" "$1" ;;
    esac
}
home=$(resolve_dir "$home")
bin_dir=$(resolve_dir "$bin_dir")

# ------------------------------------------------------- предусловия (до HOME)

os=$(uname -s) || die "не удалось определить систему (uname)"
case $os in
    Darwin|Linux) ;;
    *) die "поддерживаются только Darwin и Linux, а система — $os" ;;
esac

command -v tar >/dev/null 2>&1 || die "tar не найден в PATH — распаковать архив нечем"

command -v python3 >/dev/null 2>&1 || die "python3 не найден в PATH — Listik требует Python 3.11+"
python3_bin=$(resolve_dir "$(command -v python3)")
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' ||
    die "python3 ($python3_bin) старее 3.11 — Listik требует Python 3.11+"

checksum_file=
fetcher=
local_archive=0
if [ -n "$archive" ]; then
    local_archive=1
    [ -f "$archive" ] || die "нет файла архива: $archive"
    # Сумму проверяем, только если она лежит рядом с архивом.
    if [ -f "$archive.sha256" ]; then
        checksum_file=$archive.sha256
    fi
else
    if command -v curl >/dev/null 2>&1; then
        fetcher=curl
    elif command -v wget >/dev/null 2>&1; then
        fetcher=wget
    else
        die "нет ни curl, ни wget — скачать релиз нечем (или укажите --archive)"
    fi
    # При установке из сети сумма обязательна.
    checksum_file=network
fi

sha_tool=
if [ -n "$checksum_file" ]; then
    if command -v sha256sum >/dev/null 2>&1; then
        sha_tool=sha256sum
    elif command -v shasum >/dev/null 2>&1; then
        sha_tool=shasum
    else
        die "нет ни sha256sum, ни shasum — проверить сумму нечем"
    fi
fi

# ------------------------------------------------------------ временный каталог

tmp=$(mktemp -d "${TMPDIR:-/tmp}/listik-install.XXXXXX") ||
    die "не удалось создать временный каталог"
staging=
cleanup() {
    ui_tty_restore
    ui_cursor_show
    if [ -n "$staging" ] && [ -d "$staging" ]; then
        rm -rf "$staging"
    fi
    rm -rf "$tmp"
}
trap cleanup EXIT HUP INT TERM QUIT

download() {
    if [ "$fetcher" = curl ]; then
        curl -fsSL "$1" -o "$2"
    else
        wget -q -O "$2" "$1"
    fi
}

# --------------------------------------------------------- шаг 1: сам архив

if [ -n "$fetcher" ]; then
    releases_api=${LISTIK_RELEASES_API:-$DEFAULT_RELEASES_API}
    download_base=${LISTIK_DOWNLOAD_BASE:-$DEFAULT_DOWNLOAD_BASE}
    if [ -z "$version" ]; then
        download "$releases_api" "$tmp/releases.json" ||
            die "не удалось получить последнюю версию: $releases_api"
        version=$(sed -n \
            's/.*"tag_name": *"v\([^"]*\)".*/\1/p' \
            "$tmp/releases.json" | sed -n '1p')
        [ -n "$version" ] ||
            die "в ответе $releases_api нет \"tag_name\": \"v<версия>\" — укажите --version"
    fi
    printf '%s\n' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' ||
        die "версия должна быть вида X.Y.Z, а не '$version'"
    archive=$tmp/listik-$version.tar.gz
    url=$download_base/v$version/listik-$version.tar.gz
    download "$url" "$archive" || die "не удалось скачать архив: $url"
    download "$url.sha256" "$archive.sha256" ||
        die "не удалось скачать сумму: $url.sha256 — релиз без .sha256 не ставим"
    checksum_file=$archive.sha256
fi

# Верхний каталог архива — listik-<версия>, он же нужен, чтобы достать VERSION.
tar -tzf "$archive" > "$tmp/members" 2>/dev/null || die "не удалось прочитать архив: $archive"
tops=$(sed -e 's|/.*||' -e '/^$/d' "$tmp/members" | sort -u)
top=$(printf '%s\n' "$tops" | sed -n '1p')
[ -n "$top" ] || die "архив пуст: $archive"
[ "$(printf '%s\n' "$tops" | wc -l | tr -d ' ')" = 1 ] ||
    die "в архиве должен быть один верхний каталог listik-<версия>: $archive"

if [ "$local_archive" = 1 ]; then
    # Локальный архив: версия — из VERSION внутри него, а не из имени файла.
    tar -xzOf "$archive" "$top/VERSION" > "$tmp/VERSION" 2>/dev/null ||
        die "в архиве нет $top/VERSION — версию взять неоткуда"
    version=$(tr -d '[:space:]' < "$tmp/VERSION")
    printf '%s\n' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' ||
        die "VERSION в архиве должен быть вида X.Y.Z, а в нём: '$version'"
fi

# ------------------------------------------------------ шаг 2: проверка суммы

if [ -n "$checksum_file" ]; then
    if [ "$sha_tool" = sha256sum ]; then
        actual=$(sha256sum "$archive" | awk '{print $1}')
    else
        actual=$(shasum -a 256 "$archive" | awk '{print $1}')
    fi
    expected=$(awk 'NR == 1 {print $1}' "$checksum_file")
    [ -n "$expected" ] || die "файл суммы пуст: $checksum_file"
    actual=$(printf '%s' "$actual" | tr 'A-F' 'a-f')
    expected=$(printf '%s' "$expected" | tr 'A-F' 'a-f')
    [ "$actual" = "$expected" ] ||
        die "сумма не совпала: ожидалась $expected, получена $actual — установка отменена"
    ui_step ok "сумма архива совпала"
fi

# ------------------------------------- шаг 3: распаковка во временный каталог

app_dir=$home/app
staging=$app_dir/.new-$version-$$
mkdir -p "$staging" || die "не удалось создать каталог распаковки: $staging"
tar -xzf "$archive" -C "$staging" --strip-components=1 ||
    die "не удалось распаковать архив: $archive"
[ -f "$staging/bin/listik" ] || die "в архиве нет bin/listik"
[ -f "$staging/VERSION" ] || die "в архиве нет VERSION"
ui_step ok "архив распакован: listik $version"

# ------------------------------------------------------- шаг 4: поставить на место

code_dir=$app_dir/$version
if [ -e "$code_dir" ]; then
    old=$app_dir/.old-$version-$$
    rm -rf "$old"
    mv "$code_dir" "$old" || die "не удалось отодвинуть прежнюю версию: $code_dir"
    if ! mv "$staging" "$code_dir"; then
        mv "$old" "$code_dir" 2>/dev/null || true
        die "не удалось поставить версию $version на место: $code_dir"
    fi
    rm -rf "$old"
else
    mv "$staging" "$code_dir" || die "не удалось поставить версию $version на место: $code_dir"
fi
staging=

# Куда указывал current до переключения: понадобится для сверки протокола (шаг 9).
prev_code=
if [ -e "$app_dir/current" ]; then
    prev_code=$(cd "$app_dir/current" 2>/dev/null && pwd -P) || prev_code=
fi
if [ -n "$prev_code" ] && [ -f "$prev_code/docs/harness-protocol.md" ]; then
    cp "$prev_code/docs/harness-protocol.md" "$tmp/prev-protocol.md" || true
fi

# ---------------------------------------------------------- шаг 5: обёртка

mkdir -p "$bin_dir" || die "не удалось создать каталог обёртки: $bin_dir"
wrapper=$bin_dir/listik
cat > "$wrapper" <<WRAPPER
#!/bin/sh
# Обёртка Listik: каталог данных и код текущей версии. Перезаписывается установщиком.
: "\${LISTIK_HOME:=$home}"
export LISTIK_HOME
export LISTIK_WRAPPER="$wrapper"
exec "$python3_bin" "$home/app/current/bin/listik" "\$@"
WRAPPER
chmod +x "$wrapper" || die "не удалось сделать обёртку исполняемой: $wrapper"
ui_step ok "обёртка: $wrapper"

case ":${PATH:-}:" in
    *":$bin_dir:"*) ;;
    *)
        note "$prog: каталога $bin_dir нет в PATH — добавьте в профиль:"
        note "  export PATH=\"$bin_dir:\$PATH\""
        ;;
esac

# ------------------------------------------------- шаг 6: переключить current

ln -sfn "$version" "$app_dir/current" || die "не удалось переключить current на $version"
ui_step ok "current → $version"

# ------------------------------------------------------------ шаг 7: listik init

ui_step run "listik init: схема базы"
if ! "$wrapper" init; then
    ui_step fail "listik init"
    note "$prog: код установлен: $code_dir, current переключён на $version, но listik init упал" >&2
    note "$prog: исправьте причину и выполните listik init" >&2
    exit 1
fi

# ------------------------------------ шаг 7.1: автозапуск, MCP и плагины Claude

ask_yes_default_yes() {
    # $1 — значение флага (может быть пустым), $2 — вопрос, $3 — подсказка под ним.
    # Результат — $decision (yes/no), по умолчанию yes: без флага, без --yes и при
    # открытом /dev/tty спрашиваем, иначе отвечаем по умолчанию.
    if [ -n "$1" ]; then
        decision=$1
        return
    fi
    if [ "$assume_yes" = 1 ]; then
        decision=yes
        return
    fi
    if ui_menu2 "$2" "$3" "Да|" "Нет|пропустить этот шаг" 1; then
        if [ "$menu_choice" = 1 ]; then
            decision=yes
        else
            decision=no
        fi
        return
    fi
    if ! printf '%s [Y/n] ' "$2" >/dev/tty 2>/dev/null; then
        decision=yes
        return
    fi
    answer=
    if ! read -r answer < /dev/tty 2>/dev/null; then
        decision=yes
        return
    fi
    case $answer in
        [nN]*) decision=no ;;
        *) decision=yes ;;
    esac
}

ask_yes_default_yes "$service_answer" \
    "Установить автозапуск сервера (launchd/systemd)?" \
    "Сервер будет подниматься сам при входе в систему."
service_answer=$decision
ask_yes_default_yes "$mcp_answer" \
    "Подключить MCP-сервер Claude (claude mcp add)?" \
    "Даёт агентам инструменты listik_* без CLI."
mcp_answer=$decision
ask_yes_default_yes "$plugins_answer" \
    "Установить плагины Claude (marketplace + listik/feature-pipeline)?" \
    "Скилы работы с задачами и конвейеры реализации."
plugins_answer=$decision

service_status=пропущен
if [ "$service_answer" = yes ]; then
    if service_out=$("$wrapper" service install 2>&1); then
        service_status=ok
    else
        service_status="не удалось"
        note "$prog: автозапуск: 'listik service install' не выполнился:" >&2
        note "$service_out" >&2
    fi
fi

mcp_status=пропущен
if [ "$mcp_answer" = yes ]; then
    if command -v claude >/dev/null 2>&1; then
        if claude mcp get listik >/dev/null 2>&1; then
            claude mcp remove --scope user listik >/dev/null 2>&1 || true
        fi
        if claude mcp add --scope user listik -- "$wrapper" mcp >/dev/null 2>&1; then
            mcp_status=ok
        else
            mcp_status="не удалось"
            note "$prog: MCP: команда не выполнилась — подключите вручную:" >&2
            note "  claude mcp add --scope user listik -- \"$wrapper\" mcp" >&2
        fi
    else
        mcp_status="не удалось"
        note "$prog: MCP: нет claude в PATH — подключите вручную:"
        note "  claude mcp add --scope user listik -- \"$wrapper\" mcp"
    fi
fi

plugins_status=пропущен
if [ "$plugins_answer" = yes ]; then
    if command -v claude >/dev/null 2>&1 && claude plugin --help >/dev/null 2>&1; then
        plugins_ok=1
        if ! claude plugin marketplace add dmitry-fomin/listik >/dev/null 2>&1; then
            claude plugin marketplace update listik >/dev/null 2>&1 || plugins_ok=0
        fi
        claude plugin install listik@listik >/dev/null 2>&1 || plugins_ok=0
        claude plugin install feature-pipeline@listik >/dev/null 2>&1 || plugins_ok=0
        claude plugin install dsh@listik >/dev/null 2>&1 || plugins_ok=0
        claude plugin install codex@listik >/dev/null 2>&1 || plugins_ok=0
        claude plugin install second-opinion@listik >/dev/null 2>&1 || plugins_ok=0
        if [ "$plugins_ok" = 1 ]; then
            plugins_status=ok
        else
            plugins_status="не удалось"
            note "$prog: плагины: не все команды claude plugin отработали — поставьте вручную:" >&2
            note "  /plugin marketplace add dmitry-fomin/listik" >&2
            note "  /plugin install listik@listik" >&2
            note "  /plugin install feature-pipeline@listik" >&2
            note "  /plugin install dsh@listik" >&2
            note "  /plugin install codex@listik" >&2
            note "  /plugin install second-opinion@listik" >&2
        fi
    else
        plugins_status="не удалось"
        note "$prog: плагины: claude недоступен (нет в PATH или без подкоманды plugin) — поставьте вручную:"
        note "  /plugin marketplace add dmitry-fomin/listik"
        note "  /plugin install listik@listik"
        note "  /plugin install feature-pipeline@listik"
        note "  /plugin install dsh@listik"
        note "  /plugin install codex@listik"
        note "  /plugin install second-opinion@listik"
    fi
fi

# ------------------------------------ шаг 7.2: Codex и network_access

# Codex в режиме записи ходит в сеть из песочницы, а Listik слушает 127.0.0.1: без
# `network_access = true` в [sandbox_workspace_write] агент не возьмёт задачу,
# не отправит heartbeat и не запишет журнал (listik-htgq).
codex_config=${CODEX_HOME:-$HOME/.codex}/config.toml
codex_status="пропущен (codex не найден)"
codex_reason=
codex_backup=
codex_warn=0

codex_network_enabled() {
    # 0 — в конфиге уже есть [sandbox_workspace_write] network_access = true.
    # Понимает и точечную запись `sandbox_workspace_write.network_access = true`.
    [ -f "$1" ] || return 1
    awk '
        function norm(s) { gsub(/[[:space:]]/, "", s); gsub(/["\047]/, "", s); return s }
        /^[[:space:]]*\[/ {
            section = $0
            sub(/^[[:space:]]*\[/, "", section)
            sub(/\].*$/, "", section)
            section = norm(section)
            next
        }
        {
            line = $0
            sub(/#.*/, "", line)
            if (line !~ /=/) next
            split(line, kv, "=")
            if (norm(kv[2]) != "true") next
            key = norm(kv[1])
            if (section == "sandbox_workspace_write" && key == "network_access") enabled = 1
            if (section == "" && key == "sandbox_workspace_write.network_access") enabled = 1
        }
        END { exit(enabled ? 0 : 1) }
    ' "$1"
}

codex_network_write() {
    # $1 — конфиг: ставит network_access = true в [sandbox_workspace_write].
    # Ключ уже есть — меняет значение, секции нет — дописывает её в конец файла,
    # секция есть без ключа — дописывает ключ внутрь неё (дубль ключа сломал бы TOML).
    cfg=$1
    cfg_new=$cfg.listik-new-$$
    [ -f "$cfg" ] || : > "$cfg" || return 1
    awk '
        function norm(s) { gsub(/[[:space:]]/, "", s); gsub(/["\047]/, "", s); return s }
        function secname(line) {
            sub(/^[[:space:]]*\[/, "", line)
            sub(/\].*$/, "", line)
            return norm(line)
        }
        BEGIN { section = ""; seen = 0; written = 0 }
        /^[[:space:]]*\[/ {
            if (section == "sandbox_workspace_write" && !written) {
                print "network_access = true"
                written = 1
            }
            section = secname($0)
            if (section == "sandbox_workspace_write") seen = 1
            print
            next
        }
        {
            if (section == "sandbox_workspace_write") {
                line = $0
                sub(/#.*/, "", line)
                if (line ~ /=/) {
                    split(line, kv, "=")
                    if (norm(kv[1]) == "network_access") {
                        print "network_access = true"
                        written = 1
                        next
                    }
                }
            }
            print
        }
        END {
            if (written) exit 0
            if (seen) { print "network_access = true"; exit 0 }
            print ""
            print "[sandbox_workspace_write]"
            print "network_access = true"
        }
    ' "$cfg" > "$cfg_new" || return 1
    # Копируем поверх, а не mv: так у конфига сохраняются права (обычно 0600) и
    # ссылка, если ~/.codex/config.toml — симлинк в дотфайлы.
    if ! cp "$cfg_new" "$cfg"; then
        rm -f "$cfg_new"
        return 1
    fi
    rm -f "$cfg_new"
}

codex_enable_network() {
    # $1 — конфиг: резервная копия рядом (.bak-<время>), затем правка (в codex_backup).
    cfg=$1
    codex_backup=
    if [ -f "$cfg" ]; then
        codex_backup=$cfg.bak-$(date +%Y%m%d-%H%M%S)
        cp -p "$cfg" "$codex_backup" || return 1
    else
        mkdir -p "$(dirname "$cfg")" || return 1
    fi
    codex_network_write "$cfg"
}

ask_codex_network() {
    # 0 — дописать настройку, 1 — не трогать (в codex_reason причина).
    codex_reason="нет /dev/tty"
    case $codex_network in
        yes) return 0 ;;
        no)
            codex_reason="--codex-network no"
            return 1
            ;;
    esac
    if [ "$assume_yes" = 1 ]; then
        # Открытый вопрос: считать ли --yes согласием на правку чужого config.toml.
        # Пока --yes на этот вопрос «да» не отвечает и конфиг Codex не трогается.
        codex_reason="--yes"
        return 1
    fi
    if ui_menu2 "Codex: дописать network_access = true?" \
            "В $codex_config нет [sandbox_workspace_write] network_access = true." \
            "Дописать|копия конфига сохранится рядом" \
            "Не трогать|Codex не достучится до сервера Listik" 1; then
        if [ "$menu_choice" = 1 ]; then
            return 0
        fi
        codex_reason="выбрано «не трогать»"
        return 1
    fi
    if ! printf 'Codex: в %s нет [sandbox_workspace_write] network_access = true. Дописать? [Y/n] ' \
            "$codex_config" >/dev/tty 2>/dev/null; then
        return 1
    fi
    answer=
    read -r answer < /dev/tty 2>/dev/null || return 1
    case $answer in
        [nN]*)
            codex_reason="ответ '$answer'"
            return 1
            ;;
        *) return 0 ;;
    esac
}

if command -v codex >/dev/null 2>&1; then
    if codex_network_enabled "$codex_config"; then
        codex_status="уже включён"
    elif ask_codex_network; then
        if codex_enable_network "$codex_config"; then
            codex_status="включён"
            note "$prog: Codex: network_access = true добавлен в $codex_config"
            [ -z "$codex_backup" ] ||
                note "$prog: Codex: резервная копия конфига — $codex_backup"
        else
            codex_status="не удалось"
            codex_warn=1
            note "$prog: Codex: не удалось изменить $codex_config" >&2
        fi
    else
        codex_status="не включён"
        codex_warn=1
    fi
fi

if [ "$codex_warn" = 1 ]; then
    note "$prog: Codex: network_access = true не включён ($codex_reason)" >&2
    note "$prog: без него Codex в режиме записи не достучится до сервера Listik (127.0.0.1):" >&2
    note "$prog: не сможет брать задачи, слать heartbeat и писать журнал." >&2
    note "$prog: допишите в $codex_config сами или повторите с --codex-network yes:" >&2
    note "  [sandbox_workspace_write]" >&2
    note "  network_access = true" >&2
fi

# ------------------------------------------------- шаг 8: routes.json

replace_routes() {
    bak=$runtime_routes.bak-$(date +%Y%m%d-%H%M%S)
    mv "$runtime_routes" "$bak" || die "не удалось переименовать $runtime_routes"
    cp "$sample_routes" "$runtime_routes" || die "не удалось записать $runtime_routes"
    note "$prog: routes.json: рабочая копия заменена, прежняя — $bak"
}

ask_routes() {
    # 0 — заменить, 1 — оставить (в ask_reason причина).
    ask_reason="нет /dev/tty"
    if [ "$assume_yes" = 1 ]; then
        ask_reason="--yes"
        return 1
    fi
    if ui_menu2 "routes.json отличается от нового образца" \
            "Рабочая копия: $runtime_routes" \
            "Оставить мою копию|прежняя копия останется как есть" \
            "Заменить новой|прежняя сохранится рядом как .bak-<время>" 1; then
        if [ "$menu_choice" = 2 ]; then
            return 0
        fi
        ask_reason="выбрано «оставить»"
        return 1
    fi
    if ! printf 'routes.json отличается от нового образца. Заменить рабочую копию? [y/N] ' \
            >/dev/tty 2>/dev/null; then
        return 1
    fi
    answer=
    if ! read -r answer < /dev/tty 2>/dev/null; then
        return 1
    fi
    case $answer in
        [yY]*) return 0 ;;
        *)
            ask_reason="ответ '$answer'"
            return 1
            ;;
    esac
}

runtime_routes=${LISTIK_ROUTES:-$HOME/.config/listik/routes.json}
sample_routes=$app_dir/current/routes.json
if [ ! -f "$runtime_routes" ]; then
    : # копии нет — её создаст сервер при первом старте
elif cmp -s "$sample_routes" "$runtime_routes"; then
    : # копия совпадает с образцом
else
    case $routes_policy in
        keep)
            note "$prog: routes.json: рабочая копия оставлена без изменений, новый образец: $sample_routes"
            ;;
        replace) replace_routes ;;
        ask)
            if ask_routes; then
                replace_routes
            else
                note "$prog: routes.json: рабочая копия оставлена без изменений ($ask_reason), новый образец: $sample_routes"
            fi
            ;;
    esac
fi

# ------------------------------------------------- шаг 9: протокол и шаг 10: сводка

protocol_changed=0
if [ -f "$tmp/prev-protocol.md" ] && [ -f "$code_dir/docs/harness-protocol.md" ]; then
    if ! cmp -s "$tmp/prev-protocol.md" "$code_dir/docs/harness-protocol.md"; then
        protocol_changed=1
    fi
fi

ui_outro
if [ "$ui_enabled" != 1 ]; then
    note "Listik $version установлен."
fi
note "версия:  $version"
note "код:     $app_dir/current"
note "данные:  $home"
note "обёртка: $wrapper"
if [ "$protocol_changed" = 1 ]; then
    note "протокол изменился: выполните listik init-projects (сначала можно с --dry-run)"
fi
note "автозапуск: $service_status"
note "MCP: $mcp_status"
note "плагины: $plugins_status"
note "Codex: $codex_status"
note "дальше:"
if [ "$service_status" = ok ]; then
    note "  listik service status"
    note "  listik token"
else
    note "  listik serve --daemon"
    note "  listik token"
fi

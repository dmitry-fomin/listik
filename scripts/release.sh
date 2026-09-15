#!/bin/sh
# scripts/release.sh — собрать архив релиза Listik и (по флагу --publish) выложить его.
#
# Архив listik-<VERSION>.tar.gz содержит единственный верхний каталог listik-<VERSION>/.
# Состав задан белым списком: код и документация из `git ls-files` плюс собранная доска
# web/dist с файловой системы. Всё остальное (tests/, plugins/, исходники доски,
# неотслеживаемые файлы) в релиз не попадает.
#
# Запуск: scripts/release.sh [--skip-web] [--out КАТАЛОГ] [--publish] [--notes-file ФАЙЛ]
set -eu

prog=release.sh

die() {
    printf '%s: ошибка: %s\n' "$prog" "$1" >&2
    exit 1
}

usage() {
    cat <<'USAGE'
Сборка архива релиза Listik.

Использование: scripts/release.sh [флаги]

  --skip-web           не запускать npm, взять готовый web/dist
  --out <каталог>      куда положить результат (по умолчанию <корень>/dist)
  --publish            после сборки выложить релиз через gh
  --notes-file <файл>  текст заметок для --publish (по умолчанию "Listik v<VERSION>")
  --help               эта справка

Без флагов собирает доску (npm ci && npm run build в web/) и кладёт в каталог
результата listik-<VERSION>.tar.gz и listik-<VERSION>.tar.gz.sha256.
USAGE
}

skip_web=0
publish=0
out=""
notes_file=""

while [ $# -gt 0 ]; do
    case $1 in
        --skip-web) skip_web=1 ;;
        --publish) publish=1 ;;
        --out)
            [ $# -ge 2 ] || die "--out требует каталог"
            out=$2
            shift
            ;;
        --notes-file)
            [ $# -ge 2 ] || die "--notes-file требует файл"
            notes_file=$2
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *) die "неизвестный флаг: $1 (справка: --help)" ;;
    esac
    shift
done

# Корень репозитория — родитель каталога скрипта, от cwd не зависит.
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH= cd -- "$script_dir/.." && pwd)

[ -f "$root/VERSION" ] || die "нет файла VERSION в корне репозитория: $root"
version=$(tr -d '[:space:]' < "$root/VERSION")
printf '%s\n' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' ||
    die "VERSION должен быть вида 1.2.3, а в нём: '$version'"

git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
    die "корень не является рабочим деревом git: $root"

if [ "$publish" = 1 ]; then
    command -v gh >/dev/null 2>&1 || die "gh не найден в PATH — публикация невозможна"
    [ -z "$(git -C "$root" status --porcelain)" ] ||
        die "рабочее дерево грязное: для --publish нужен пустой git status --porcelain"
    [ -z "$(git -C "$root" tag -l "v$version")" ] ||
        die "тег v$version уже существует"
    git -C "$root" ls-files --error-unmatch install.sh >/dev/null 2>&1 ||
        die "install.sh нет в дереве: публиковать можно после порции c"
fi

if [ -z "$out" ]; then
    out=$root/dist
fi
if [ -e "$out" ] && [ ! -d "$out" ]; then
    die "--out указывает на файл, а не на каталог: $out"
fi

web_dist=$root/web/dist
if [ "$skip_web" = 1 ]; then
    if [ ! -d "$web_dist" ] || [ ! -f "$web_dist/index.html" ]; then
        die "нет собранной доски ($web_dist/index.html) — соберите: cd web && npm ci && npm run build"
    fi
else
    (cd "$root/web" && npm ci && npm run build) ||
        die "сборка доски не удалась — cd web && npm ci && npm run build"
fi

mkdir -p "$out" || die "не удалось создать каталог результата: $out"
out=$(CDPATH= cd -- "$out" && pwd)

# Сборка идёт во временном каталоге: в дереве репозитория остаётся только результат.
tmp=$(mktemp -d "${TMPDIR:-/tmp}/listik-release.XXXXXX") ||
    die "не удалось создать временный каталог"
trap 'rm -rf "$tmp"' EXIT HUP INT TERM

stage=$tmp/listik-$version
mkdir -p "$stage"

tracked=$tmp/tracked.txt
selected=$tmp/selected.txt
git -C "$root" ls-files > "$tracked" || die "git ls-files не сработал в $root"

: > "$selected"
while IFS= read -r path; do
    [ -n "$path" ] || continue
    case $path in
        bin/*|listik/*|alembic/*|VERSION|routes.json|config.example.toml|alembic.ini|README.md|docs/API.md|AGENTS.md|docs/harness-protocol.md|install.sh)
            printf '%s\n' "$path" >> "$selected"
            ;;
    esac
done < "$tracked"

for dir in bin listik alembic; do
    grep -q "^$dir/" "$tracked" ||
        die "в git нет ни одного файла под $dir/ — релиз неполный"
done
for path in VERSION routes.json config.example.toml alembic.ini README.md docs/API.md AGENTS.md docs/harness-protocol.md; do
    grep -qxF "$path" "$tracked" ||
        die "обязательный файл $path не отслеживается git — релиз неполный"
done

while IFS= read -r path; do
    [ -n "$path" ] || continue
    mkdir -p "$stage/$(dirname -- "$path")"
    cp -p "$root/$path" "$stage/$path" || die "не удалось скопировать $path"
done < "$selected"

# Единственное исключение из белого списка: собранная доска берётся с файловой системы.
mkdir -p "$stage/web"
cp -R "$web_dist" "$stage/web/dist" || die "не удалось скопировать доску $web_dist"

archive_name=listik-$version.tar.gz
archive=$out/$archive_name
sum=$archive.sha256

# COPYFILE_DISABLE — чтобы bsdtar на macOS не дописывал в архив AppleDouble-файлы ._*
# для всего, у чего есть расширенные атрибуты; на GNU tar переменная не влияет.
COPYFILE_DISABLE=1 tar -czf "$archive" -C "$tmp" "listik-$version" ||
    die "tar не смог собрать $archive_name"

if command -v sha256sum >/dev/null 2>&1; then
    digest=$(sha256sum "$archive")
elif command -v shasum >/dev/null 2>&1; then
    digest=$(shasum -a 256 "$archive")
else
    die "нет ни sha256sum, ни shasum — нечем посчитать контрольную сумму"
fi
digest=${digest%% *}
printf '%s  %s\n' "$digest" "$archive_name" > "$sum"

if [ "$publish" = 1 ]; then
    if [ -n "$notes_file" ]; then
        notes=$notes_file
    else
        notes=$tmp/notes.txt
        printf 'Listik v%s\n' "$version" > "$notes"
    fi
    # Тег создаёт сам gh; git tag и git push скрипт не вызывает.
    gh release create "v$version" "$archive" "$sum" "$root/install.sh" \
        --title "v$version" --notes-file "$notes" ||
        die "gh release create завершился с ошибкой"
fi

printf 'архив: %s\n' "$archive"
printf 'сумма: %s\n' "$sum"

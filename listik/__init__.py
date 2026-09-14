"""Хаб задач Listik: единый индекс, поиск и доска по всем проектам."""
from pathlib import Path


def _read_version() -> str:
    """Версия из файла `VERSION` рядом с пакетом; нет файла — «0+unknown»."""
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text(
            encoding="utf-8").strip()
    except OSError:
        return "0+unknown"


__version__ = _read_version()

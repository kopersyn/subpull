"""Plural forms per language (CLDR categories for whole numbers).

A translation may be a single string, or – when the wording depends on a number – an object
with one string per plural category, e.g. English ``{"one": "{count} file", "other": "{count} files"}``
or Polish ``{"one": …, "few": …, "many": …, "other": …}``. The category is picked from the
``count`` value. Languages not listed here use the English rule (``one`` for 1, else ``other``).
"""
from __future__ import annotations

from typing import Callable

CATEGORIES = ("zero", "one", "two", "few", "many", "other")


def _english(n: int) -> str:
    return "one" if n == 1 else "other"


def _none(n: int) -> str:
    return "other"


def _french(n: int) -> str:
    return "one" if n in (0, 1) else "other"


def _polish(n: int) -> str:
    if n == 1:
        return "one"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "few"
    return "many"


def _east_slavic(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "one"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "few"
    return "many"


def _south_slavic(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "one"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "few"
    return "other"


def _czech(n: int) -> str:
    if n == 1:
        return "one"
    return "few" if 2 <= n <= 4 else "other"


def _slovenian(n: int) -> str:
    return {1: "one", 2: "two", 3: "few", 4: "few"}.get(n % 100, "other")


def _lithuanian(n: int) -> str:
    if n % 10 == 1 and not 11 <= n % 100 <= 19:
        return "one"
    if 2 <= n % 10 <= 9 and not 11 <= n % 100 <= 19:
        return "few"
    return "other"


def _latvian(n: int) -> str:
    if n % 10 == 0 or 11 <= n % 100 <= 19:
        return "zero"
    return "one" if n % 10 == 1 and n % 100 != 11 else "other"


def _romanian(n: int) -> str:
    if n == 1:
        return "one"
    return "few" if n == 0 or 1 <= n % 100 <= 19 else "other"


def _arabic(n: int) -> str:
    if n in (0, 1, 2):
        return ("zero", "one", "two")[n]
    if 3 <= n % 100 <= 10:
        return "few"
    return "many" if 11 <= n % 100 <= 99 else "other"


def _hebrew(n: int) -> str:
    return {1: "one", 2: "two"}.get(n, "other")


_RULES: dict[str, Callable[[int], str]] = {
    **dict.fromkeys(("ja", "zh", "ko", "vi", "th", "id", "ms", "lo", "my", "km"), _none),
    **dict.fromkeys(("fr", "pt", "hi", "bn", "fa", "hy"), _french),
    "pl": _polish,
    **dict.fromkeys(("ru", "uk", "be"), _east_slavic),
    **dict.fromkeys(("sr", "hr", "bs"), _south_slavic),
    **dict.fromkeys(("cs", "sk"), _czech),
    "sl": _slovenian, "lt": _lithuanian, "lv": _latvian, "ro": _romanian, "ar": _arabic, "he": _hebrew,
}


def _rule(code: str) -> Callable[[int], str]:
    return _RULES.get(code.split("-")[0].lower(), _english)


def category(code: str, count) -> str:
    """Plural category of ``count`` in language ``code`` (``other`` for anything that is not a whole number)."""
    try:
        if isinstance(count, bool) or int(count) != count:
            return "other"
        return _rule(code)(abs(int(count)))
    except (TypeError, ValueError):
        return "other"


def categories(code: str) -> tuple[str, ...]:
    """The plural categories language ``code`` uses, in canonical order (always includes ``other``)."""
    rule = _rule(code)
    used = {rule(n) for n in range(0, 201)} | {"other"}
    return tuple(c for c in CATEGORIES if c in used)

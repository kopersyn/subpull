"""Check the translation files.  Usage::

    python -m subpull.i18n.validate              # every file in subpull/i18n/locales
    python -m subpull.i18n.validate pl.json      # only some files (names or paths)
    python -m subpull.i18n.validate --strict     # missing translations fail too

Errors (exit status 1): invalid JSON, duplicate keys, broken ``_meta``, keys that do not exist
in English, wrong value types, wrong plural forms and ``{placeholders}`` that differ from the
English text. Missing translations are only reported – the application shows English for them.
Needs nothing but Python itself.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import plurals
from .locale_file import (CODE_RE, DIRECTIONS, LOCALES_DIR, SOURCE_LANGUAGE, LocaleFile, LocaleFileError,
                          is_translated, load, locale_paths, placeholders)


@dataclass
class Report:
    path: Path
    code: str
    errors: list[tuple[Optional[str], str]] = field(default_factory=list)      # (key or None, message)
    missing: list[str] = field(default_factory=list)                           # keys without a translation
    identical: list[str] = field(default_factory=list)                         # same text as English
    total: int = 0                                                             # number of English strings

    def error(self, key: Optional[str], message: str) -> None:
        self.errors.append((key, message))

    @property
    def translated(self) -> int:
        return self.total - len(self.missing)

    @property
    def completion(self) -> int:
        return 100 if not self.total else self.translated * 100 // self.total


def _names(found: set[str]) -> str:
    return ", ".join("{%s}" % n for n in sorted(found)) or "none"


def _placeholders(report: Report, key: str, text: str, form: str = "") -> Optional[set[str]]:
    try:
        return placeholders(text)
    except ValueError as e:
        report.error(key, f"{form}unbalanced braces – write placeholders exactly like {{count}} ({e})")
        return None


def _safe_placeholders(text) -> set[str]:
    try:
        return placeholders(text) if isinstance(text, str) else set()
    except ValueError:
        return set()


def _check_meta(report: Report, lf: LocaleFile) -> None:
    meta, stem = lf.meta, lf.path.stem
    code = meta.get("code")
    if code != stem:
        report.error(None, f"_meta.code is {code!r} but the file is named {lf.path.name} – they must match")
    if not CODE_RE.match(stem):
        report.error(None, f"'{stem}' is not a valid language code (examples: de, pt-BR, zh-Hans)")
    for name in ("name", "english_name"):
        if not isinstance(meta.get(name), str) or not meta[name].strip():
            report.error(None, f"_meta.{name} is missing or empty")
    if meta.get("direction") not in DIRECTIONS:
        report.error(None, "_meta.direction must be \"ltr\" (left to right) or \"rtl\" (right to left)")
    maintainers = meta.get("maintainers", [])
    if not isinstance(maintainers, list) or not all(isinstance(m, str) for m in maintainers):
        report.error(None, "_meta.maintainers must be a list of names, e.g. [\"your-github-name\"]")


def _check_value(report: Report, code: str, key: str, value, source) -> None:
    """Compare one translation with its English source (``source`` is None for English itself)."""
    reference = source if source is not None else value
    ref_forms = reference if isinstance(reference, dict) else {"other": reference}
    expected_all = set().union(*(_safe_placeholders(v) for v in ref_forms.values()))
    expected_other = _safe_placeholders(ref_forms.get("other"))

    if isinstance(value, str):
        found = _placeholders(report, key, value)
        if found is not None and source is not None and found != expected_other:
            report.error(key, f"placeholders differ from English: expected {_names(expected_other)}, "
                              f"found {_names(found)}")
        return
    if not isinstance(value, dict):
        report.error(key, f"must be text in quotes, not {type(value).__name__}")
        return
    if source is not None and "count" not in expected_all:
        report.error(key, "plural forms are only possible for texts with a {count} placeholder")
        return
    allowed = plurals.categories(code)
    if not isinstance(value.get("other"), str):
        report.error(key, "plural forms need an \"other\" form")
    for form, text in value.items():
        if form not in allowed:
            report.error(key, f"plural form \"{form}\" is not used in this language (allowed: {', '.join(allowed)})")
            continue
        if not isinstance(text, str):
            report.error(key, f"plural form \"{form}\" must be text in quotes")
            continue
        found = _placeholders(report, key, text, f"plural form \"{form}\": ")
        if found is None:
            continue
        if not found <= expected_all or (form == "other" and found != expected_other):
            report.error(key, f"plural form \"{form}\": placeholders differ from English: expected "
                              f"{_names(expected_other)}, found {_names(found)}")


def check_file(path: Path, source: Optional[LocaleFile]) -> Report:
    """Validate one locale file against the English ``source`` (None when ``path`` is the English file)."""
    report = Report(Path(path), Path(path).stem)
    try:
        lf = load(path)
    except LocaleFileError as e:
        report.error(None, str(e))
        return report
    _check_meta(report, lf)
    for key in sorted(set(lf.duplicates)):
        report.error(key, "appears more than once in the file – keep only one")
    reference = source.translations if source is not None else lf.translations
    report.total = len(reference)
    for key, value in lf.translations.items():
        if source is not None and key not in reference:
            report.error(key, "does not exist in English (en.json) – keys must not be renamed or added")
            continue
        if value == "" or value is None:
            continue                                   # not translated yet – reported as missing below
        _check_value(report, report.code, key, value, reference[key] if source is not None else None)
        if source is not None and value == reference[key]:
            report.identical.append(key)
    report.missing = [k for k in reference if not is_translated(lf.translations.get(k))]
    if source is None:
        for key in report.missing:
            report.error(key, "the English source text must not be empty")
    return report


def check_all(paths: Optional[list[Path]] = None, directory: Path = LOCALES_DIR) -> list[Report]:
    """Validate the given files (default: all), always against ``<directory>/en.json``."""
    source_path = directory / f"{SOURCE_LANGUAGE}.json"
    reports = {}
    source: Optional[LocaleFile] = None
    en_report = check_file(source_path, None)
    try:
        source = load(source_path)
    except LocaleFileError:
        pass
    for path in (paths if paths else locale_paths(directory)):
        path = Path(path)
        if path.resolve() == source_path.resolve():
            reports[path] = en_report
        elif source is None:
            r = Report(path, path.stem)
            r.error(None, f"cannot be checked because {source_path.name} is broken")
            reports[path] = r
        else:
            reports[path] = check_file(path, source)
    if source is None and source_path not in reports:
        reports[source_path] = en_report
    return list(reports.values())


def _line_of(path: Path, key: Optional[str]) -> int:
    if key:
        try:
            for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
                if line.lstrip().startswith(f'"{key}"'):
                    return n
        except (OSError, UnicodeDecodeError):
            pass
    return 1


def _relative(path: Path) -> str:
    """Path relative to the working directory (the repository root in CI), for GitHub annotations."""
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _resolve(arg: str, directory: Path) -> Path:
    p = Path(arg)
    if p.exists():
        return p
    return directory / (arg if arg.endswith(".json") else f"{arg}.json")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m subpull.i18n.validate", description="Validate SubPull translation files.")
    ap.add_argument("files", nargs="*", help="language codes, file names or paths (default: all languages)")
    ap.add_argument("--strict", action="store_true", help="treat missing translations as errors")
    ap.add_argument("--dir", type=Path, default=LOCALES_DIR, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")       # Windows consoles
    annotate = bool(os.environ.get("GITHUB_ACTIONS"))
    reports = check_all([_resolve(f, args.dir) for f in args.files] or None, args.dir)
    failed = False
    for r in reports:
        name = r.path.name
        for key, message in r.errors:
            text = f"[{key}] {message}" if key else message
            print(f"ERROR    {name}: {text}")
            if annotate:
                print(f"::error file={_relative(r.path)},line={_line_of(r.path, key)},title=Translation {r.code}::{text}")
        for key in r.missing if r.code != SOURCE_LANGUAGE else ():
            print(f"{'ERROR  ' if args.strict else 'missing'}  {name}: [{key}] not translated yet – English is shown")
        if r.identical and len(r.identical) * 4 > r.total:
            print(f"note     {name}: {len(r.identical)} texts are identical to English – set texts you have not "
                  "translated yet to \"\" so they are not counted as translated")
        status = "FAILED" if r.errors else "ok"
        progress = f"{r.translated}/{r.total} translated ({r.completion}%), " if r.total else ""
        print(f"{status:8} {name}: {progress}{len(r.errors)} error(s), {len(r.missing)} missing")
        failed = failed or bool(r.errors) or (args.strict and bool(r.missing))
    print("\nTranslation check FAILED – see the ERROR lines above." if failed else "\nAll translation files are valid.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

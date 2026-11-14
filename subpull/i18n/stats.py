"""Translation progress, calculated from the locale files.  Usage::

    python -m subpull.i18n.stats            # print the table
    python -m subpull.i18n.stats --write    # update the tables in README.md and translations/README.md
    python -m subpull.i18n.stats --check    # exit 1 when those tables are out of date

Completion is the share of English texts that have a (non-empty) translation – nobody has to
maintain the numbers by hand.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

from .locale_file import LOCALES_DIR, SOURCE_LANGUAGE, LocaleFileError, load
from .validate import Report, check_all

REPO_ROOT = Path(__file__).resolve().parents[2]
STATUS_FILES = ("README.md", "translations/README.md")
START, END = "<!-- translation-status:start -->", "<!-- translation-status:end -->"
_BLOCK = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)


def _row(r: Report) -> str:
    try:
        meta = load(r.path).meta
    except LocaleFileError:
        return f"| {r.code} | `{r.code}` | – | File cannot be read | |"
    name, english = str(meta.get("name") or r.code), str(meta.get("english_name") or r.code)
    language = name if name == english else f"{name} ({english})"
    if r.code == SOURCE_LANGUAGE:
        status = "Source language"
    elif r.errors:
        status = "Needs fixes"
    else:
        status = "Complete" if not r.missing else "In progress"
    maintainers = ", ".join(str(m) for m in meta.get("maintainers") or [])
    return f"| {language} | `{r.code}` | {r.completion}% ({r.translated}/{r.total}) | {status} | {maintainers} |"


def table(directory: Path = LOCALES_DIR) -> str:
    reports = sorted(check_all(directory=directory), key=lambda r: (r.code != SOURCE_LANGUAGE, -r.completion, r.code))
    lines = ["| Language | Code | Translated | Status | Maintainers |", "|---|---|---|---|---|"]
    return "\n".join(lines + [_row(r) for r in reports])


def update_file(path: Path, tbl: str, write: bool) -> bool:
    """Put ``tbl`` between the status markers of ``path``. Returns True when the file is (now) up to date."""
    text = path.read_text(encoding="utf-8")
    if not _BLOCK.search(text):
        print(f"{path}: status markers not found", file=sys.stderr)
        return False
    new = _BLOCK.sub(lambda _: f"{START}\n{tbl}\n{END}", text)
    if new == text:
        return True
    if write:
        path.write_text(new, encoding="utf-8", newline="\n")
        print(f"updated {path}")
    return write


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m subpull.i18n.stats", description="SubPull translation progress.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="update the status tables in the documentation")
    mode.add_argument("--check", action="store_true", help="fail when the status tables are out of date")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    tbl = table()
    if not (args.write or args.check):
        print(tbl)
        return 0
    current = [update_file(REPO_ROOT / f, tbl, args.write) for f in STATUS_FILES]
    if args.check and not all(current):
        print("The translation status tables are out of date – run: python -m subpull.i18n.stats --write")
        return 1
    return 0 if all(current) else 1


if __name__ == "__main__":
    sys.exit(main())

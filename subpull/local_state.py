"""Destination paths and local file state (duplicates, partial downloads)."""
from __future__ import annotations

import re
from collections import Counter
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

from . import config

# Names are validated against Windows rules on every platform so a library stays portable.
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class InvalidName(Exception):
    pass


class LocalStatus(str, Enum):
    NEW = "new"
    PRESENT = "already local"                     # same name, same size
    CONFLICT = "size conflict"                    # same name, different size
    PARTIAL = "partially downloaded"              # a partial file exists – can resume
    HISTORY_ONLY = "downloaded before (missing)"  # in history, but no longer on disk
    NAME_CLASH = "name clash"                     # ≥2 remote files would land on the same path


def validate_name(name: str, what: str) -> None:
    if not name or name in (".", ".."):
        raise InvalidName(f"Empty or invalid {what} name")
    if _INVALID.search(name) or name.endswith((" ", ".")) or name.split(".")[0].upper() in _RESERVED:
        raise InvalidName(f"{what.capitalize()} name '{name}' is not a valid folder/file name – skipped (names are never changed)")


def dest_dir(object_name: str, root: Path) -> Path:
    validate_name(object_name, "object")
    return Path(root) / object_name / config.SUBS_DIRNAME


def dest_path(object_name: str, filename: str, root: Path) -> Path:
    validate_name(filename, "file")
    return dest_dir(object_name, root) / filename


def part_path(dest: Path) -> Path:
    return dest.with_name(dest.name + config.PART_SUFFIX)


def name_clashes(files: Iterable, root: Path) -> set[str]:
    """remote_path of every scanned file whose destination collides with another scanned file."""
    keyed = {}
    for f in files:
        try:
            keyed[f.remote_path] = str(dest_path(f.object_name, f.name, root)).lower()
        except InvalidName:
            continue
    counts = Counter(keyed.values())
    return {rp for rp, k in keyed.items() if counts[k] > 1}


def local_status(f, history_rows: dict, clashes: set[str], root: Path) -> tuple[LocalStatus, Optional[str]]:
    """Return (status, detail)."""
    try:
        dest = dest_path(f.object_name, f.name, root)
    except InvalidName as e:
        return LocalStatus.NAME_CLASH, str(e)
    if dest.exists():
        lsize = dest.stat().st_size
        if lsize == f.size:
            return LocalStatus.PRESENT, None
        return LocalStatus.CONFLICT, f"local {lsize} B, server {f.size} B"
    if f.remote_path in clashes:
        return LocalStatus.NAME_CLASH, "another scanned file has the same name for this object"
    if part_path(dest).exists():
        return LocalStatus.PARTIAL, f"{part_path(dest).stat().st_size} B downloaded"
    row = history_rows.get(f.remote_path)
    if row is not None:
        return LocalStatus.HISTORY_ONLY, f"was saved to {row['local_path']}"
    return LocalStatus.NEW, None

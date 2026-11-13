"""Recognition of session dates, frame types and frame files (pure logic, no I/O)."""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from .config import PART_SUFFIX

FRAME_TYPES = ("LIGHT", "DARK", "FLAT", "BIAS", "DARKFLAT")

# Frame-type directory names (scheme A). Case-insensitive.
_DIR_ALIASES = {
    "LIGHT": "LIGHT", "LIGHTS": "LIGHT",
    "DARK": "DARK", "DARKS": "DARK",
    "FLAT": "FLAT", "FLATS": "FLAT",
    "BIAS": "BIAS", "BIASES": "BIAS", "BIASS": "BIAS",
    "DARKFLAT": "DARKFLAT", "DARKFLATS": "DARKFLAT",
    "FLATDARK": "DARKFLAT", "FLATDARKS": "DARKFLAT",
}

# File name prefixes (scheme B). Longest first.
_FILE_PREFIXES = (
    ("DARKFLAT_", "DARKFLAT"),
    ("FLATDARK_", "DARKFLAT"),
    ("LIGHT_", "LIGHT"),
    ("DARK_", "DARK"),
    ("FLAT_", "FLAT"),
    ("BIAS_", "BIAS"),
)

# FITS IMAGETYP values.
_IMAGETYP = {
    "LIGHT": "LIGHT", "LIGHT FRAME": "LIGHT", "OBJECT": "LIGHT", "SCIENCE": "LIGHT",
    "DARK": "DARK", "DARK FRAME": "DARK",
    "FLAT": "FLAT", "FLAT FRAME": "FLAT", "FLAT FIELD": "FLAT", "FLATFIELD": "FLAT",
    "BIAS": "BIAS", "BIAS FRAME": "BIAS", "OFFSET": "BIAS", "ZERO": "BIAS",
    "DARKFLAT": "DARKFLAT", "DARK FLAT": "DARKFLAT", "FLAT DARK": "DARKFLAT", "FLATDARK": "DARKFLAT",
}

FITS_EXTENSIONS = (".fit", ".fits", ".fts", ".fitz", ".fz")
FRAME_EXTENSIONS = FITS_EXTENSIONS + (".xisf", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".dng", ".orf", ".rw2")

_DATE_RE = re.compile(r"^(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})(?:$|[ _T-].*)")


def _norm(s: str) -> str:
    return re.sub(r"[\s\-]+", "", s.strip().upper())


def parse_session_date(name: str) -> Optional[str]:
    """'2025-08-15' -> '2025-08-15'; None if the name is not a date."""
    m = _DATE_RE.match(name.strip())
    if not m:
        return None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return d.isoformat()


def frame_type_from_dirname(name: str) -> Optional[str]:
    return _DIR_ALIASES.get(_norm(name).replace("_", ""))


def frame_type_from_filename(name: str) -> Optional[str]:
    up = name.upper()
    for prefix, ft in _FILE_PREFIXES:
        if up.startswith(prefix):
            return ft
    return None


def frame_type_from_imagetyp(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = re.sub(r"\s+", " ", value.strip().upper().replace("_", " "))
    return _IMAGETYP.get(v)


def is_frame_file(name: str) -> bool:
    low = name.lower()
    if low.startswith(".") or low.endswith(PART_SUFFIX):
        return False
    return low.endswith(FRAME_EXTENSIONS)


def is_fits(name: str) -> bool:
    return name.lower().endswith(FITS_EXTENSIONS)


def same_object(a: Optional[str], b: Optional[str]) -> bool:
    if a is None or b is None:
        return False
    return _norm(a).replace("_", "") == _norm(b).replace("_", "")

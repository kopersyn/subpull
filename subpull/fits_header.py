"""Read FITS headers straight from the server (only the 2880-byte header blocks)."""
from __future__ import annotations

import logging
from typing import BinaryIO, Optional

log = logging.getLogger(__name__)

BLOCK = 2880
CARD = 80
MAX_HEADER_BLOCKS = 12          # ~34 KB per header – plenty


class FitsHeaderError(Exception):
    pass


def _parse_value(raw: str):
    raw = raw.strip()
    if raw.startswith("'"):
        # string value; '' is an escaped quote
        out, i = [], 1
        while i < len(raw):
            c = raw[i]
            if c == "'":
                if i + 1 < len(raw) and raw[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                break
            out.append(c)
            i += 1
        return "".join(out).rstrip()
    token = raw.split("/", 1)[0].strip()
    if token == "T":
        return True
    if token == "F":
        return False
    try:
        return int(token)
    except ValueError:
        try:
            return float(token.replace("D", "E"))
        except ValueError:
            return token or None


def parse_cards(data: bytes) -> tuple[dict, bool]:
    """Parse header cards; returns (keywords, END found)."""
    cards: dict = {}
    for i in range(0, len(data) - CARD + 1, CARD):
        card = data[i:i + CARD].decode("ascii", "replace")
        key = card[:8].strip()
        if key == "END":
            return cards, True
        if card[8:10] == "= " and key and key not in cards:
            cards[key] = _parse_value(card[10:])
    return cards, False


def _read_hdu_header(f: BinaryIO, first: bytes = b"") -> dict:
    """Read 2880-byte blocks until the END card; the stream ends up just past the header."""
    buf = first
    if buf:
        cards, found = parse_cards(buf)
        if found:
            return cards
    for _ in range(MAX_HEADER_BLOCKS):
        chunk = f.read(BLOCK)
        if not chunk:
            break
        buf += chunk
        cards, found = parse_cards(buf)
        if found:
            return cards
        if len(chunk) < BLOCK:
            break
    raise FitsHeaderError("END card not found in header")


def read_header(f: BinaryIO) -> dict:
    """Read the primary header. For tile-compressed files (fpack .fz) whose primary
    HDU is empty, the first extension header – which holds the real keywords – is merged in."""
    head = f.read(BLOCK)
    if not head.startswith(b"SIMPLE  ="):
        raise FitsHeaderError("Not a FITS file")
    primary = _read_hdu_header(f, head)
    if primary.get("NAXIS") == 0 and primary.get("EXTEND") and "OBJECT" not in primary:
        try:
            for k, v in _read_hdu_header(f).items():
                primary.setdefault(k, v)
        except FitsHeaderError:
            pass
    return primary


def summarize(cards: dict) -> dict:
    return {k: cards[k] for k in ("OBJECT", "IMAGETYP", "FILTER", "EXPTIME", "DATE-OBS")
            if k in cards and cards[k] not in (None, "")}


def read_remote_header(fs, path: str) -> Optional[dict]:
    """Selected keywords, or None when the header cannot be read.
    Network errors propagate so the caller can decide to retry."""
    from .connection import RemotePathError
    try:
        f = fs.open_read(path)
    except RemotePathError as e:
        log.info("FITS header unavailable: %s", e)
        return None
    try:
        return summarize(read_header(f))
    except FitsHeaderError as e:
        log.info("FITS header unreadable (%s): %s", path, e)
        return None
    finally:
        try:
            f.close()
        except Exception:
            pass

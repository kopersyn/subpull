"""Scan the remote directory tree and recognize the frame layouts.

Scheme A:  DATE/OBJECT/TYPE/files          (type from the directory name)
Scheme B:  DATE/OBJECT/TYPE_xxx.fits       (type from the file name prefix)
Scheme A0: DATE/TYPE/files                 (no object directory; the object comes
                                            only from the FITS OBJECT keyword)

The scheme is detected per object directory, so all of them may coexist –
even inside a single directory.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from . import classify, config
from .connection import ConnectionFailed, RemoteFS, RemotePathError, join
from .i18n import t

log = logging.getLogger(__name__)

SCHEME_A = "A"
SCHEME_B = "B"
SCHEME_A0 = "A0"

SRC_DIR = "directory"
SRC_PREFIX = "prefix"
SRC_FITS = "FITS"

FITS_NOT_READ, FITS_OK, FITS_MISSING, FITS_NA = "not read", "ok", "unreadable", "n/a"
_FITS_STATUS_KEYS = {FITS_NOT_READ: "not_read", FITS_OK: "ok", FITS_MISSING: "unreadable", FITS_NA: "na"}


def fits_status_label(status: str) -> str:
    """The FITS header status as shown to the user."""
    key = _FITS_STATUS_KEYS.get(status)
    return t(f"fits.status.{key}") if key else status


@dataclass
class FrameFile:
    remote_path: str
    name: str
    size: int
    mtime: int
    session_date: str
    object_name: Optional[str]
    frame_type: Optional[str]
    scheme: str
    object_source: Optional[str] = SRC_DIR
    type_source: Optional[str] = None
    fits: Optional[dict] = None
    fits_status: str = FITS_NOT_READ
    notes: list[str] = field(default_factory=list)

    @property
    def filter(self) -> Optional[str]:
        return (self.fits or {}).get("FILTER") or None

    @property
    def downloadable(self) -> bool:
        return bool(self.object_name)

    def apply_fits(self, header: Optional[dict],
                   placeholders: Iterable[str] = config.DEFAULT_PLACEHOLDER_OBJECTS) -> None:
        if header is None:
            self.fits_status = FITS_MISSING
            if self.object_name is None:
                self._note(t("note.no_header"))
            return
        self.fits = header
        self.fits_status = FITS_OK
        obj = str(header.get("OBJECT") or "").strip()
        obj_valid = obj.upper() not in {p.upper() for p in placeholders}
        if self.object_name is None:
            if obj_valid:
                self.object_name = obj
                self.object_source = SRC_FITS
            else:
                self._note(t("note.object_placeholder", value=obj))
        elif obj_valid and not classify.same_object(obj, self.object_name):
            self._note(t("note.object_mismatch", fits=obj, directory=self.object_name))
        ftype = classify.frame_type_from_imagetyp(header.get("IMAGETYP"))
        if self.frame_type is None and ftype:
            self.frame_type = ftype
            self.type_source = SRC_FITS
        elif ftype and self.frame_type and ftype != self.frame_type:
            self._note(t("note.type_mismatch", fits=header.get("IMAGETYP"), type=self.frame_type))

    def _note(self, msg: str) -> None:
        if msg not in self.notes:
            self.notes.append(msg)


@dataclass
class Unrecognized:
    remote_path: str
    reason: str
    size: int = 0


@dataclass
class ScanResult:
    root: str
    files: list[FrameFile] = field(default_factory=list)
    unrecognized: list[Unrecognized] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    date_dirs: int = 0
    started: float = 0.0
    finished: float = 0.0
    cancelled: bool = False

    @property
    def objects(self) -> dict[str, list[FrameFile]]:
        out: dict[str, list[FrameFile]] = {}
        for f in self.files:
            if f.object_name:
                out.setdefault(f.object_name, []).append(f)
        return out


class _Cancelled(Exception):
    pass


class Scanner:
    def __init__(self, fs: RemoteFS, root: str, progress: Optional[Callable[[str], None]] = None,
                 cancel: Optional[threading.Event] = None, max_search_depth: int = 4):
        self.fs = fs
        self.root = root
        self.progress = progress or (lambda m: None)
        self.cancel = cancel or threading.Event()
        self.max_search_depth = max_search_depth
        self.result = ScanResult(root=root)

    def _list(self, path: str):
        if self.cancel.is_set():
            raise _Cancelled()
        try:
            return sorted(self.fs.listdir(path), key=lambda e: e.name)
        except RemotePathError as e:
            self.result.errors.append(str(e))
            log.warning("Skipped directory: %s", e)
            return []

    def _add(self, path: str, e, date: str, obj: Optional[str], ftype: Optional[str],
             scheme: str, type_source: Optional[str]) -> None:
        ff = FrameFile(remote_path=path, name=e.name, size=e.size, mtime=e.mtime,
                       session_date=date, object_name=obj, frame_type=ftype, scheme=scheme,
                       object_source=SRC_DIR if obj else None, type_source=type_source,
                       fits_status=FITS_NOT_READ if classify.is_fits(e.name) else FITS_NA)
        if ftype is None:
            ff.notes.append(t("note.type_unknown"))
        self.result.files.append(ff)

    def scan(self) -> ScanResult:
        self.result.started = time.time()
        try:
            self._search(self.root, 0)
        except _Cancelled:
            self.result.cancelled = True
        self.result.finished = time.time()
        return self.result

    def _search(self, path: str, depth: int) -> None:
        for e in self._list(path):
            if not e.is_dir:
                continue
            p = join(path, e.name)
            date = classify.parse_session_date(e.name)
            if date:
                self.result.date_dirs += 1
                self.progress(t("scan.progress.session", path=p))
                self._date_dir(p, date)
            elif depth < self.max_search_depth:
                self._search(p, depth + 1)

    def _date_dir(self, path: str, date: str) -> None:
        for e in self._list(path):
            p = join(path, e.name)
            if e.is_dir:
                ftype = classify.frame_type_from_dirname(e.name)
                if ftype:   # DATE/TYPE/ – no object directory
                    self._type_dir(p, date, None, ftype, SCHEME_A0)
                else:
                    self._object_dir(p, date, e.name)
            elif classify.is_frame_file(e.name):
                ftype = classify.frame_type_from_filename(e.name)
                self._add(p, e, date, None, ftype, SCHEME_A0, SRC_PREFIX if ftype else None)

    def _object_dir(self, path: str, date: str, obj: str) -> None:
        for e in self._list(path):
            p = join(path, e.name)
            if e.is_dir:
                ftype = classify.frame_type_from_dirname(e.name)
                if ftype:
                    self._type_dir(p, date, obj, ftype, SCHEME_A)
                else:
                    self.result.unrecognized.append(
                        Unrecognized(p + "/", t("unrecognized.reason.not_frame_type", name=e.name)))
            elif classify.is_frame_file(e.name):
                ftype = classify.frame_type_from_filename(e.name)
                self._add(p, e, date, obj, ftype, SCHEME_B, SRC_PREFIX if ftype else None)

    def _type_dir(self, path: str, date: str, obj: Optional[str], ftype: str, scheme: str) -> None:
        for e in self._list(path):
            p = join(path, e.name)
            if e.is_dir:   # e.g. per-filter subdirectories – the type is inherited
                self._type_dir(p, date, obj, ftype, scheme)
            elif classify.is_frame_file(e.name):
                self._add(p, e, date, obj, ftype, scheme, SRC_DIR)


def finalize(result: ScanResult) -> None:
    """Move files without a known object to the unrecognized list."""
    keep = []
    for f in result.files:
        if f.object_name:
            keep.append(f)
        else:
            reason = "; ".join(f.notes) or t("unrecognized.reason.no_object")
            result.unrecognized.append(Unrecognized(f.remote_path, reason, f.size))
    result.files = keep


def read_headers(files: list[FrameFile], connect: Callable[[], RemoteFS], workers: int = config.HEADER_READERS,
                 cancel: Optional[threading.Event] = None, progress: Optional[Callable[[int, int], None]] = None,
                 placeholders: Iterable[str] = config.DEFAULT_PLACEHOLDER_OBJECTS) -> int:
    """Read FITS headers in parallel (one session per worker thread). Returns how many were read."""
    from .fits_header import read_remote_header
    cancel = cancel or threading.Event()
    targets = [f for f in files if classify.is_fits(f.name)]
    if not targets:
        return 0
    placeholders = list(placeholders)
    lock = threading.Lock()
    done = [0, 0]
    local = threading.local()
    sessions: list = []

    def get_fs():
        fs = getattr(local, "fs", None)
        if fs is None:
            fs = local.fs = connect()
            with lock:
                sessions.append(fs)
        return fs

    def work(f: FrameFile):
        if cancel.is_set():
            return
        hdr = None
        for attempt in range(2):
            try:
                hdr = read_remote_header(get_fs(), f.remote_path)
                break
            except ConnectionFailed as e:
                log.warning("Header %s: %s – reconnecting", f.remote_path, e)
                local.fs = None
            except Exception as e:  # a header must never block the workflow
                log.info("Header %s: %s", f.remote_path, e)
                break
        f.apply_fits(hdr, placeholders)
        with lock:
            done[0] += 1
            if hdr:
                done[1] += 1
            n = done[0]
        if progress:
            progress(n, len(targets))

    try:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(targets)))) as ex:
            list(ex.map(work, targets))
    finally:
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass
    return done[1]

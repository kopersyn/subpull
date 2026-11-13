"""Local stand-in for RemoteFS used only by the tests – a temporary directory plays the server tree."""
from __future__ import annotations

import io
import os
import socket
from pathlib import Path
from typing import Optional

from subpull.connection import RemoteEntry, RemotePathError, ConnectionFailed


def fits_bytes(obj: str = "M 31", imagetyp: str = "LIGHT", filt: str = "Ha", data_blocks: int = 2) -> bytes:
    cards = [
        "SIMPLE  =                    T",
        "BITPIX  =                   16",
        "NAXIS   =                    2",
        f"IMAGETYP= '{imagetyp}'",
        f"OBJECT  = '{obj}'",
        f"FILTER  = '{filt}'",
        "EXPTIME =                300.0 / s",
        "END",
    ]
    hdr = b"".join(c.ljust(80).encode() for c in cards)
    hdr = hdr.ljust(2880, b" ")
    return hdr + os.urandom(2880 * data_blocks)


class _FailingReader(io.RawIOBase):
    def __init__(self, f, fail_after: int):
        self.f, self.left = f, fail_after

    def read(self, n=-1):
        if self.left <= 0:
            raise socket.timeout("simulated connection drop")
        data = self.f.read(min(n, self.left) if n and n > 0 else self.left)
        self.left -= len(data)
        return data

    def close(self):
        self.f.close()


class LocalFS:
    def __init__(self, root: Path, fail_after_bytes: Optional[int] = None, fail_times: int = 0):
        self.root = Path(root)
        self.fail_after_bytes = fail_after_bytes
        self.fail_times = fail_times
        self.opened: list[tuple[str, int]] = []
        self.closed = False

    def _p(self, path: str) -> Path:
        return self.root / path.lstrip("/")

    def listdir(self, path: str) -> list[RemoteEntry]:
        p = self._p(path)
        if not p.is_dir():
            raise RemotePathError(f"Not found on server: {path}")
        return [self._entry(c) for c in p.iterdir()]

    def _entry(self, c: Path) -> RemoteEntry:
        st = c.stat()
        return RemoteEntry(c.name, c.is_dir(), 0 if c.is_dir() else st.st_size, int(st.st_mtime))

    def stat(self, path: str) -> RemoteEntry:
        p = self._p(path)
        if not p.exists():
            raise RemotePathError(f"Not found on server: {path}")
        return self._entry(p)

    def open_read(self, path: str, offset: int = 0, prefetch_size=None):
        p = self._p(path)
        f = open(p, "rb")
        f.seek(offset)
        self.opened.append((path, offset))
        if self.fail_after_bytes is not None and self.fail_times > 0:
            self.fail_times -= 1
            return _FailingReader(f, self.fail_after_bytes)
        return f

    def remote_hash(self, path: str):
        return None

    def close(self):
        self.closed = True


class DeadFS:
    """Every operation raises a network error."""
    def listdir(self, path):
        raise ConnectionFailed("no connection")
    stat = open_read = listdir

    def remote_hash(self, path):
        return None

    def close(self):
        pass

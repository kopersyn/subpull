"""Download queue: parallel transfers, progress, resume and verification.

Data-safety rules:
* a file is downloaded to ``<name>.subpull-part`` and renamed to its original name
  only after verification,
* an existing destination file is overwritten only when ``job.overwrite`` is
  set (an explicit user decision); otherwise the job is skipped,
* an interrupted transfer keeps its ``.subpull-part`` and the next download resumes
  from there, provided the remote file has not changed.
"""
from __future__ import annotations

import hashlib
import itertools
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Optional

from . import config
from .connection import AuthFailed, ConnectionFailed, HostKeyError, RemoteFS, RemotePathError, is_network_error
from .history import History
from .local_state import part_path
from .scanner import FrameFile

log = logging.getLogger(__name__)

QUEUED, ACTIVE, VERIFYING, DONE, SKIPPED, ERROR, CANCELLED = (
    "queued", "downloading", "verifying", "done", "skipped", "error", "cancelled")
FINAL_STATES = {DONE, SKIPPED, ERROR, CANCELLED}

_ids = itertools.count(1)


@dataclass
class TransferJob:
    file: FrameFile
    dest: Path
    overwrite: bool = False
    id: int = field(default_factory=lambda: next(_ids))
    state: str = QUEUED
    total: int = 0
    done: int = 0
    resumed_from: int = 0
    speed: float = 0.0
    message: str = ""
    verified: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0

    def __post_init__(self):
        self.total = self.total or self.file.size


class _Cancel(Exception):
    pass


class DownloadManager:
    def __init__(self, connect: Callable[[], RemoteFS], history: History,
                 on_event: Callable[[str, object], None], workers: int = 3,
                 retries: int = config.TRANSFER_RETRIES, backoff: tuple = config.RETRY_BACKOFF):
        self.connect = connect
        self.history = history
        self.on_event = on_event
        self.workers = workers
        self.retries = retries
        self.backoff = backoff
        self.jobs: dict[int, TransferJob] = {}
        self._q: "queue.Queue[TransferJob]" = queue.Queue()
        self._stop_all = threading.Event()
        self._job_cancel: set[int] = set()
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._fatal: Optional[Exception] = None
        self._active = 0

    # ------------------------------------------------------------------ API
    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def submit(self, jobs: list[TransferJob]) -> None:
        self._stop_all.clear()
        self._fatal = None
        for j in jobs:
            self.jobs[j.id] = j
            self._q.put(j)
            self._emit(j)
        self._threads = [t for t in self._threads if t.is_alive()]
        for _ in range(max(0, min(self.workers, self._q.qsize()) - len(self._threads))):
            t = threading.Thread(target=self._worker, name=f"subpull-dl-{len(self._threads) + 1}", daemon=True)
            with self._lock:
                self._active += 1
            self._threads.append(t)
            t.start()

    def cancel_all(self) -> None:
        self._stop_all.set()

    def cancel_job(self, job_id: int) -> None:
        with self._lock:
            self._job_cancel.add(job_id)

    def wait(self, timeout: Optional[float] = None) -> None:
        end = None if timeout is None else time.time() + timeout
        for t in list(self._threads):
            t.join(None if end is None else max(0, end - time.time()))

    # ------------------------------------------------------------------ internals
    def _emit(self, job: TransferJob) -> None:
        self.on_event("job", replace(job))

    def _cancelled(self, job: TransferJob) -> bool:
        return self._stop_all.is_set() or job.id in self._job_cancel

    def _worker(self) -> None:
        fs: Optional[RemoteFS] = None
        try:
            while True:
                try:
                    job = self._q.get_nowait()
                except queue.Empty:
                    break
                if self._cancelled(job) or self._fatal is not None:
                    job.state, job.message = CANCELLED, (str(self._fatal) if self._fatal else "cancelled before start")
                    self._emit(job)
                    continue
                fs = self._run_job(job, fs)
        finally:
            if fs is not None:
                try:
                    fs.close()
                except Exception:
                    pass
            with self._lock:
                self._active -= 1
                last = self._active == 0
            if last:
                self.on_event("idle", None)

    def _run_job(self, job: TransferJob, fs: Optional[RemoteFS]) -> Optional[RemoteFS]:
        job.started_at = time.time()
        attempt = 0
        while True:
            try:
                if fs is None:
                    fs = self.connect()
                self._transfer(job, fs)
                return fs
            except _Cancel:
                job.state, job.speed = CANCELLED, 0
                job.message = "cancelled – .subpull-part kept for resume" if job.done else "cancelled"
                self._emit(job)
                return fs
            except (AuthFailed, HostKeyError) as e:
                self._fatal = e
                job.state, job.message = ERROR, str(e)
                self._emit(job)
                return None
            except RemotePathError as e:
                job.state, job.message = ERROR, str(e)
                self._emit(job)
                return fs
            except Exception as e:
                if is_network_error(e) and attempt < self.retries and not self._cancelled(job):
                    delay = self.backoff[min(attempt, len(self.backoff) - 1)]
                    attempt += 1
                    log.warning("%s: %s – retry %d/%d in %ss", job.file.name, e, attempt, self.retries, delay)
                    job.state, job.message, job.speed = QUEUED, f"connection lost, retry {attempt}/{self.retries}…", 0
                    self._emit(job)
                    if fs is not None:
                        try:
                            fs.close()
                        except Exception:
                            pass
                    fs = None
                    self._stop_all.wait(delay)
                    continue
                if is_network_error(e):
                    log.error("Download failed %s: %s", job.file.remote_path, e)
                else:
                    log.exception("Download failed %s", job.file.remote_path)
                job.state, job.message, job.speed = ERROR, str(e), 0
                self._emit(job)
                return None if is_network_error(e) else fs

    def _transfer(self, job: TransferJob, fs: RemoteFS) -> None:
        f = job.file
        dest = job.dest
        part = part_path(dest)
        if self._cancelled(job):
            raise _Cancel()

        if dest.exists() and not job.overwrite:
            job.state, job.message = SKIPPED, "already exists locally – not overwritten"
            self._emit(job)
            return

        st = fs.stat(f.remote_path)
        size, mtime = st.size, st.mtime
        job.total = size
        dest.parent.mkdir(parents=True, exist_ok=True)

        # --- resume -------------------------------------------------------------
        offset = 0
        if part.exists():
            rec = self.history.get_partial(str(part))
            psize = part.stat().st_size
            if rec and rec["remote_path"] == f.remote_path and rec["remote_size"] == size \
                    and rec["remote_mtime"] == mtime and psize <= size:
                offset = psize
            else:
                log.info("Partial file %s does not match the server file – starting over", part)
        self.history.set_partial(str(part), f.remote_path, size, mtime)
        job.resumed_from = offset
        job.done = offset
        job.state, job.message = ACTIVE, (f"resumed at {offset} B" if offset else "")
        self._emit(job)

        hasher = hashlib.sha256() if offset == 0 else None
        last_emit, win_t, win_b = 0.0, time.time(), offset
        rf = fs.open_read(f.remote_path, offset=offset, prefetch_size=size if size > offset else None)
        try:
            with open(part, "r+b" if offset else "wb") as out:
                if offset:
                    out.seek(offset)
                    out.truncate()
                while job.done < size:
                    if self._cancelled(job):
                        raise _Cancel()
                    chunk = rf.read(min(config.CHUNK_SIZE, size - job.done))
                    if not chunk:
                        raise ConnectionFailed("Server stopped sending before the end of the file")
                    out.write(chunk)
                    if hasher:
                        hasher.update(chunk)
                    job.done += len(chunk)
                    now = time.time()
                    if now - win_t >= 0.5:
                        job.speed = (job.done - win_b) / (now - win_t)
                        win_t, win_b = now, job.done
                    if now - last_emit >= 0.2:
                        last_emit = now
                        self._emit(job)
                out.flush()
                os.fsync(out.fileno())
        finally:
            try:
                rf.close()
            except Exception:
                pass

        # --- verification ---------------------------------------------------------
        job.state, job.speed = VERIFYING, 0
        self._emit(job)
        local_size = part.stat().st_size
        if local_size != size:
            raise ConnectionFailed(f"Size mismatch after download ({local_size} ≠ {size})")
        st2 = fs.stat(f.remote_path)
        if (st2.size, st2.mtime) != (size, mtime):
            part.unlink(missing_ok=True)
            self.history.clear_partial(str(part))
            raise RemotePathError("The server file changed during download – download it again")
        if hasher is None:
            hasher = hashlib.sha256()
            with open(part, "rb") as fh:
                for block in iter(lambda: fh.read(config.CHUNK_SIZE), b""):
                    hasher.update(block)
        local_sha = hasher.hexdigest()
        verified = "size"
        remote = fs.remote_hash(f.remote_path)
        if remote:
            algo, digest = remote
            if algo == "sha256" and digest.lower() != local_sha:
                part.unlink(missing_ok=True)
                self.history.clear_partial(str(part))
                raise RemotePathError("SHA-256 does not match the server – partial file removed, download again")
            verified = "size + server sha256"

        # --- finalize (never a silent overwrite) ------------------------------------
        if dest.exists():
            if not job.overwrite:
                job.state, job.message = SKIPPED, "file appeared locally meanwhile – .subpull-part kept"
                self._emit(job)
                return
            os.replace(part, dest)
        else:
            os.rename(part, dest)
        try:
            os.utime(dest, (mtime, mtime))
        except OSError:
            pass
        self.history.clear_partial(str(part))
        self.history.record_download(
            object=f.object_name, session_date=f.session_date, filename=f.name, frame_type=f.frame_type,
            remote_path=f.remote_path, local_path=str(dest), size=size, remote_mtime=mtime,
            sha256=local_sha, verified=verified)
        job.state, job.verified, job.finished_at = DONE, verified, time.time()
        job.message = ("overwritten with your consent; " if job.overwrite else "") + f"verified: {verified}"
        self._emit(job)

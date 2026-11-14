"""Application controller: the single bridge between the Qt views and SubPull's core modules.

It owns the state (settings, site connection, scan result, selection, transfers), runs every
network operation in a background thread and reports through signals. Nothing here scans or
downloads on its own – every operation starts from an explicit user action in the UI.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from PySide6.QtCore import QObject, Signal

from .. import config
from ..config import Settings
from ..connection import ConnectionFailed, SFTPConnection
from ..downloader import CANCELLED, DONE, ERROR, FINAL_STATES, SKIPPED, DownloadManager, TransferJob
from ..filezilla import ProfileError, SiteProfile, list_sites, load_site
from ..history import History
from ..i18n import t
from ..local_state import LocalStatus
from ..scanner import SCHEME_A0, ScanResult, Scanner, finalize, read_headers
from ..workflow import (DownloadPlan, QueueStats, Status, build_plan, compute_statuses, fmt_size, make_jobs,
                        object_summaries, scan_summary)
from . import language
from .workers import TaskThread

log = logging.getLogger(__name__)


class Controller(QObject):
    profileChanged = Signal()
    connectionState = Signal(str, str)          # idle | busy | ok | error, text
    scanRunning = Signal(bool)
    scanProgress = Signal(str)
    scanFinished = Signal()
    errorRaised = Signal(object, object)        # exception, retry callable (or None)
    statusesChanged = Signal()
    pathsChanged = Signal(object)               # set of remote paths whose status changed
    selectionChanged = Signal(bool)             # True when changed by a bulk action (views must refresh)
    jobUpdated = Signal(object)                 # TransferJob snapshot
    queueIdle = Signal()
    historyChanged = Signal()
    destinationChanged = Signal()
    headersBusy = Signal(bool)
    settingsChanged = Signal()
    languageChanged = Signal()                  # the interface language changed – views re-apply their texts
    _dl = Signal(str, object)

    def __init__(self, settings: Optional[Settings] = None, history: Optional[History] = None, parent=None):
        super().__init__(parent)
        self.settings = settings or Settings.load()
        self.history = history or History()
        self.sites: list[str] = []
        self.conn: Optional[SFTPConnection] = None
        self.profile: Optional[SiteProfile] = None
        self.profile_error: Optional[str] = None
        self._conn: tuple = ("idle", "", None, {})  # state, literal text, translation key, parameters
        self.result: Optional[ScanResult] = None
        self.scan_elapsed = 0.0
        self.statuses: dict[str, Status] = {}
        self.selected: set[str] = set()
        self.files_by_path: dict = {}
        self.jobs: dict[int, TransferJob] = {}
        self.batch_ids: set[int] = set()
        self._threads: set[TaskThread] = set()
        self._scan_cancel = threading.Event()
        self._scanning = False
        self.manager = DownloadManager(lambda: self._require_conn().connect(), self.history,
                                       lambda kind, job: self._dl.emit(kind, job),
                                       workers=max(1, int(self.settings.parallel_transfers)))
        self._dl.connect(self._on_download_event)

    # ================================================================== profile / settings
    def reload_sites(self) -> None:
        self.sites = list_sites()
        name = self.settings.profile_name
        if name not in self.sites and self.sites:
            if name:
                log.warning("Site '%s' not found in FileZilla – using '%s'", name, self.sites[0])
            name = self.sites[0]
        self.select_site(name, force=True)

    def select_site(self, name: str, force: bool = False) -> None:
        if not force and name == self.settings.profile_name and self.conn is not None:
            return
        self.settings.profile_name = name
        self.settings.save()
        self.conn, self.profile, self.profile_error = None, None, None
        try:
            if not name:
                raise ProfileError(t("error.site.none"))
            self.profile = load_site(name)
            self.conn = SFTPConnection(self.profile)
            log.info("Using FileZilla site '%s': %s (%s)", name, self.conn.label, self.profile.auth_label)
        except (ProfileError, ConnectionFailed) as e:
            self.profile_error = str(e)
            log.error("Site profile: %s", e)
        self._set_conn("idle" if self.conn else "error", "" if self.conn else self.profile_error)
        self.profileChanged.emit()

    def _require_conn(self):
        if self.conn is None:
            raise ConnectionFailed(self.profile_error or t("error.site.no_profile"))
        return self.conn

    def _set_conn(self, state: str, text: str = "", key: Optional[str] = None, **params) -> None:
        """Connection state with either a literal ``text`` or a translation ``key`` (re-rendered
        when the language changes)."""
        self._conn = (state, text, key, params)
        self.connectionState.emit(*self.conn_state)

    @property
    def conn_state(self) -> tuple[str, str]:
        state, text, key, params = self._conn
        return state, (t(key, **params) if key else text)

    def set_language(self, code: str) -> None:
        """Switch the interface language at runtime ("auto" = system language) and remember it."""
        if code != self.settings.language:
            self.settings.language = code
            self.settings.save()
        self.apply_language()

    def apply_language(self) -> None:
        language.apply(self.settings.language)
        self.recompute_statuses(emit=False)      # status details are texts too
        self.languageChanged.emit()
        self.connectionState.emit(*self.conn_state)

    @property
    def dest_root(self) -> Optional[Path]:
        return self.settings.dest_path

    def set_destination(self, path: str) -> None:
        self.settings.dest_root = str(Path(path))
        self.settings.save()
        log.info("Destination folder: %s", self.settings.dest_root)
        self.recompute_statuses()
        self.destinationChanged.emit()

    def update_settings(self, **changes) -> None:
        for k, v in changes.items():
            setattr(self.settings, k, v)
        self.settings.save()
        if "parallel_transfers" in changes:
            self.manager.workers = max(1, int(self.settings.parallel_transfers))
        self.settingsChanged.emit()

    # ================================================================== threads
    def _run(self, fn, on_ok: Callable, on_fail: Callable, on_progress: Optional[Callable] = None) -> TaskThread:
        t = TaskThread(fn, self)
        t.succeeded.connect(on_ok)
        t.failed.connect(on_fail)
        if on_progress:
            t.progress.connect(on_progress)
        t.finished.connect(lambda: self._threads.discard(t))
        t.finished.connect(t.deleteLater)
        self._threads.add(t)
        t.start()
        return t

    def _fail(self, e: Exception, retry: Optional[Callable]) -> None:
        self._set_conn("error", str(e)[:160])
        self.errorRaised.emit(e, retry)

    # ================================================================== connection test
    def test_connection(self) -> None:
        conn = self.conn
        if conn is None:
            self._fail(ConnectionFailed(self.profile_error or t("error.site.no_profile")), None)
            return
        src = self.settings.source_dir or "/"
        self._set_conn("busy", key="connection.busy")

        def work(progress):
            t0 = time.time()
            fs = conn.connect()
            try:
                fs.listdir(src)
            finally:
                fs.close()
            return time.time() - t0

        self._run(work, lambda dt: self._set_conn("ok", key="connection.connected_ping", ms=f"{dt * 1000:.0f}"),
                  lambda e: self._fail(e, self.test_connection))

    # ================================================================== scanning
    @property
    def scanning(self) -> bool:
        return self._scanning

    def start_scan(self, source_dir: Optional[str] = None) -> None:
        if self._scanning:
            return
        conn = self.conn
        if conn is None:
            self._fail(ConnectionFailed(self.profile_error or t("error.site.no_profile")), None)
            return
        src = (source_dir if source_dir is not None else self.settings.source_dir).strip() or "/"
        if src != self.settings.source_dir:
            self.update_settings(source_dir=src)
        read_all = bool(self.settings.read_fits)
        placeholders = self.settings.placeholders
        cancel = self._scan_cancel
        cancel.clear()
        self._scanning = True
        self.scanRunning.emit(True)
        self._set_conn("busy", key="connection.connecting_to", server=conn.label)

        def work(progress):
            t0 = time.time()
            fs = conn.connect_with_retry(cancel=cancel, on_retry=lambda n, e: progress(t("connection.retrying", attempt=n)))
            self.connectionState.emit("ok", t("connection.connected_scanning", folder=src))
            try:
                res = Scanner(fs, src, progress=progress, cancel=cancel).scan()
            finally:
                fs.close()
            if not res.cancelled:
                targets = [f for f in res.files if read_all or f.scheme == SCHEME_A0]
                if targets:
                    progress(t("scan.progress.headers", done=0, total=len(targets)))
                    read_headers(targets, conn.connect_with_retry, cancel=cancel, placeholders=placeholders,
                                 progress=lambda n, total: progress(t("scan.progress.headers", done=n, total=total))
                                 if n % 10 == 0 or n == total else None)
            finalize(res)
            res.finished = time.time()
            return res, time.time() - t0

        def ok(payload):
            res, elapsed = payload
            self._scanning = False
            self.scanRunning.emit(False)
            self._set_conn("ok", key="connection.connected_scan_finished", time=time.strftime("%H:%M"))
            self._apply_scan(res, elapsed)

        def fail(e):
            self._scanning = False
            self.scanRunning.emit(False)
            log.error("Scan failed: %s", e)
            self._fail(e, lambda: self.start_scan(src))

        self._run(work, ok, fail, self.scanProgress.emit)

    def cancel_scan(self) -> None:
        self._scan_cancel.set()

    def _apply_scan(self, res: ScanResult, elapsed: float) -> None:
        self.result = res
        self.scan_elapsed = elapsed
        self.files_by_path = {f.remote_path: f for f in res.files}
        self.selected.intersection_update(self.files_by_path)
        self.recompute_statuses(emit=False)
        summary = scan_summary(res, elapsed)
        self.history.log_operation("scan", f"{res.root}: {summary}")
        log.info("Scan %s: %s", res.root, summary)
        for err in res.errors[:20]:
            log.warning("Scan: %s", err)
        self.scanFinished.emit()
        self.historyChanged.emit()

    def recompute_statuses(self, emit: bool = True) -> None:
        if self.result is None:
            return
        self.statuses = compute_statuses(self.result.files, self.history.downloaded_remote_paths(), self.dest_root)
        if emit:
            self.statusesChanged.emit()

    def object_summaries(self):
        return object_summaries(self.result, self.statuses) if self.result else []

    def last_scan(self):
        return self.history.last_operation("scan")

    # ================================================================== FITS headers on demand
    def read_headers_for(self, paths: Iterable[str]) -> None:
        targets = [self.files_by_path[p] for p in paths if p in self.files_by_path and self.files_by_path[p].fits is None]
        if not targets:
            return
        conn = self._require_conn()
        placeholders = self.settings.placeholders
        self.headersBusy.emit(True)

        def work(progress):
            return read_headers(targets, conn.connect_with_retry, placeholders=placeholders,
                                progress=lambda a, b: progress(t("scan.progress.headers", done=a, total=b)))

        def ok(n):
            self.headersBusy.emit(False)
            log.info("Read %d FITS headers", n)
            self.pathsChanged.emit({f.remote_path for f in targets})

        def fail(e):
            self.headersBusy.emit(False)
            self._fail(e, lambda: self.read_headers_for([f.remote_path for f in targets]))

        self._run(work, ok, fail, self.scanProgress.emit)

    # ================================================================== selection
    def set_selected(self, paths: Iterable[str], value: bool) -> None:
        for p in paths:
            if p in self.files_by_path:
                (self.selected.add if value else self.selected.discard)(p)
        self.selectionChanged.emit(True)

    def select_all(self, value: bool) -> None:
        self.selected = set(self.files_by_path) if value else set()
        self.selectionChanged.emit(True)

    def selection_summary(self) -> tuple[int, int]:
        files = [self.files_by_path[p] for p in self.selected if p in self.files_by_path]
        return len(files), sum(f.size for f in files)

    # ================================================================== downloads
    def active_paths(self) -> set[str]:
        return {j.file.remote_path for j in self.jobs.values() if j.state not in FINAL_STATES}

    def plan(self, include: Iterable[LocalStatus] = ()) -> DownloadPlan:
        files = [self.files_by_path[p] for p in self.selected if p in self.files_by_path]
        return build_plan(files, self.statuses, self.dest_root, include, self.active_paths())

    def start_download(self, files, overwrite_states: Iterable[LocalStatus] = ()) -> int:
        """Queue the given files. Only call after the user confirmed the plan."""
        if self.dest_root is None:
            raise ValueError("Destination folder not set")
        jobs, errors = make_jobs(files, self.statuses, self.dest_root, overwrite_states)
        for e in errors:
            log.error("%s", e)
        if not jobs:
            return 0
        if all(j.state in FINAL_STATES for j in self.jobs.values()):
            self.batch_ids = set()
        for j in jobs:
            self.jobs[j.id] = j
            self.batch_ids.add(j.id)
        total = sum(j.total for j in jobs)
        self.history.log_operation("download", t("history.summary.queued", count=len(jobs), size=fmt_size(total)))
        log.info("Download started: %d files (%s)", len(jobs), fmt_size(total))
        self.manager.submit(jobs)
        self.historyChanged.emit()
        return len(jobs)

    def stop_all(self) -> None:
        self.manager.cancel_all()
        log.info("Stopping all transfers – partial files are kept for resume")

    def cancel_jobs(self, ids: Iterable[int]) -> None:
        for i in ids:
            self.manager.cancel_job(i)

    def retry_failed(self) -> int:
        failed = [j for j in self.jobs.values() if j.state in (ERROR, CANCELLED)]
        if not failed:
            return 0
        for j in failed:
            self.jobs.pop(j.id, None)
            self.batch_ids.discard(j.id)
        self.recompute_statuses()
        over = {LocalStatus.PRESENT, LocalStatus.CONFLICT} if any(j.overwrite for j in failed) else set()
        return self.start_download([j.file for j in failed], over)

    def clear_finished(self) -> set[int]:
        ids = {j.id for j in self.jobs.values() if j.state in FINAL_STATES}
        for i in ids:
            self.jobs.pop(i, None)
            self.batch_ids.discard(i)
        return ids

    def queue_stats(self) -> QueueStats:
        return QueueStats.from_jobs(self.jobs[i] for i in self.batch_ids if i in self.jobs)

    @property
    def downloading(self) -> bool:
        return self.manager.running

    def _on_download_event(self, kind: str, job) -> None:
        if kind == "idle":
            batch = [self.jobs[i] for i in self.batch_ids if i in self.jobs]
            count = lambda s: sum(1 for j in batch if j.state == s)
            msg = t("history.summary.finished", done=count(DONE), skipped=count(SKIPPED),
                    cancelled=count(CANCELLED), errors=count(ERROR))
            self.history.log_operation("download", msg)
            log.info("Download %s", msg)
            self.queueIdle.emit()
            self.historyChanged.emit()
            return
        prev = self.jobs.get(job.id)
        self.jobs[job.id] = job
        if job.state in FINAL_STATES and (prev is None or prev.state != job.state):
            if job.state == ERROR:
                self.history.log_operation("error", f"{job.file.object_name}/{job.file.name}: {job.message}")
            if self.result is not None and job.file.remote_path in self.files_by_path and self.dest_root:
                from .. import local_state
                hist = self.history.downloaded_remote_paths() if job.state == DONE else {}
                self.statuses[job.file.remote_path] = local_state.local_status(job.file, hist, set(), self.dest_root)
                self.pathsChanged.emit({job.file.remote_path})
            if job.state in (DONE, ERROR):
                self.historyChanged.emit()
        self.jobUpdated.emit(job)

    # ================================================================== shutdown
    def shutdown(self, wait: float = 5.0) -> None:
        self._scan_cancel.set()
        if self.manager.running:
            self.manager.cancel_all()
            self.manager.wait(wait)
        for th in list(self._threads):
            th.wait(int(wait * 1000))
        self.history.close()

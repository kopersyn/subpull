"""Qt UI integration tests. They run headless (offscreen platform) against a local fake server."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEventLoop, Qt, QTimer
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover – PySide6 is a regular dependency
    QApplication = None

from subpull import config, local_state
from subpull.config import Settings
from subpull.connection import ConnectionFailed
from subpull.filezilla import SiteProfile
from subpull.history import History
from subpull.local_state import LocalStatus
from tests.fakes import LocalFS, fits_bytes

APP = None


def app():
    global APP
    if APP is None:
        APP = QApplication.instance() or QApplication([])
    return APP


def write(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


class FakeConnection:
    """Stands in for SFTPConnection: sessions read a local directory."""

    def __init__(self, root: Path, fail: bool = False, delay: float = 0.0):
        self.root, self.fail, self.delay = root, fail, delay
        self.profile = SiteProfile("Test", "localhost", 22, "sftp", "tester", 5, None)
        self.label = "tester@localhost:22"

    def connect(self):
        if self.fail:
            raise ConnectionFailed("server unreachable")
        fs = LocalFS(self.root)
        if self.delay:                                  # emulate network latency per directory listing
            listdir = fs.listdir
            fs.listdir = lambda path: (time.sleep(self.delay), listdir(path))[1]
        return fs

    def connect_with_retry(self, retries=1, cancel=None, on_retry=None):
        return self.connect()

    def accept_hostkey_for_session(self, blob):
        pass


def wait_for(signal, timeout_ms=20000) -> bool:
    loop = QEventLoop()
    fired = []
    signal.connect(lambda *a: (fired.append(a), loop.quit()))
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    return bool(fired)


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class UITests(unittest.TestCase):
    def setUp(self):
        app()
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self._old_app_dir = config.APP_DIR
        config.APP_DIR = self.tmp / "appdata"            # ui.ini etc. for MainWindow
        config.APP_DIR.mkdir()
        self.server = self.tmp / "server"
        self.dest = self.tmp / "library"
        s = self.server / "frames"
        write(s / "2026-01-01" / "M31" / "LIGHT" / "a.fit", fits_bytes("M31", data_blocks=30))
        write(s / "2026-01-01" / "M31" / "FLAT" / "f.fit", fits_bytes("M31", "FLAT", data_blocks=5))
        write(s / "2026-01-02" / "M42" / "LIGHT_001.fit", fits_bytes("M42", data_blocks=5))
        write(s / "2026-01-02" / "M42" / "DARK_001.fit", fits_bytes("M42", "DARK", data_blocks=5))
        write(s / "2026-01-03" / "LIGHT" / "old.fits", fits_bytes("NGC 7000", data_blocks=5))       # scheme A0
        write(s / "2026-01-03" / "LIGHT" / "anon.fits", fits_bytes("Target", data_blocks=5))       # unrecognized
        from subpull.ui.controller import Controller
        settings = Settings(dest_root=str(self.dest), source_dir="/frames", profile_name="Test")
        settings._path = self.tmp / "settings.json"
        self.history = History(self.tmp / "h.db")
        self.ctl = Controller(settings, self.history)
        self.ctl.sites = ["Test"]
        self.ctl.conn = FakeConnection(self.server)
        self.ctl.profile = self.ctl.conn.profile

    def tearDown(self):
        self.ctl.shutdown()
        config.APP_DIR = self._old_app_dir
        self._td.cleanup()

    def scan(self):
        ticks = [0]
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.__setitem__(0, ticks[0] + 1))
        timer.start(5)
        self.ctl.start_scan()
        self.assertTrue(wait_for(self.ctl.scanFinished))
        timer.stop()
        return ticks[0]

    # ------------------------------------------------------------------ controller
    def test_scan_runs_in_background_and_recognizes_layouts(self):
        self.ctl.conn = FakeConnection(self.server, delay=0.05)   # scan takes ~0.5 s, timer ticks every 5 ms
        ticks = self.scan()
        self.assertGreater(ticks, 10, "GUI event loop must keep running during a scan")
        names = {f.name: f for f in self.ctl.result.files}
        self.assertEqual(set(names), {"a.fit", "f.fit", "LIGHT_001.fit", "DARK_001.fit", "old.fits"})
        self.assertEqual((names["old.fits"].object_name, names["old.fits"].object_source), ("NGC 7000", "FITS"))
        self.assertEqual(len(self.ctl.result.unrecognized), 1)
        self.assertTrue(all(s[0] == LocalStatus.NEW for s in self.ctl.statuses.values()))

    def test_download_via_controller(self):
        self.scan()
        self.ctl.select_all(True)
        plan = self.ctl.plan()
        self.assertEqual(len(plan.todo), 5)
        self.ctl.start_download(plan.todo)
        self.assertTrue(wait_for(self.ctl.queueIdle))
        got = sorted(str(p.relative_to(self.dest)).replace("\\", "/") for p in self.dest.rglob("*") if p.is_file())
        self.assertEqual(got, ["M31/subs/a.fit", "M31/subs/f.fit", "M42/subs/DARK_001.fit", "M42/subs/LIGHT_001.fit",
                               "NGC 7000/subs/old.fits"])
        self.assertEqual(len(self.history.list_downloads()), 5)
        self.assertTrue(all(self.ctl.statuses[p][0] == LocalStatus.PRESENT for p in self.ctl.selected))
        s = self.ctl.queue_stats()
        self.assertEqual((s.done, s.errors, s.running), (5, 0, False))

    def test_existing_file_is_not_overwritten_without_consent(self):
        self.scan()
        f = next(x for x in self.ctl.result.files if x.name == "a.fit")
        dest = local_state.dest_path("M31", "a.fit", self.dest)
        write(dest, b"MY LOCAL COPY")
        self.ctl.recompute_statuses()
        self.ctl.set_selected([f.remote_path], True)
        plan = self.ctl.plan()
        self.assertEqual(plan.todo, [])                                    # skipped by default
        plan = self.ctl.plan(include={LocalStatus.CONFLICT})
        self.assertEqual(plan.overwrite_count, 1)
        self.ctl.start_download(plan.todo, overwrite_states=set())         # included, but no overwrite consent
        self.assertTrue(wait_for(self.ctl.queueIdle))
        self.assertEqual(dest.read_bytes(), b"MY LOCAL COPY")
        self.assertEqual(next(iter(self.ctl.jobs.values())).state, "skipped")

    def test_connection_failure_is_reported(self):
        self.ctl.conn = FakeConnection(self.server, fail=True)
        errors = []
        self.ctl.errorRaised.connect(lambda e, retry: errors.append((e, retry)))
        self.ctl.start_scan()
        t0 = time.time()
        while not errors and time.time() - t0 < 10:
            app().processEvents()
        self.assertTrue(errors)
        self.assertIsInstance(errors[0][0], ConnectionFailed)
        self.assertIsNotNone(errors[0][1])                                 # retry offered
        self.assertFalse(self.ctl.scanning)
        self.assertEqual(self.ctl.conn_state[0], "error")

    # ------------------------------------------------------------------ models
    def test_frame_model_grouping_and_tristate(self):
        from subpull.ui.models import FrameTreeModel
        self.scan()
        m = FrameTreeModel(self.ctl.selected)
        m.set_files(self.ctl.result.files, self.ctl.statuses, "object")
        self.assertEqual([m.index(r, 0).data() for r in range(m.rowCount())], ["M31", "M42", "NGC 7000"])
        g = m.index(0, 0)
        self.assertEqual(m.rowCount(g), 2)
        child = m.index(0, 0, g)
        m.setData(child, Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)
        self.assertEqual(g.data(Qt.ItemDataRole.CheckStateRole), Qt.CheckState.PartiallyChecked)
        m.setData(g, Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)
        self.assertEqual(g.data(Qt.ItemDataRole.CheckStateRole), Qt.CheckState.Checked)
        self.assertEqual(len(self.ctl.selected), 2)
        m.setData(g, Qt.CheckState.Unchecked.value, Qt.ItemDataRole.CheckStateRole)
        self.assertEqual(len(self.ctl.selected), 0)
        m.sort(FrameTreeModel.COL["size"], Qt.SortOrder.DescendingOrder)
        self.assertEqual(m.index(0, 0, m.index(0, 0)).data(), "a.fit")

    def test_main_window_pages_and_confirm_dialog(self):
        from subpull.ui.dialogs import ConfirmDownloadDialog
        from subpull.ui.main_window import MainWindow
        from subpull.ui.models import LogModel
        w = MainWindow(self.ctl, LogModel())
        w.resize(1400, 900)
        w.show()
        self.scan()
        lib = w.library
        self.assertEqual(lib.stack.currentIndex(), 1)
        self.assertEqual(lib.obj_model.rowCount(), 4)                       # all + 3 objects
        lib.obj_view.setCurrentIndex(lib.obj_model.index(lib.obj_model.row_of("M42")))
        app().processEvents()
        self.assertEqual({f.object_name for f in lib.model.visible_files()}, {"M42"})
        lib.chips[0].setChecked(False)                                     # hide LIGHT
        self.assertEqual([f.name for f in lib.model.visible_files()], ["DARK_001.fit"])
        lib._select_visible(True)
        self.assertEqual(len(self.ctl.selected), 1)
        for key in ("queue", "history", "logs", "settings", "library"):
            w.show_page(key)
            app().processEvents()
        dlg = ConfirmDownloadDialog(self.ctl, w)
        self.assertEqual(len(dlg.plan.todo), 1)
        self.assertTrue(dlg.btn_ok.isEnabled())
        dlg.reject()
        w.close()


if __name__ == "__main__":
    unittest.main()

"""Qt UI integration tests. They run headless (offscreen platform) against a local fake server."""
from __future__ import annotations

import gc
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


def flush_deletes() -> None:
    """Destroy Qt objects now, in a defined order, instead of at interpreter exit
    (late destruction during Python shutdown can crash Qt on Linux)."""
    from PySide6.QtCore import QCoreApplication, QEvent
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QCoreApplication.processEvents()
    gc.collect()


def tearDownModule():
    if APP is not None:
        flush_deletes()


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
        from subpull import i18n
        from subpull.ui import language
        i18n.reload()
        language.apply("en")
        self.ctl.shutdown()
        self.ctl.deleteLater()
        flush_deletes()
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
        dlg.deleteLater()
        w.deleteLater()
        flush_deletes()

    # ------------------------------------------------------------------ localization
    def pick_language(self, w, code: str) -> None:
        box = w.settings.language
        box.setCurrentIndex(box.findData(code))
        box.activated.emit(box.currentIndex())
        app().processEvents()

    def test_language_switch_retranslates_existing_widgets(self):
        from subpull import i18n
        from subpull.ui.main_window import MainWindow
        from subpull.ui.models import LogModel
        w = MainWindow(self.ctl, LogModel())
        w.resize(1400, 900)
        w.show()
        self.scan()
        lib = w.library
        title, model, sidebar_btn = lib.title, lib.model, w.sidebar.buttons["queue"]
        header = lambda m, i: m.headerData(i, Qt.Orientation.Horizontal)
        self.assertEqual((title.text(), header(model, 0), sidebar_btn.text()), ("Library", "File", "Transfers"))
        box = w.settings.language
        self.assertEqual([box.itemData(i) for i in range(box.count())][:2], ["auto", "en"])
        self.assertIn("Polski", box.itemText(box.findData("pl")))
        lib._select_visible(True)
        selected = set(self.ctl.selected)

        self.pick_language(w, "pl")
        self.assertEqual(i18n.language(), "pl")
        self.assertEqual(Settings.load(self.tmp / "settings.json").language, "pl")          # persisted
        self.assertIs(lib.title, title)                                                   # same widgets, new texts
        self.assertIs(lib.model, model)
        self.assertEqual((title.text(), header(model, 0), sidebar_btn.text()), ("Biblioteka", "Plik", "Transfery"))
        self.assertEqual(w.topbar.scan_btn.text(), "Skanuj")
        self.assertEqual(w.topbar.scan_btn.toolTip(), "Skanuj folder zdalny (F5). Nic nie jest pobierane.")
        self.assertEqual(lib.btn_unrec.text(), "1 nierozpoznany")
        self.assertEqual(lib.btn_download.text(), f"Pobierz {len(selected)} plików")
        self.assertEqual(lib.btn_select.menu().actions()[0].text(), "Zaznacz wszystkie widoczne")
        self.assertEqual(lib.obj_model.index(0).data(), "Wszystkie obiekty")
        self.assertEqual(lib.session_cb.itemText(0), "Wszystkie sesje")
        self.assertIn("5 klatek", lib.subtitle.text())
        self.assertTrue(self.ctl.conn_state[1].startswith("Połączono"), self.ctl.conn_state)  # dynamic status follows
        self.assertEqual(w.sidebar.conn_label.text(), self.ctl.conn_state[1])
        self.assertEqual(header(w.queue.model, 2), "Stan")
        self.assertEqual(header(w.history.model, 0), "Pobrano")
        self.assertEqual(header(w.logs.model, 3), "Komunikat")
        self.assertEqual(w.logs.level.itemText(3), "Tylko błędy")
        self.assertEqual(w.windowTitle(), config.APP_NAME)
        self.assertEqual(set(self.ctl.selected), selected)                                # state is untouched
        self.assertEqual(len(lib.model.visible_files()), 5)

        self.pick_language(w, "en")
        self.assertEqual((title.text(), header(model, 0), sidebar_btn.text()), ("Library", "File", "Transfers"))
        self.assertEqual(Settings.load(self.tmp / "settings.json").language, "en")
        w.close()
        w.deleteLater()
        flush_deletes()

    def test_right_to_left_language_flips_the_layout(self):
        import json
        from subpull import i18n
        from subpull.i18n.locale_file import LOCALES_DIR
        locales = self.tmp / "locales"
        locales.mkdir()
        (locales / "en.json").write_bytes((LOCALES_DIR / "en.json").read_bytes())
        (locales / "ar.json").write_text(json.dumps({
            "_meta": {"code": "ar", "name": "العربية", "english_name": "Arabic", "direction": "rtl"},
            "translations": {"nav.library": "المكتبة"}}, ensure_ascii=False), encoding="utf-8")
        i18n.reload(locales)
        self.ctl.set_language("ar")
        self.assertEqual(app().layoutDirection(), Qt.LayoutDirection.RightToLeft)
        self.assertEqual(i18n.t("nav.library"), "المكتبة")
        self.assertEqual(i18n.t("nav.history"), "History")                                # partial → English fallback
        self.ctl.set_language("en")
        self.assertEqual(app().layoutDirection(), Qt.LayoutDirection.LeftToRight)

    def test_starts_in_a_translation_without_network(self):
        import socket
        import sys
        from unittest import mock
        from subpull.ui.main_window import MainWindow
        from subpull.ui.models import LogModel
        self.ctl.settings.language = "pl"
        offline = OSError("network is disabled in this test")
        with mock.patch.object(socket, "socket", side_effect=offline), \
                mock.patch.object(socket, "create_connection", side_effect=offline), \
                mock.patch.object(socket, "getaddrinfo", side_effect=offline):
            self.ctl.apply_language()
            w = MainWindow(self.ctl, LogModel())
            w.show()
            for key in ("queue", "history", "logs", "settings", "library"):
                w.show_page(key)
                app().processEvents()
            self.assertEqual(w.library.title.text(), "Biblioteka")
            self.assertEqual(w.queue.state_label.text(), "Bezczynny")
        self.assertNotIn("PySide6.QtNetwork", sys.modules)
        w.close()
        w.deleteLater()
        flush_deletes()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import base64
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from subpull import classify, local_state
from subpull.connection import ConnectionFailed, SFTPConnection, with_retry
from subpull.downloader import (CANCELLED, DONE, ERROR, SKIPPED, DownloadManager, TransferJob)
from subpull.fits_header import read_header
from subpull.history import History
from subpull.scanner import FITS_MISSING, SCHEME_A, SCHEME_A0, SCHEME_B, SRC_DIR, SRC_FITS, Scanner, finalize, read_headers
from subpull.filezilla import list_sites, load_site, load_trusted_hostkeys

from tests.fakes import DeadFS, LocalFS, fits_bytes


def write(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.server = self.tmp / "server"
        self.dest = self.tmp / "dest"
        self.history = History(self.tmp / "h.db")

    def tearDown(self):
        self.history.close()
        self._td.cleanup()


# ----------------------------------------------------------------------------- klasyfikacja
class ClassifyTests(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(classify.parse_session_date("2025-08-15"), "2025-08-15")
        self.assertEqual(classify.parse_session_date("20251015"), "2025-10-15")
        self.assertIsNone(classify.parse_session_date("2025-13-01"))
        self.assertIsNone(classify.parse_session_date("master"))
        self.assertIsNone(classify.parse_session_date("test"))

    def test_type_from_dir(self):
        for name, exp in [("LIGHT", "LIGHT"), ("lights", "LIGHT"), ("Dark", "DARK"), ("FLAT", "FLAT"),
                          ("flats", "FLAT"), ("biases", "BIAS"), ("BIAS", "BIAS"), ("DarkFlats", "DARKFLAT"),
                          ("M31", None), ("FlatWizard", None), ("process", None)]:
            self.assertEqual(classify.frame_type_from_dirname(name), exp, name)

    def test_type_from_prefix(self):
        for name, exp in [("LIGHT_001.fit", "LIGHT"), ("DARK_001.fit", "DARK"), ("FLAT_001.fit", "FLAT"),
                          ("BIAS_001.fit", "BIAS"), ("DARKFLAT_001.fit", "DARKFLAT"), ("light_x.fits", "LIGHT"),
                          ("2026-09-02_21-21-22_B_300.00s_0000.fits", None), ("LIGHTS.fit", None)]:
            self.assertEqual(classify.frame_type_from_filename(name), exp, name)

    def test_imagetyp(self):
        self.assertEqual(classify.frame_type_from_imagetyp("Light Frame"), "LIGHT")
        self.assertEqual(classify.frame_type_from_imagetyp("DARKFLAT"), "DARKFLAT")
        self.assertEqual(classify.frame_type_from_imagetyp("Bias Frame"), "BIAS")
        self.assertIsNone(classify.frame_type_from_imagetyp(None))

    def test_frame_file(self):
        self.assertTrue(classify.is_frame_file("x.fits"))
        self.assertTrue(classify.is_frame_file("x.FIT"))
        self.assertTrue(classify.is_frame_file("x.fits.fz"))
        self.assertFalse(classify.is_frame_file("guide.txt"))
        self.assertFalse(classify.is_frame_file("x.fits.subpull-part"))


# ----------------------------------------------------------------------------- FITS
class FitsTests(unittest.TestCase):
    def test_parse(self):
        import io
        h = read_header(io.BytesIO(fits_bytes("NGC 7000", "FLAT", "Oiii")))
        self.assertEqual(h["OBJECT"], "NGC 7000")
        self.assertEqual(h["IMAGETYP"], "FLAT")
        self.assertEqual(h["FILTER"], "Oiii")
        self.assertEqual(h["NAXIS"], 2)

    def test_fpack_extension(self):
        import io
        prim = b"".join(c.ljust(80).encode() for c in
                        ["SIMPLE  =                    T", "BITPIX  =                    8",
                         "NAXIS   =                    0", "EXTEND  =                    T", "END"]).ljust(2880)
        ext = b"".join(c.ljust(80).encode() for c in
                       ["XTENSION= 'BINTABLE'", "ZIMAGE  =                    T", "OBJECT  = 'M 42'",
                        "IMAGETYP= 'LIGHT'", "END"]).ljust(2880)
        h = read_header(io.BytesIO(prim + ext + b"\0" * 2880))
        self.assertEqual(h["OBJECT"], "M 42")

    def test_not_fits(self):
        import io
        from subpull.fits_header import FitsHeaderError
        with self.assertRaises(FitsHeaderError):
            read_header(io.BytesIO(b"\x00" * 3000))


# ----------------------------------------------------------------------------- skanowanie
class ScanTests(TmpCase):
    def build_tree(self):
        s = self.server / "frames"
        # Scheme A
        write(s / "main" / "2025-08-15" / "M31" / "LIGHT" / "a1.fit", fits_bytes("M31", "LIGHT"))
        write(s / "main" / "2025-08-15" / "M31" / "LIGHT" / "a2.fit", fits_bytes("M31", "LIGHT"))
        write(s / "main" / "2025-08-15" / "M31" / "DARK" / "d1.fit", fits_bytes("M31", "DARK"))
        write(s / "main" / "2025-08-15" / "M31" / "FLAT" / "f1.fit", fits_bytes("M31", "FLAT"))
        write(s / "main" / "2025-08-15" / "M42" / "LIGHT" / "m42.fit", fits_bytes("M42", "LIGHT"))
        # Scheme B (other branch and date – both coexist)
        b = s / "observer" / "2026-10-15"
        for n in ["LIGHT_001.fit", "LIGHT_002.fit", "DARK_001.fit", "FLAT_001.fit", "BIAS_001.fit", "DARKFLAT_001.fit"]:
            write(b / "M31" / n, fits_bytes("M31", n.split("_")[0]))
        write(b / "M42" / "LIGHT_001.fit", fits_bytes("M42"))
        write(b / "M42" / "DARK_001.fit", fits_bytes("M42", "DARK"))
        write(b / "M42" / "notes.txt", b"x")
        # Scheme A0 – no object directory
        write(s / "observer" / "2025-04-26" / "LIGHT" / "2025-04-26_x.fits", fits_bytes("NGC 6992", "LIGHT"))
        write(s / "observer" / "2025-04-26" / "LIGHT" / "2025-04-26_y.fits", fits_bytes("Target", "LIGHT"))
        write(s / "observer" / "2025-04-29" / "biases" / "b.fits", fits_bytes("Target", "BIAS"))
        # non-date directory is ignored; a non-type subdirectory inside an object is unrecognized
        write(s / "observer" / "test" / "lights" / "t.fits", fits_bytes())
        write(s / "observer" / "2026-10-15" / "M31" / "process" / "p.fits", fits_bytes())
        # mixed object directory (both schemes in one)
        write(s / "main" / "2026-11-01" / "NGC7000" / "LIGHT" / "x.fit", fits_bytes("NGC7000"))
        write(s / "main" / "2026-11-01" / "NGC7000" / "FLAT_9.fit", fits_bytes("NGC7000", "FLAT"))
        return LocalFS(self.server)

    def scan(self, fs, headers=True):
        res = Scanner(fs, "/frames").scan()
        if headers:
            read_headers(res.files, lambda: fs, workers=2)
        finalize(res)
        return res

    def by_name(self, res):
        return {f.remote_path.split("/frames/")[1]: f for f in res.files}

    def test_scheme_a(self):
        res = self.scan(self.build_tree())
        f = self.by_name(res)["main/2025-08-15/M31/DARK/d1.fit"]
        self.assertEqual((f.scheme, f.object_name, f.frame_type, f.session_date), (SCHEME_A, "M31", "DARK", "2025-08-15"))
        self.assertEqual(f.type_source, SRC_DIR)

    def test_scheme_b(self):
        res = self.scan(self.build_tree())
        m = self.by_name(res)
        for n, t in [("LIGHT_001.fit", "LIGHT"), ("DARK_001.fit", "DARK"), ("FLAT_001.fit", "FLAT"),
                     ("BIAS_001.fit", "BIAS"), ("DARKFLAT_001.fit", "DARKFLAT")]:
            f = m[f"observer/2026-10-15/M31/{n}"]
            self.assertEqual((f.scheme, f.object_name, f.frame_type, f.session_date), (SCHEME_B, "M31", t, "2026-10-15"))
        self.assertNotIn("observer/2026-10-15/M42/notes.txt", m)

    def test_mixed_and_counts(self):
        res = self.scan(self.build_tree())
        m = self.by_name(res)
        self.assertEqual(m["main/2026-11-01/NGC7000/LIGHT/x.fit"].scheme, SCHEME_A)
        self.assertEqual(m["main/2026-11-01/NGC7000/FLAT_9.fit"].scheme, SCHEME_B)
        objs = res.objects
        self.assertEqual(len(objs["M31"]), 4 + 6)
        self.assertEqual(len(objs["M42"]), 1 + 2)
        self.assertNotIn("observer/test/lights/t.fits", m)  # no date directory

    def test_object_from_dir_not_filename(self):
        res = self.scan(self.build_tree(), headers=False)
        for f in res.files:
            if f.scheme != SCHEME_A0:
                self.assertEqual(f.object_source, SRC_DIR)
                self.assertIn(f"/{f.object_name}/", f.remote_path)

    def test_scheme_a0_object_from_fits(self):
        res = self.scan(self.build_tree())
        m = self.by_name(res)
        f = m["observer/2025-04-26/LIGHT/2025-04-26_x.fits"]
        self.assertEqual((f.scheme, f.object_name, f.object_source, f.frame_type), (SCHEME_A0, "NGC 6992", SRC_FITS, "LIGHT"))
        unrec = {u.remote_path.split("/frames/")[1] for u in res.unrecognized}
        self.assertIn("observer/2025-04-26/LIGHT/2025-04-26_y.fits", unrec)   # OBJECT='Target'
        self.assertIn("observer/2025-04-29/biases/b.fits", unrec)
        self.assertIn("observer/2026-10-15/M31/process/", unrec)

    def test_scheme_a0_without_headers_not_downloadable(self):
        res = self.scan(self.build_tree(), headers=False)
        self.assertFalse(any(f.scheme == SCHEME_A0 for f in res.files))

    def test_fits_mismatch_note(self):
        write(self.server / "frames" / "2026-01-01" / "M31" / "LIGHT_1.fit", fits_bytes("M 33", "DARK"))
        res = self.scan(LocalFS(self.server))
        f = res.files[0]
        self.assertEqual(f.object_name, "M31")      # the directory wins
        self.assertEqual(f.frame_type, "LIGHT")
        self.assertTrue(any("M 33" in n for n in f.notes))
        self.assertTrue(any("IMAGETYP" in n for n in f.notes))

    def test_header_failure_does_not_block(self):
        write(self.server / "frames" / "2026-01-01" / "M31" / "LIGHT_1.fit", b"not a fits file" * 300)
        res = self.scan(LocalFS(self.server))
        self.assertEqual(len(res.files), 1)
        self.assertEqual(res.files[0].fits_status, FITS_MISSING)
        self.assertTrue(res.files[0].downloadable)

    def test_scan_connection_error_propagates(self):
        with self.assertRaises(ConnectionFailed):
            Scanner(DeadFS(), "/frames").scan()

    def test_scan_cancel(self):
        ev = threading.Event()
        ev.set()
        res = Scanner(self.build_tree(), "/frames", cancel=ev).scan()
        self.assertTrue(res.cancelled)


# ----------------------------------------------------------------------------- stan lokalny
class LocalStateTests(TmpCase):
    def test_dest_layout(self):
        p = local_state.dest_path("NGC7000", "LIGHT_001.fit", self.dest)
        self.assertEqual(p, self.dest / "NGC7000" / "subs" / "LIGHT_001.fit")

    def test_invalid_names_not_renamed(self):
        with self.assertRaises(local_state.InvalidName):
            local_state.dest_path("M:31", "a.fit", self.dest)
        with self.assertRaises(local_state.InvalidName):
            local_state.dest_path("CON", "a.fit", self.dest)


# ----------------------------------------------------------------------------- pobieranie
class DownloadTests(TmpCase):
    def make_files(self):
        s = self.server / "frames"
        write(s / "2025-08-15" / "M31" / "LIGHT" / "a.fit", fits_bytes("M31", data_blocks=40))
        write(s / "2025-08-15" / "M31" / "DARK" / "d.fit", fits_bytes("M31", "DARK", data_blocks=10))
        write(s / "2026-10-15" / "M31" / "LIGHT_001.fit", fits_bytes("M31", data_blocks=10))
        write(s / "2026-10-15" / "NGC7000" / "LIGHT_001.fit", fits_bytes("NGC7000", data_blocks=10))
        res = Scanner(LocalFS(self.server), "/frames").scan()
        finalize(res)
        return {f.remote_path: f for f in res.files}

    def run_jobs(self, fs_factory, jobs, **kw):
        events = []
        mgr = DownloadManager(fs_factory, self.history, lambda k, j: events.append((k, j)), backoff=(0.01,), **kw)
        mgr.submit(jobs)
        mgr.wait(30)
        return mgr, events

    def jobs_for(self, files, overwrite=False):
        return [TransferJob(f, local_state.dest_path(f.object_name, f.name, self.dest), overwrite=overwrite)
                for f in files]

    def test_download_to_object_subs(self):
        files = self.make_files()
        mgr, events = self.run_jobs(lambda: LocalFS(self.server), self.jobs_for(files.values()))
        self.assertTrue(all(j.state == DONE for j in mgr.jobs.values()), [(j.file.name, j.state, j.message) for j in mgr.jobs.values()])
        got = sorted(str(p.relative_to(self.dest)).replace("\\", "/") for p in self.dest.rglob("*") if p.is_file())
        self.assertEqual(got, ["M31/subs/LIGHT_001.fit", "M31/subs/a.fit", "M31/subs/d.fit", "NGC7000/subs/LIGHT_001.fit"])
        for rp, f in files.items():
            dest = local_state.dest_path(f.object_name, f.name, self.dest)
            self.assertEqual(dest.read_bytes(), (self.server / rp.lstrip("/")).read_bytes())
        self.assertFalse(list(self.dest.rglob("*.subpull-part")))
        self.assertEqual(len(self.history.list_downloads()), 4)
        self.assertIn("idle", [e[0] for e in events])
        # no date/type folders
        self.assertEqual({p.name for p in (self.dest / "M31").iterdir()}, {"subs"})

    def test_duplicate_detection_and_no_overwrite(self):
        files = self.make_files()
        f = next(x for x in files.values() if x.name == "d.fit")
        dest = local_state.dest_path(f.object_name, f.name, self.dest)
        write(dest, b"LOCAL-ORIGINAL")
        clashes = local_state.name_clashes(files.values(), self.dest)
        st, detail = local_state.local_status(f, {}, clashes, self.dest)
        self.assertEqual(st, local_state.LocalStatus.CONFLICT)
        write(dest, (self.server / f.remote_path.lstrip("/")).read_bytes())
        st, _ = local_state.local_status(f, {}, clashes, self.dest)
        self.assertEqual(st, local_state.LocalStatus.PRESENT)
        # even a queued download does not overwrite without consent
        write(dest, b"LOCAL-ORIGINAL")
        mgr, _ = self.run_jobs(lambda: LocalFS(self.server), self.jobs_for([f]))
        self.assertEqual(list(mgr.jobs.values())[0].state, SKIPPED)
        self.assertEqual(dest.read_bytes(), b"LOCAL-ORIGINAL")
        # with consent it overwrites
        mgr, _ = self.run_jobs(lambda: LocalFS(self.server), self.jobs_for([f], overwrite=True))
        self.assertEqual(list(mgr.jobs.values())[0].state, DONE)
        self.assertEqual(dest.read_bytes(), (self.server / f.remote_path.lstrip("/")).read_bytes())

    def test_name_clash_between_sessions(self):
        files = self.make_files()
        write(self.server / "frames" / "2026-10-16" / "M31" / "LIGHT_001.fit", fits_bytes("M31"))
        res = Scanner(LocalFS(self.server), "/frames").scan()
        finalize(res)
        clashes = local_state.name_clashes(res.files, self.dest)
        self.assertEqual(len(clashes), 2)

    def test_history_status(self):
        files = self.make_files()
        f = next(x for x in files.values() if x.name == "a.fit")
        self.run_jobs(lambda: LocalFS(self.server), self.jobs_for([f]))
        local_state.dest_path(f.object_name, f.name, self.dest).unlink()
        st, _ = local_state.local_status(f, self.history.downloaded_remote_paths(), set(), self.dest)
        self.assertEqual(st, local_state.LocalStatus.HISTORY_ONLY)

    def test_interrupted_transfer_resumes(self):
        files = self.make_files()
        f = next(x for x in files.values() if x.name == "a.fit")
        shared = LocalFS(self.server, fail_after_bytes=50_000, fail_times=1)
        mgr, events = self.run_jobs(lambda: shared, self.jobs_for([f]))
        job = list(mgr.jobs.values())[0]
        self.assertEqual(job.state, DONE, job.message)
        self.assertEqual(job.resumed_from, 50_000)            # resumed, not restarted
        self.assertIn(("/" + f.remote_path.lstrip("/"), 50_000), shared.opened)
        dest = local_state.dest_path(f.object_name, f.name, self.dest)
        self.assertEqual(dest.read_bytes(), (self.server / f.remote_path.lstrip("/")).read_bytes())
        self.assertTrue(any(e[1] and "retry" in getattr(e[1], "message", "") for e in events))

    def test_cancel_keeps_part_then_resume(self):
        files = self.make_files()
        f = next(x for x in files.values() if x.name == "a.fit")
        holder = {}

        class SlowFS(LocalFS):
            def open_read(s, path, offset=0, prefetch_size=None):
                base = LocalFS.open_read(s, path, offset)
                class R:
                    def read(_, n):
                        time.sleep(0.02)
                        return base.read(min(n, 4096))
                    def close(_):
                        base.close()
                return R()

        events = []
        mgr = DownloadManager(lambda: SlowFS(self.server), self.history, lambda k, j: events.append(j))
        job = self.jobs_for([f])[0]
        mgr.submit([job])
        t0 = time.time()
        while job.done < 20_000 and time.time() - t0 < 10:
            time.sleep(0.01)
        mgr.cancel_job(job.id)
        mgr.wait(10)
        self.assertEqual(job.state, CANCELLED)
        dest = local_state.dest_path(f.object_name, f.name, self.dest)
        part = local_state.part_path(dest)
        self.assertFalse(dest.exists())
        self.assertTrue(part.exists())
        done_bytes = part.stat().st_size
        st, _ = local_state.local_status(f, {}, set(), self.dest)
        self.assertEqual(st, local_state.LocalStatus.PARTIAL)
        mgr2, _ = self.run_jobs(lambda: LocalFS(self.server), self.jobs_for([f]))
        j2 = list(mgr2.jobs.values())[0]
        self.assertEqual(j2.state, DONE)
        self.assertEqual(j2.resumed_from, done_bytes)
        self.assertEqual(dest.read_bytes(), (self.server / f.remote_path.lstrip("/")).read_bytes())

    def test_changed_remote_file_restarts_part(self):
        files = self.make_files()
        f = next(x for x in files.values() if x.name == "d.fit")
        dest = local_state.dest_path(f.object_name, f.name, self.dest)
        write(local_state.part_path(dest), b"garbage-from-other-file")
        mgr, _ = self.run_jobs(lambda: LocalFS(self.server), self.jobs_for([f]))
        j = list(mgr.jobs.values())[0]
        self.assertEqual((j.state, j.resumed_from), (DONE, 0))
        self.assertEqual(dest.read_bytes(), (self.server / f.remote_path.lstrip("/")).read_bytes())

    def test_connection_failure_reports_error(self):
        files = self.make_files()
        calls = []

        def factory():
            calls.append(1)
            raise ConnectionFailed("server unreachable")
        mgr, _ = self.run_jobs(factory, self.jobs_for(list(files.values())[:1]), retries=2)
        j = list(mgr.jobs.values())[0]
        self.assertEqual(j.state, ERROR)
        self.assertIn("unreachable", j.message)
        self.assertEqual(len(calls), 3)   # 1 + 2 retries
        self.assertFalse(list(self.dest.rglob("*.fit")))

    def test_missing_remote_file(self):
        files = self.make_files()
        f = list(files.values())[0]
        (self.server / f.remote_path.lstrip("/")).unlink()
        mgr, _ = self.run_jobs(lambda: LocalFS(self.server), self.jobs_for([f]))
        self.assertEqual(list(mgr.jobs.values())[0].state, ERROR)


# ----------------------------------------------------------------------------- connection / profile
class ConnectionTests(TmpCase):
    def test_retry_then_fail(self):
        n = []

        def fn():
            n.append(1)
            raise ConnectionFailed("timeout")
        import subpull.config as cfg
        old = cfg.RETRY_BACKOFF
        cfg.RETRY_BACKOFF = (0.01,)
        try:
            with self.assertRaises(ConnectionFailed):
                with_retry(fn, 3)
        finally:
            cfg.RETRY_BACKOFF = old
        self.assertEqual(len(n), 3)

    def test_unreachable_host_timeout(self):
        from subpull.filezilla import SiteProfile
        prof = SiteProfile("Obs", "10.255.255.1", 2222, "sftp", "x", 5, None)
        conn = SFTPConnection(prof, timeout=1)
        t0 = time.time()
        with self.assertRaises(ConnectionFailed):
            conn.connect()
        self.assertLess(time.time() - t0, 5)

    def test_profile_parsing(self):
        xml = self.tmp / "sm.xml"
        xml.write_text("""<?xml version="1.0"?><FileZilla3><Servers><Folder>
          <Server><Host>h.example</Host><Port>2222</Port><Protocol>1</Protocol><User>u</User>
          <Pass encoding="base64">c2VjcmV0</Pass><Logontype>1</Logontype><Name>Obs</Name></Server>
          </Folder></Servers></FileZilla3>""", encoding="utf-8")
        before = xml.read_bytes()
        p = load_site("Obs", xml)
        self.assertEqual((p.host, p.port, p.protocol, p.user, p.password), ("h.example", 2222, "sftp", "u", "secret"))
        self.assertNotIn("secret", repr(p))
        self.assertEqual(xml.read_bytes(), before)

    def test_list_sites_sftp_only(self):
        xml = self.tmp / "sm.xml"
        xml.write_text("""<FileZilla3><Servers>
          <Server><Host>a</Host><Protocol>1</Protocol><Name>Obs</Name></Server>
          <Server><Host>b</Host><Protocol>0</Protocol><Name>Ftp</Name></Server>
          </Servers></FileZilla3>""", encoding="utf-8")
        self.assertEqual(list_sites(xml), ["Obs"])
        self.assertEqual(list_sites(self.tmp / "missing.xml"), [])

    def test_settings_roundtrip(self):
        from subpull.config import Settings
        path = self.tmp / "s.json"
        self.assertIsNone(Settings.load(path).dest_root)        # no hardcoded destination
        s = Settings(dest_root=str(self.dest), profile_name="Obs", source_dir="/data")
        s.save(path)
        path.write_text(path.read_text().replace("{", '{"unknown_key": 1,', 1))   # tolerate unknown keys
        s2 = Settings.load(path)
        self.assertEqual((s2.dest_path, s2.profile_name, s2.source_dir), (self.dest, "Obs", "/data"))

    def test_custom_placeholder(self):
        write(self.server / "d" / "2026-01-01" / "LIGHT" / "x.fits", fits_bytes("Default"))
        fs = LocalFS(self.server)
        res = Scanner(fs, "/d").scan()
        read_headers(res.files, lambda: fs, placeholders=["DEFAULT"])
        finalize(res)
        self.assertEqual(len(res.files), 0)
        self.assertEqual(len(res.unrecognized), 1)

    def test_hostkeys(self):
        hk = self.tmp / "hk.xml"
        blob = b"\x00\x00\x00\x0bssh-ed25519" + b"k" * 36
        hk.write_text(f'<FileZilla3><Server Host="h" Port="2222"><Hostkey>{base64.b64encode(blob).decode()}</Hostkey></Server></FileZilla3>')
        self.assertEqual(load_trusted_hostkeys("h", 2222, hk, self.tmp / "none"), [blob])
        self.assertEqual(load_trusted_hostkeys("h", 22, hk, self.tmp / "none"), [])


if __name__ == "__main__":
    unittest.main()

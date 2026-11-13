from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from subpull import local_state
from subpull.downloader import ACTIVE, CANCELLED, DONE, ERROR, QUEUED, SKIPPED, TransferJob
from subpull.local_state import LocalStatus
from subpull.scanner import FrameFile, ScanResult
from subpull.workflow import (FileFilter, QueueStats, build_plan, compute_statuses, filter_files, fmt_duration,
                              group_files, make_jobs, object_summaries)


def ff(obj, name, date="2026-01-01", ftype="LIGHT", size=100, fits=None):
    return FrameFile(remote_path=f"/{date}/{obj}/{name}", name=name, size=size, mtime=0, session_date=date,
                     object_name=obj, frame_type=ftype, scheme="B", fits=fits)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.files = [
            ff("M31", "LIGHT_1.fit"), ff("M31", "LIGHT_2.fit", date="2026-01-02"),
            ff("M31", "FLAT_1.fit", ftype="FLAT"), ff("M42", "LIGHT_1.fit"), ff("M42", "x.fit", ftype=None),
        ]

    def tearDown(self):
        self._td.cleanup()

    def test_statuses_without_destination(self):
        st = compute_statuses(self.files, {}, None)
        self.assertTrue(all(s[0] == LocalStatus.NEW for s in st.values()))

    def test_statuses_present_and_conflict(self):
        d = local_state.dest_path("M31", "LIGHT_1.fit", self.root)
        d.parent.mkdir(parents=True)
        d.write_bytes(b"x" * 100)
        local_state.dest_path("M31", "FLAT_1.fit", self.root).write_bytes(b"x")
        st = compute_statuses(self.files, {}, self.root)
        self.assertEqual(st[self.files[0].remote_path][0], LocalStatus.PRESENT)
        self.assertEqual(st[self.files[2].remote_path][0], LocalStatus.CONFLICT)

    def test_filter(self):
        st = compute_statuses(self.files, {}, self.root)
        self.assertEqual(len(filter_files(self.files, st, FileFilter(object_name="M31"))), 3)
        self.assertEqual(len(filter_files(self.files, st, FileFilter(types={"FLAT"}))), 1)
        self.assertEqual(len(filter_files(self.files, st, FileFilter(types={"?"}))), 1)
        self.assertEqual(len(filter_files(self.files, st, FileFilter(session="2026-01-02"))), 1)
        self.assertEqual(len(filter_files(self.files, st, FileFilter(object_query="m4"))), 2)
        self.assertEqual(len(filter_files(self.files, st, FileFilter(text="flat"))), 1)

    def test_new_only(self):
        d = local_state.dest_path("M42", "LIGHT_1.fit", self.root)
        d.parent.mkdir(parents=True)
        d.write_bytes(b"x" * 100)
        st = compute_statuses(self.files, {}, self.root)
        self.assertEqual(len(filter_files(self.files, st, FileFilter(new_only=True))), 4)

    def test_grouping(self):
        g = group_files(self.files, "object")
        self.assertEqual([k for k, _ in g], ["M31", "M42"])
        g = group_files(self.files, "session")
        self.assertEqual([k for k, _ in g], ["2026-01-02", "2026-01-01"])
        g = group_files(self.files, "type")
        self.assertEqual([k for k, _ in g], ["LIGHT", "FLAT", "?"])

    def test_object_summaries(self):
        res = ScanResult(root="/", files=self.files)
        s = {o.name: o for o in object_summaries(res, compute_statuses(self.files, {}, self.root))}
        self.assertEqual((s["M31"].files, s["M31"].sessions, s["M31"].types), (3, 2, {"LIGHT": 2, "FLAT": 1}))

    def test_plan_opt_in_and_overwrite(self):
        d = local_state.dest_path("M31", "LIGHT_1.fit", self.root)
        d.parent.mkdir(parents=True)
        d.write_bytes(b"LOCAL")                                # size conflict
        st = compute_statuses(self.files, {}, self.root)
        plan = build_plan(self.files, st, self.root)
        self.assertEqual(len(plan.todo), 4)
        self.assertEqual(plan.skipped[0][1], LocalStatus.CONFLICT.value)
        self.assertEqual(plan.overwrite_count, 0)
        plan = build_plan(self.files, st, self.root, include={LocalStatus.CONFLICT})
        self.assertEqual((len(plan.todo), plan.overwrite_count), (5, 1))
        # without an explicit overwrite state the job never overwrites
        jobs, _ = make_jobs(plan.todo, st, self.root)
        self.assertFalse(any(j.overwrite for j in jobs))
        jobs, _ = make_jobs(plan.todo, st, self.root, overwrite_states={LocalStatus.CONFLICT})
        self.assertEqual([j.file.name for j in jobs if j.overwrite], ["LIGHT_1.fit"])
        self.assertEqual(d.read_bytes(), b"LOCAL")

    def test_plan_skips_queued_and_new_folder_flag(self):
        st = compute_statuses(self.files, {}, self.root)
        plan = build_plan(self.files, st, self.root, active_paths={self.files[0].remote_path})
        self.assertEqual(len(plan.todo), 4)
        self.assertTrue(all(r.new_folder for r in plan.rows))
        self.assertEqual(plan.rows[0].dest, self.root / "M31" / "subs")

    def test_queue_stats(self):
        def job(state, total, done=0, speed=0.0):
            j = TransferJob(ff("M31", f"{state}.fit", size=total), self.root / "x")
            j.state, j.done, j.speed = state, done, speed
            return j
        jobs = [job(DONE, 100, 100), job(ACTIVE, 100, 40, 10.0), job(QUEUED, 100), job(SKIPPED, 100),
                job(ERROR, 100, 10), job(CANCELLED, 100, 50)]
        s = QueueStats.from_jobs(jobs)
        self.assertEqual((s.total_files, s.done, s.skipped, s.errors, s.cancelled, s.active, s.queued),
                         (6, 1, 1, 1, 1, 1, 1))
        self.assertEqual((s.total_bytes, s.done_bytes, s.remaining_bytes), (500, 200, 160))
        self.assertAlmostEqual(s.eta_seconds, 16.0)
        self.assertTrue(s.running)

    def test_fmt_duration(self):
        self.assertEqual(fmt_duration(None), "—")
        self.assertEqual(fmt_duration(42), "42 s")
        self.assertEqual(fmt_duration(125), "2 min 05 s")
        self.assertEqual(fmt_duration(3700), "1 h 01 min")


if __name__ == "__main__":
    unittest.main()

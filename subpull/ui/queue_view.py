"""Transfer queue page: overall progress, statistics and per-file transfers."""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QProgressBar, QStackedWidget, QTableView, QWidget

from ..i18n import number, t
from ..workflow import fmt_duration, fmt_size, fmt_speed
from .controller import Controller
from .models import QueueModel
from .widgets import Card, EmptyState, QueueDelegate, StatTile, Texts, hbox, headers_changed, label, vbox


class QueuePage(QWidget):
    def __init__(self, ctl: Controller, qs: QSettings, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.ctl = ctl
        self.qs = qs

        self.tx = tx = Texts()

        self.state_label = label("", "Muted")
        header = hbox(vbox(tx.label("nav.queue", "PageTitle"), self.state_label, spacing=2), None)

        # ---- summary card ------------------------------------------------------------
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress_text = label("", "Muted")
        self.t_files, self.t_speed, self.t_left, self.t_eta, self.t_problems = (StatTile() for _ in range(5))
        for tile, key in ((self.t_files, "downloaded"), (self.t_speed, "speed"), (self.t_left, "remaining"),
                          (self.t_eta, "eta"), (self.t_problems, "problems")):
            tx.bind(tile.set_caption, f"queue.tile.{key}")
        tiles = hbox(self.t_files, self.t_speed, self.t_left, self.t_eta, self.t_problems, None, spacing=36)
        self.btn_stop = tx.button("queue.stop_all", "stop", "Danger", "queue.stop_all.tooltip", self.ctl.stop_all)
        self.btn_cancel = tx.button("queue.cancel_selected", "x", slot=self._cancel_selected)
        self.btn_retry = tx.button("queue.retry_failed", "retry", tooltip="queue.retry_failed.tooltip",
                                   slot=self.ctl.retry_failed)
        self.btn_clear = tx.button("queue.clear_finished", "clear", tooltip="queue.clear_finished.tooltip",
                                   slot=self._clear_finished)
        summary = Card()
        summary.setLayout(vbox(hbox(self.progress_text, None, self.btn_stop, self.btn_cancel, self.btn_retry, self.btn_clear),
                               self.progress, 4, tiles, spacing=10, margins=(16, 14, 16, 14)))

        # ---- table -------------------------------------------------------------------
        self.model = QueueModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegate(QueueDelegate(self.table))
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(int(self.fontMetrics().height() * 1.9))
        cw = self.fontMetrics().averageCharWidth()
        for i, w in enumerate((48, 16, 14, 26, 12, 11, 40)):
            self.table.setColumnWidth(i, cw * w)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if qs.contains("queue/header"):
            self.table.horizontalHeader().restoreState(qs.value("queue/header"))

        self.empty = EmptyState("queue", "", "")
        tx.bind(self.empty.title.setText, "queue.empty.title")
        tx.bind(self.empty.text.setText, "queue.empty.text")
        self.stack = QStackedWidget()
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.table)

        self.setLayout(vbox(header, summary, self.stack, spacing=12, margins=(20, 16, 20, 16)))

        self._timer = QTimer(self, interval=500, timeout=self.update_stats)
        ctl.jobUpdated.connect(self._on_job)
        ctl.queueIdle.connect(self.update_stats)
        self.update_stats()

    def retranslate(self) -> None:
        self.tx.retranslate()
        headers_changed(self.model)
        self.table.viewport().update()
        self.update_stats()

    def save_state(self) -> None:
        self.qs.setValue("queue/header", self.table.horizontalHeader().saveState())

    def _on_job(self, job) -> None:
        self.model.update_job(job)
        self.stack.setCurrentIndex(1)
        if not self._timer.isActive():
            self._timer.start()

    def _cancel_selected(self) -> None:
        ids = {self.model.job_at(ix.row()).id for ix in self.table.selectionModel().selectedRows()}
        self.ctl.cancel_jobs(ids)

    def _clear_finished(self) -> None:
        self.model.remove_ids(self.ctl.clear_finished())
        self.update_stats()

    def update_stats(self) -> None:
        s = self.ctl.queue_stats()
        running = self.ctl.downloading or s.running
        if not running:
            self._timer.stop()
        self.stack.setCurrentIndex(1 if self.model.rowCount() else 0)
        self.progress.setValue(int(1000 * s.fraction))
        self.btn_stop.setEnabled(running)
        self.btn_retry.setEnabled(not running and (s.errors + s.cancelled) > 0)
        self.btn_clear.setEnabled(self.model.rowCount() > 0 and not running)
        if s.total_files == 0:
            self.state_label.setText(t("queue.state.idle"))
            self.progress_text.setText(t("queue.progress.none"))
        else:
            if running:
                self.state_label.setText(t("queue.state.running", active=s.active, queued=s.queued))
            else:
                self.state_label.setText(t("queue.state.finished_problems" if s.errors or s.cancelled
                                           else "queue.state.finished"))
            self.progress_text.setText(t("queue.progress.text", done=fmt_size(s.done_bytes), total=fmt_size(s.total_bytes),
                                         percent=number(s.fraction * 100, ".1f")))
        self.t_files.value.setText(f"{s.done} / {s.total_files - s.skipped}"
                                   + (f"  {t('queue.tile.skipped', count=s.skipped)}" if s.skipped else ""))
        self.t_speed.value.setText(fmt_speed(s.speed) if running else "—")
        self.t_left.value.setText(fmt_size(s.remaining_bytes) if running else "—")
        self.t_eta.value.setText(fmt_duration(s.eta_seconds) if running else "—")
        self.t_problems.value.setText(f"{s.errors} / {s.cancelled}")
        self.t_problems.value.setStyleSheet("color: #e5554e;" if s.errors else "")

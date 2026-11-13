"""Tkinter/ttk user interface. All network work runs in background threads; the
GUI receives events through a queue (``_pump``), so the window never blocks."""
from __future__ import annotations

import logging
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from . import config, local_state
from .config import Settings
from .connection import AuthFailed, ConnectionFailed, HostKeyError, SFTPConnection
from .downloader import (ACTIVE, CANCELLED, DONE, ERROR, FINAL_STATES, QUEUED, SKIPPED, VERIFYING,
                         DownloadManager, TransferJob)
from .filezilla import ProfileError, list_sites, load_site
from .history import History
from .local_state import InvalidName, LocalStatus
from .scanner import SCHEME_A0, SRC_FITS, ScanResult, Scanner, finalize, read_headers

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ look & feel
C = dict(bg="#121418", panel="#1a1d22", panel2="#20242a", border="#2b3038", text="#e6e8eb", muted="#8b939e",
         accent="#4c8dff", accent2="#3a74e0", ok="#3fb950", warn="#d29922", err="#f85149", sel="#24395e")
F = {}   # fonts, filled in by setup_style()
CHK_ON, CHK_OFF, CHK_PART = "☑", "☐", "◪"
TYPES = ("LIGHT", "DARK", "FLAT", "BIAS", "DARKFLAT", "?")
ALL_SESSIONS = "all"

STATUS_TAG = {LocalStatus.NEW: "new", LocalStatus.PRESENT: "present", LocalStatus.CONFLICT: "conflict",
              LocalStatus.PARTIAL: "partial", LocalStatus.HISTORY_ONLY: "history", LocalStatus.NAME_CLASH: "clash"}


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def bar(frac: float, width: int = 14) -> str:
    frac = max(0.0, min(1.0, frac))
    full = int(round(frac * width))
    return "█" * full + "░" * (width - full) + f" {frac * 100:5.1f}%"


class QueueLogHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__(logging.INFO)
        self.q = q
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            self.q.put(("log", self.format(record), record.levelno))
        except Exception:
            pass


def setup_style(root: tk.Tk) -> None:
    fam = tkfont.nametofont("TkDefaultFont").actual("family")
    if sys.platform == "win32":
        fam = "Segoe UI"
    mono = tkfont.nametofont("TkFixedFont").actual("family")
    F.update(base=(fam, 10), bold=(fam, 10, "bold"), title=(fam, 15, "bold"), small=(fam, 9), mono=(mono, 9))
    root.configure(bg=C["bg"])
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=C["bg"], foreground=C["text"], fieldbackground=C["panel2"], font=F["base"],
                bordercolor=C["border"], lightcolor=C["border"], darkcolor=C["border"], troughcolor=C["panel2"],
                selectbackground=C["sel"], selectforeground=C["text"], insertcolor=C["text"])
    s.configure("TFrame", background=C["bg"])
    s.configure("Panel.TFrame", background=C["panel"])
    s.configure("TLabel", background=C["bg"], foreground=C["text"])
    s.configure("Panel.TLabel", background=C["panel"])
    s.configure("Muted.TLabel", background=C["bg"], foreground=C["muted"], font=F["small"])
    s.configure("PanelMuted.TLabel", background=C["panel"], foreground=C["muted"], font=F["small"])
    s.configure("Title.TLabel", font=F["title"])
    s.configure("H.TLabel", background=C["panel"], font=F["bold"])
    s.configure("TButton", background=C["panel2"], foreground=C["text"], padding=(10, 5), borderwidth=1, focusthickness=0)
    s.map("TButton", background=[("disabled", C["panel"]), ("pressed", C["border"]), ("active", C["border"])],
          foreground=[("disabled", C["muted"])])
    s.configure("Accent.TButton", background=C["accent"], foreground="#ffffff", font=F["bold"], bordercolor=C["accent"])
    s.map("Accent.TButton", background=[("disabled", C["panel2"]), ("pressed", C["accent2"]), ("active", C["accent2"])],
          foreground=[("disabled", C["muted"])])
    s.configure("Link.TButton", background=C["bg"], foreground=C["accent"], padding=(4, 0), borderwidth=0, font=F["small"])
    s.map("Link.TButton", background=[("active", C["bg"])], foreground=[("active", C["text"])])
    s.configure("TCheckbutton", background=C["bg"], foreground=C["text"], indicatorbackground=C["panel2"],
                indicatorforeground=C["text"])
    s.map("TCheckbutton", background=[("active", C["bg"])], indicatorbackground=[("selected", C["accent"])])
    s.configure("Panel.TCheckbutton", background=C["panel"])
    s.map("Panel.TCheckbutton", background=[("active", C["panel"])])
    s.configure("TEntry", fieldbackground=C["panel2"], foreground=C["text"], padding=4)
    s.configure("TCombobox", fieldbackground=C["panel2"], background=C["panel2"], foreground=C["text"],
                arrowcolor=C["text"], padding=3)
    s.map("TCombobox", fieldbackground=[("readonly", C["panel2"])], foreground=[("readonly", C["text"])])
    root.option_add("*TCombobox*Listbox.background", C["panel2"])
    root.option_add("*TCombobox*Listbox.foreground", C["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["sel"])
    s.configure("Treeview", background=C["panel"], fieldbackground=C["panel"], foreground=C["text"], rowheight=24,
                borderwidth=0)
    s.map("Treeview", background=[("selected", C["sel"])], foreground=[("selected", C["text"])])
    s.configure("Treeview.Heading", background=C["panel2"], foreground=C["muted"], font=F["small"], relief="flat", padding=4)
    s.map("Treeview.Heading", background=[("active", C["border"])])
    s.configure("TNotebook", background=C["bg"], borderwidth=0, tabmargins=(0, 4, 0, 0))
    s.configure("TNotebook.Tab", background=C["bg"], foreground=C["muted"], padding=(14, 6), borderwidth=0)
    s.map("TNotebook.Tab", background=[("selected", C["panel"])], foreground=[("selected", C["text"])])
    s.configure("Horizontal.TProgressbar", background=C["accent"], troughcolor=C["panel2"], bordercolor=C["panel2"],
                lightcolor=C["accent"], darkcolor=C["accent"], thickness=8)
    s.configure("TPanedwindow", background=C["bg"])
    s.configure("Vertical.TScrollbar", background=C["panel2"], troughcolor=C["panel"], arrowcolor=C["muted"],
                bordercolor=C["panel"])


def tree_with_scroll(parent, **kw) -> ttk.Treeview:
    frame = ttk.Frame(parent, style="Panel.TFrame")
    frame.pack(fill="both", expand=True)
    tv = ttk.Treeview(frame, **kw)
    sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=sb.set)
    tv.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    return tv


# =========================================================================== application
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.Queue = queue.Queue()
        logging.getLogger().addHandler(QueueLogHandler(self.events))
        self.history = History()
        self.settings = Settings.load()

        self.conn: Optional[SFTPConnection] = None
        self.profile_error: Optional[str] = None
        self.sites = list_sites()

        self.result: Optional[ScanResult] = None
        self.files_by_path: dict = {}
        self.status: dict[str, tuple[LocalStatus, Optional[str]]] = {}
        self.selected: set[str] = set()
        self._visible: list = []
        self.scan_thread: Optional[threading.Thread] = None
        self.scan_cancel = threading.Event()
        self.batch_ids: set[int] = set()
        self.jobs: dict[int, TransferJob] = {}
        self.manager = DownloadManager(lambda: self._require_conn().connect(), self.history,
                                       lambda kind, job: self.events.put(("dl", kind, job)),
                                       workers=max(1, int(self.settings.parallel_transfers)))

        root.title(config.APP_NAME)
        root.geometry("1440x900")
        root.minsize(1100, 680)
        setup_style(root)
        self._build()
        if self.settings.profile_name not in self.sites and self.sites:
            if self.settings.profile_name:
                log.warning("Site '%s' not found in FileZilla – using '%s'", self.settings.profile_name, self.sites[0])
            self.settings.profile_name = self.sites[0]
            self.site_var.set(self.sites[0])
        self._load_profile(self.settings.profile_name)
        self._update_dest_label()
        self._refresh_last_scan()
        self._refresh_history()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._pump)
        root.after(200, lambda: self.vpane.sashpos(0, int(self.vpane.winfo_height() * 0.66)))

    # ------------------------------------------------------------------ settings / profile
    @property
    def dest_root(self) -> Optional[Path]:
        return self.settings.dest_path

    def _load_profile(self, name: str) -> None:
        self.conn, self.profile_error = None, None
        try:
            if not name:
                raise ProfileError("No SFTP site in FileZilla Site Manager – create one first.")
            profile = load_site(name)
            self.conn = SFTPConnection(profile)
            log.info("Using FileZilla site '%s': %s (%s)", name, self.conn.label, profile.auth_label)
        except (ProfileError, ConnectionFailed) as e:
            self.profile_error = str(e)
            log.error("Site profile: %s", e)
        if self.conn:
            self.profile_label.configure(text=f"{self.conn.label} · {self.conn.profile.auth_label}")
        else:
            self.profile_label.configure(text="no usable site profile")
        self._set_conn_state("idle")

    def _on_site_selected(self, _=None) -> None:
        name = self.site_var.get()
        if name == self.settings.profile_name and self.conn:
            return
        self.settings.profile_name = name
        self.settings.save()
        self._load_profile(name)

    def _choose_dest(self) -> bool:
        initial = str(self.dest_root) if self.dest_root and self.dest_root.exists() else str(Path.home())
        d = filedialog.askdirectory(parent=self.root, initialdir=initial, mustexist=True,
                                    title="Choose the destination folder (frames go to <folder>/<OBJECT>/subs)")
        if not d:
            return False
        self.settings.dest_root = str(Path(d))
        self.settings.save()
        log.info("Destination folder: %s", self.settings.dest_root)
        self._update_dest_label()
        if self.result:
            self._recompute_status()
            self._fill_objects()
            self._fill_files()
        return True

    def _update_dest_label(self) -> None:
        if self.dest_root:
            self.dest_label.configure(text=f"Frames are saved to  {self.dest_root / '<OBJECT>' / config.SUBS_DIRNAME}",
                                      foreground=C["muted"])
        else:
            self.dest_label.configure(text="Destination folder not set", foreground=C["warn"])

    def _require_conn(self) -> SFTPConnection:
        if self.conn is None:
            raise ConnectionFailed(self.profile_error or "No site profile")
        return self.conn

    # ================================================================== building the UI
    def _build(self) -> None:
        r = self.root
        # --- header: title, destination, connection --------------------------------
        head = ttk.Frame(r, padding=(16, 12, 16, 6))
        head.pack(fill="x")
        left = ttk.Frame(head)
        left.pack(side="left")
        ttk.Label(left, text=config.APP_NAME, style="Title.TLabel").pack(anchor="w")
        dl = ttk.Frame(left)
        dl.pack(anchor="w")
        self.dest_label = ttk.Label(dl, text="", style="Muted.TLabel")
        self.dest_label.pack(side="left")
        ttk.Button(dl, text="Change…", style="Link.TButton", command=self._choose_dest).pack(side="left", padx=6)

        card = ttk.Frame(head, style="Panel.TFrame", padding=(12, 8))
        card.pack(side="right")
        self.dot = tk.Canvas(card, width=12, height=12, bg=C["panel"], highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 8))
        info = ttk.Frame(card, style="Panel.TFrame")
        info.pack(side="left")
        top = ttk.Frame(info, style="Panel.TFrame")
        top.pack(anchor="w")
        ttk.Label(top, text="Site", style="PanelMuted.TLabel").pack(side="left")
        self.site_var = tk.StringVar(value=self.settings.profile_name)
        site_cb = ttk.Combobox(top, textvariable=self.site_var, values=self.sites, width=14, state="readonly")
        site_cb.pack(side="left", padx=(6, 8))
        site_cb.bind("<<ComboboxSelected>>", self._on_site_selected)
        self.profile_label = ttk.Label(top, text="", style="H.TLabel")
        self.profile_label.pack(side="left")
        self.conn_text = ttk.Label(info, text="", style="PanelMuted.TLabel")
        self.conn_text.pack(anchor="w")
        self.btn_test = ttk.Button(card, text="Test connection", command=self.test_connection)
        self.btn_test.pack(side="left", padx=(14, 0))

        # --- scan bar -------------------------------------------------------------------
        bar_ = ttk.Frame(r, padding=(16, 6))
        bar_.pack(fill="x")
        ttk.Label(bar_, text="Remote folder").pack(side="left")
        self.source_var = tk.StringVar(value=self.settings.source_dir)
        ttk.Entry(bar_, textvariable=self.source_var, width=28).pack(side="left", padx=(8, 12))
        self.fits_var = tk.BooleanVar(value=self.settings.read_fits)
        ttk.Checkbutton(bar_, text="Verify with FITS headers", variable=self.fits_var).pack(side="left")
        self.btn_scan = ttk.Button(bar_, text="Scan server", style="Accent.TButton", command=self.start_scan)
        self.btn_scan.pack(side="left", padx=(14, 6))
        self.btn_scan_cancel = ttk.Button(bar_, text="Cancel scan", command=self.scan_cancel.set, state="disabled")
        self.btn_scan_cancel.pack(side="left")
        self.scan_info = ttk.Label(bar_, text="", style="Muted.TLabel")
        self.scan_info.pack(side="right")

        # --- main split -------------------------------------------------------------------
        vpane = self.vpane = ttk.Panedwindow(r, orient="vertical")
        vpane.pack(fill="both", expand=True, padx=16, pady=(4, 12))
        hpane = ttk.Panedwindow(vpane, orient="horizontal")
        vpane.add(hpane, weight=3)
        self._build_objects(hpane)
        self._build_files(hpane)
        bottom = ttk.Notebook(vpane)
        vpane.add(bottom, weight=2)
        self._build_queue(bottom)
        self._build_history(bottom)
        self._build_log(bottom)
        self.notebook = bottom

    def _build_objects(self, parent) -> None:
        fr = ttk.Frame(parent, style="Panel.TFrame", padding=10)
        parent.add(fr, weight=1)
        ttk.Label(fr, text="Objects", style="H.TLabel").pack(anchor="w")
        self.obj_search = tk.StringVar()
        ttk.Entry(fr, textvariable=self.obj_search).pack(fill="x", pady=(6, 6))
        self.obj_search.trace_add("write", lambda *_: self._fill_objects())
        self.obj_tree = tree_with_scroll(fr, columns=("n", "new", "size"), show="tree headings", selectmode="browse", height=8)
        self.obj_tree.heading("#0", text="Object", anchor="w")
        self.obj_tree.heading("n", text="Frames")
        self.obj_tree.heading("new", text="New")
        self.obj_tree.heading("size", text="Size")
        self.obj_tree.column("#0", width=130)
        for c, w in (("n", 56), ("new", 52), ("size", 76)):
            self.obj_tree.column(c, width=w, anchor="e", stretch=False)
        self.obj_tree.bind("<<TreeviewSelect>>", lambda e: self._fill_files())
        self.btn_unrec = ttk.Button(fr, text="Unrecognized: 0", command=self.show_unrecognized)
        self.btn_unrec.pack(fill="x", pady=(8, 0))

    def _build_files(self, parent) -> None:
        fr = ttk.Frame(parent, style="Panel.TFrame", padding=10)
        parent.add(fr, weight=4)
        flt = ttk.Frame(fr, style="Panel.TFrame")
        flt.pack(fill="x")
        ttk.Label(flt, text="Group by", style="PanelMuted.TLabel").pack(side="left")
        self.group_var = tk.StringVar(value="object")
        cb = ttk.Combobox(flt, textvariable=self.group_var, values=("object", "session"), width=8, state="readonly")
        cb.pack(side="left", padx=(4, 12))
        cb.bind("<<ComboboxSelected>>", lambda e: self._fill_files())
        ttk.Label(flt, text="Type", style="PanelMuted.TLabel").pack(side="left")
        self.type_vars = {}
        for t in TYPES:
            v = tk.BooleanVar(value=True)
            self.type_vars[t] = v
            ttk.Checkbutton(flt, text=t, variable=v, style="Panel.TCheckbutton",
                            command=self._fill_files).pack(side="left", padx=2)
        ttk.Label(flt, text="Session", style="PanelMuted.TLabel").pack(side="left", padx=(12, 0))
        self.session_var = tk.StringVar(value=ALL_SESSIONS)
        self.session_cb = ttk.Combobox(flt, textvariable=self.session_var, values=(ALL_SESSIONS,), width=12, state="readonly")
        self.session_cb.pack(side="left", padx=4)
        self.session_cb.bind("<<ComboboxSelected>>", lambda e: self._fill_files())
        self.only_new = tk.BooleanVar(value=False)
        ttk.Checkbutton(flt, text="new only", variable=self.only_new, style="Panel.TCheckbutton",
                        command=self._fill_files).pack(side="left", padx=(10, 0))
        self.file_search = tk.StringVar()
        self.file_search.trace_add("write", lambda *_: self._fill_files())
        ttk.Entry(flt, textvariable=self.file_search, width=18).pack(side="right")
        ttk.Label(flt, text="Search", style="PanelMuted.TLabel").pack(side="right", padx=4)

        tfr = ttk.Frame(fr, style="Panel.TFrame")
        tfr.pack(fill="both", expand=True, pady=(8, 8))
        cols = ("obj", "type", "date", "size", "filter", "local", "notes")
        self.tree = tree_with_scroll(tfr, columns=cols, show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="  File", anchor="w")
        for c, t, w, a in (("obj", "Object", 110, "w"), ("type", "Type", 80, "w"), ("date", "Session", 90, "w"),
                           ("size", "Size", 80, "e"), ("filter", "Filter", 55, "w"), ("local", "Local", 160, "w"),
                           ("notes", "Notes", 260, "w")):
            self.tree.heading(c, text=t, anchor=a)
            self.tree.column(c, width=w, anchor=a, stretch=(c == "notes"))
        self.tree.column("#0", width=380)
        self.tree.tag_configure("group", font=F["bold"], background=C["panel2"])
        self.tree.tag_configure("present", foreground=C["ok"])
        self.tree.tag_configure("conflict", foreground=C["warn"])
        self.tree.tag_configure("partial", foreground=C["accent"])
        self.tree.tag_configure("history", foreground=C["muted"])
        self.tree.tag_configure("clash", foreground=C["err"])
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<space>", self._on_space)

        foot = ttk.Frame(fr, style="Panel.TFrame")
        foot.pack(side="bottom", fill="x", pady=(8, 0), before=tfr)
        act = ttk.Frame(fr, style="Panel.TFrame")
        act.pack(side="bottom", fill="x", before=tfr)
        for text, cmd in (("Select visible", lambda: self._select_visible(True)),
                          ("Clear visible", lambda: self._select_visible(False)),
                          ("Select object", self._select_object),
                          ("Select session", self._select_session),
                          ("Select all", lambda: self._select_all(True)),
                          ("Clear all", lambda: self._select_all(False))):
            ttk.Button(act, text=text, command=cmd).pack(side="left", padx=(0, 4))
        self.btn_headers = ttk.Button(act, text="Read FITS headers", command=self.read_selected_headers)
        self.btn_headers.pack(side="left", padx=(8, 0))
        self.btn_download = ttk.Button(foot, text="Download selected", style="Accent.TButton", command=self.confirm_download)
        self.btn_download.pack(side="right")
        self.sel_info = ttk.Label(foot, text="", style="Panel.TLabel", font=F["bold"])
        self.sel_info.pack(side="right", padx=12)
        ttk.Label(foot, text="Click ☐ next to a file or group to select it · Space toggles highlighted rows",
                  style="PanelMuted.TLabel").pack(side="left")
        self._update_sel_info()

    def _build_queue(self, nb) -> None:
        fr = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        nb.add(fr, text="Transfer queue")
        top = ttk.Frame(fr, style="Panel.TFrame")
        top.pack(fill="x")
        self.global_pb = ttk.Progressbar(top, mode="determinate", maximum=1000)
        self.global_pb.pack(side="left", fill="x", expand=True)
        self.queue_info = ttk.Label(top, text="No transfers", style="PanelMuted.TLabel", width=75, anchor="e")
        self.queue_info.pack(side="left", padx=(12, 0))
        btns = ttk.Frame(fr, style="Panel.TFrame")
        btns.pack(fill="x", pady=(8, 6))
        self.btn_stop = ttk.Button(btns, text="Stop all", command=self.stop_all, state="disabled")
        self.btn_stop.pack(side="left")
        ttk.Button(btns, text="Cancel selected", command=self.cancel_selected_jobs).pack(side="left", padx=4)
        ttk.Button(btns, text="Retry failed / cancelled", command=self.retry_failed).pack(side="left", padx=4)
        ttk.Button(btns, text="Clear finished", command=self.clear_finished).pack(side="left", padx=4)
        cols = ("obj", "state", "progress", "speed", "size", "msg")
        self.qtree = tree_with_scroll(fr, columns=cols, show="tree headings", selectmode="extended", height=6)
        self.qtree.heading("#0", text="File", anchor="w")
        self.qtree.column("#0", width=330)
        for c, t, w in (("obj", "Object", 110), ("state", "Status", 95), ("progress", "Progress", 170),
                        ("speed", "Speed", 90), ("size", "Size", 80), ("msg", "Info", 320)):
            self.qtree.heading(c, text=t, anchor="w")
            self.qtree.column(c, width=w, anchor="w", stretch=(c == "msg"))
        self.qtree.tag_configure(DONE, foreground=C["ok"])
        self.qtree.tag_configure(ERROR, foreground=C["err"])
        self.qtree.tag_configure(CANCELLED, foreground=C["warn"])
        self.qtree.tag_configure(SKIPPED, foreground=C["muted"])
        self.qtree.tag_configure(ACTIVE, foreground=C["accent"])

    def _build_history(self, nb) -> None:
        fr = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        nb.add(fr, text="History")
        top = ttk.Frame(fr, style="Panel.TFrame")
        top.pack(fill="x", pady=(0, 6))
        ttk.Button(top, text="Refresh", command=self._refresh_history).pack(side="left")
        self.hist_search = tk.StringVar()
        self.hist_search.trace_add("write", lambda *_: self._refresh_history())
        ttk.Entry(top, textvariable=self.hist_search, width=24).pack(side="right")
        ttk.Label(top, text="Search", style="PanelMuted.TLabel").pack(side="right", padx=4)
        pane = ttk.Panedwindow(fr, orient="horizontal")
        pane.pack(fill="both", expand=True)
        a = ttk.Frame(pane, style="Panel.TFrame")
        b = ttk.Frame(pane, style="Panel.TFrame")
        pane.add(a, weight=3)
        pane.add(b, weight=1)
        cols = ("obj", "date", "type", "size", "ver", "remote", "local")
        self.htree = tree_with_scroll(a, columns=cols, show="tree headings", height=6)
        self.htree.heading("#0", text="Downloaded", anchor="w")
        self.htree.column("#0", width=125, stretch=False)
        for c, t, w in (("obj", "Object", 100), ("date", "Session", 85), ("type", "Type", 70), ("size", "Size", 75),
                        ("ver", "Verified", 90), ("remote", "Server path", 300), ("local", "Local path", 300)):
            self.htree.heading(c, text=t, anchor="w")
            self.htree.column(c, width=w, anchor="w")
        self.optree = tree_with_scroll(b, columns=("sum",), show="tree headings", height=6)
        self.optree.heading("#0", text="Operation", anchor="w")
        self.optree.heading("sum", text="Summary", anchor="w")
        self.optree.column("#0", width=170, stretch=False)
        self.optree.column("sum", width=320)

    def _build_log(self, nb) -> None:
        fr = ttk.Frame(nb, style="Panel.TFrame", padding=10)
        nb.add(fr, text="Log")
        self.log_text = tk.Text(fr, bg=C["panel"], fg=C["text"], insertbackground=C["text"], relief="flat",
                                font=F["mono"], wrap="none", height=8)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.tag_configure("warn", foreground=C["warn"])
        self.log_text.tag_configure("err", foreground=C["err"])
        self.log_text.configure(state="disabled")

    # ================================================================== connection
    def _set_conn_state(self, state: str, text: str = "") -> None:
        colors = {"idle": C["muted"], "busy": C["warn"], "ok": C["ok"], "error": C["err"]}
        defaults = {"idle": "not connected – connects only when you scan or test",
                    "busy": "connecting…", "ok": "connected", "error": "connection error"}
        if self.conn is None:
            state = "error"
            text = self.profile_error or ("no SFTP sites in FileZilla Site Manager" if not self.sites else "no site selected")
        self.dot.delete("all")
        self.dot.create_oval(1, 1, 11, 11, fill=colors[state], outline="")
        self.conn_text.configure(text=text or defaults[state])

    def test_connection(self) -> None:
        if self.conn is None:
            messagebox.showerror("Site profile", self.profile_error or "No site profile")
            return
        self.btn_test.configure(state="disabled")
        self._set_conn_state("busy")
        conn = self.conn
        src = self.source_var.get().strip() or "/"

        def work():
            t0 = time.time()
            try:
                fs = conn.connect()
                try:
                    fs.listdir(src)
                finally:
                    fs.close()
                self.events.put(("conn", "ok", f"connected · {int((time.time() - t0) * 1000)} ms · {datetime.now():%H:%M:%S}"))
            except Exception as e:
                self.events.put(("conn_error", e, self.test_connection))
        threading.Thread(target=work, daemon=True).start()

    def _handle_conn_error(self, e: Exception, retry) -> None:
        self._set_conn_state("error", str(e)[:110])
        self.btn_test.configure(state="normal")
        if isinstance(e, HostKeyError):
            if e.mismatch:
                messagebox.showerror("Host key", f"{e}\n\nConnection blocked. Verify the server in FileZilla.")
                return
            if messagebox.askyesno("Unknown host key",
                                   f"{e}\n\nThis key is not stored in FileZilla or known_hosts.\n"
                                   "Trust it for this program session only?"):
                self.conn.accept_hostkey_for_session(e.key_blob)
                retry()
            return
        if isinstance(e, AuthFailed):
            messagebox.showerror("Authentication", str(e))
            return
        if messagebox.askretrycancel("Connection error", f"{e}\n\nRetry?"):
            retry()

    # ================================================================== scanning
    def start_scan(self) -> None:
        if self.conn is None:
            messagebox.showerror("Site profile", self.profile_error or "No site profile")
            return
        if self.scan_thread and self.scan_thread.is_alive():
            return
        src = self.source_var.get().strip() or "/"
        self.settings.source_dir = src
        self.settings.read_fits = bool(self.fits_var.get())
        self.settings.save()
        self.scan_cancel.clear()
        self.btn_scan.configure(state="disabled")
        self.btn_scan_cancel.configure(state="normal")
        self._set_conn_state("busy")
        self.scan_info.configure(text="Scanning…")
        self.scan_thread = threading.Thread(target=self._scan_worker, args=(self.conn, src, bool(self.fits_var.get())),
                                            daemon=True)
        self.scan_thread.start()

    def _scan_worker(self, conn: SFTPConnection, src: str, read_all_headers: bool) -> None:
        t0 = time.time()
        try:
            fs = conn.connect_with_retry(
                cancel=self.scan_cancel,
                on_retry=lambda n, e: self.events.put(("conn", "busy", f"retrying connection ({n})… {e}")))
            self.events.put(("conn", "ok", f"connected · scanning {src}"))
            try:
                res = Scanner(fs, src, progress=lambda m: self.events.put(("scan_progress", m)),
                              cancel=self.scan_cancel).scan()
            finally:
                fs.close()
            if not res.cancelled:
                targets = [f for f in res.files if read_all_headers or f.scheme == SCHEME_A0]
                if targets:
                    self.events.put(("scan_progress", f"FITS headers: 0/{len(targets)}"))
                    read_headers(targets, conn.connect_with_retry, cancel=self.scan_cancel,
                                 placeholders=self.settings.placeholders,
                                 progress=lambda n, t: self.events.put(("scan_progress", f"FITS headers: {n}/{t}"))
                                 if n % 10 == 0 or n == t else None)
            finalize(res)
            res.finished = time.time()
            self.events.put(("scan_done", res, time.time() - t0))
        except Exception as e:
            log.error("Scan failed: %s", e)
            self.events.put(("scan_failed", e))

    def _scan_done(self, res: ScanResult, elapsed: float) -> None:
        self.btn_scan.configure(state="normal")
        self.btn_scan_cancel.configure(state="disabled")
        self._set_conn_state("ok", f"connected · scan finished {datetime.now():%H:%M:%S}")
        self.result = res
        self.files_by_path = {f.remote_path: f for f in res.files}
        self.selected &= set(self.files_by_path)
        self._recompute_status()
        total = sum(f.size for f in res.files)
        summary = (f"{len(res.files)} frames · {len(res.objects)} objects · {res.date_dirs} sessions · {fmt_size(total)} · "
                   f"{len(res.unrecognized)} unrecognized · {elapsed:.1f} s" + (" · CANCELLED" if res.cancelled else ""))
        self.history.log_operation("scan", f"{res.root}: {summary}")
        log.info("Scan %s: %s", res.root, summary)
        for err in res.errors[:20]:
            log.warning("Scan: %s", err)
        self._refresh_last_scan()
        self._refresh_history()
        sessions = sorted({f.session_date for f in res.files}, reverse=True)
        self.session_cb.configure(values=(ALL_SESSIONS, *sessions))
        if self.session_var.get() not in sessions:
            self.session_var.set(ALL_SESSIONS)
        self._fill_objects()
        self._fill_files()
        if not res.files and res.errors:
            messagebox.showwarning("Scan", "No frames found.\n\n" + "\n".join(res.errors[:5]))

    def _recompute_status(self) -> None:
        if not self.result:
            return
        if self.dest_root is None:
            self.status = {f.remote_path: (LocalStatus.NEW, "destination folder not set") for f in self.result.files}
            return
        hist = self.history.downloaded_remote_paths()
        clashes = local_state.name_clashes(self.result.files, self.dest_root)
        self.status = {f.remote_path: local_state.local_status(f, hist, clashes, self.dest_root)
                       for f in self.result.files}

    def _refresh_last_scan(self) -> None:
        row = self.history.last_operation("scan")
        self.scan_info.configure(text=f"Last scan: {fmt_time(row['ts'])} · {row['summary'].split(': ', 1)[-1]}"
                                 if row else "Last scan: —")

    # ================================================================== lists
    def _is_new(self, f) -> bool:
        return self.status.get(f.remote_path, (LocalStatus.NEW,))[0] in (LocalStatus.NEW, LocalStatus.PARTIAL)

    def _fill_objects(self) -> None:
        t = self.obj_tree
        sel = t.selection()
        cur = sel[0] if sel else "__all__"
        t.delete(*t.get_children())
        if not self.result:
            return
        q = self.obj_search.get().strip().lower()
        objs = self.result.objects
        allf = self.result.files
        t.insert("", "end", iid="__all__", text="All objects",
                 values=(len(allf), sum(map(self._is_new, allf)), fmt_size(sum(f.size for f in allf))))
        for name in sorted(objs, key=str.lower):
            if q and q not in name.lower():
                continue
            fl = objs[name]
            t.insert("", "end", iid="o:" + name, text=name,
                     values=(len(fl), sum(map(self._is_new, fl)), fmt_size(sum(f.size for f in fl))))
        if t.exists(cur):
            t.selection_set(cur)
        elif q and len(t.get_children()) > 1:
            t.selection_set(t.get_children()[1])
        else:
            t.selection_set("__all__")
        self.btn_unrec.configure(text=f"Unrecognized: {len(self.result.unrecognized)}")

    def _current_object(self) -> Optional[str]:
        sel = self.obj_tree.selection()
        if sel and sel[0].startswith("o:"):
            return sel[0][2:]
        return None

    def _visible_files(self) -> list:
        if not self.result:
            return []
        obj = self._current_object()
        q = self.obj_search.get().strip().lower()
        types = {t for t, v in self.type_vars.items() if v.get()}
        sess = self.session_var.get()
        fq = self.file_search.get().strip().lower()
        out = []
        for f in self.result.files:
            if obj is not None and f.object_name != obj:
                continue
            if obj is None and q and q not in f.object_name.lower():
                continue
            if (f.frame_type or "?") not in types:
                continue
            if sess != ALL_SESSIONS and f.session_date != sess:
                continue
            if self.only_new.get() and not self._is_new(f):
                continue
            if fq and fq not in f.name.lower():
                continue
            out.append(f)
        return out

    def _file_row(self, f):
        st, detail = self.status.get(f.remote_path, (LocalStatus.NEW, None))
        chk = CHK_ON if f.remote_path in self.selected else CHK_OFF
        notes = list(f.notes)
        if f.object_source == SRC_FITS:
            notes.insert(0, "object from FITS header")
        if f.type_source == SRC_FITS:
            notes.insert(0, "type from FITS header")
        if detail:
            notes.append(detail)
        values = (f.object_name, f.frame_type or "?", f.session_date, fmt_size(f.size), f.filter or "",
                  st.value, " · ".join(notes))
        return f"{chk}  {f.name}", values, (STATUS_TAG[st],)

    def _fill_files(self) -> None:
        t = self.tree
        t.delete(*t.get_children())
        files = self._visible_files()
        self._visible = files
        by_obj = self.group_var.get() == "object"
        groups: dict[str, list] = {}
        for f in files:
            groups.setdefault(f.object_name if by_obj else f.session_date, []).append(f)
        keys = sorted(groups, key=str.lower) if by_obj else sorted(groups, reverse=True)
        open_all = len(files) <= 3000 or len(keys) == 1
        for k in keys:
            fl = sorted(groups[k], key=lambda f: (f.session_date, f.frame_type or "", f.name) if by_obj
                        else (f.object_name.lower(), f.frame_type or "", f.name))
            gid = "g:" + k
            t.insert("", "end", iid=gid, text="", open=open_all, tags=("group",))
            for f in fl:
                text, values, tags = self._file_row(f)
                t.insert(gid, "end", iid=f.remote_path, text=text, values=values, tags=tags)
            self._update_group(gid)
        self._update_sel_info()

    def _update_group(self, gid: str) -> None:
        kids = self.tree.get_children(gid)
        n_sel = sum(1 for k in kids if k in self.selected)
        chk = CHK_ON if kids and n_sel == len(kids) else (CHK_PART if n_sel else CHK_OFF)
        size = sum(self.files_by_path[k].size for k in kids)
        self.tree.item(gid, text=f"{chk}  {gid[2:]}   ({len(kids)} files · {fmt_size(size)})")

    def _refresh_rows(self, paths) -> None:
        groups = set()
        for p in paths:
            if self.tree.exists(p):
                text, values, tags = self._file_row(self.files_by_path[p])
                self.tree.item(p, text=text, values=values, tags=tags)
                groups.add(self.tree.parent(p))
        for g in groups:
            self._update_group(g)
        self._update_sel_info()

    def _update_sel_info(self) -> None:
        n = len(self.selected)
        size = sum(self.files_by_path[p].size for p in self.selected if p in self.files_by_path)
        self.sel_info.configure(text=f"{n} files selected · {fmt_size(size)}" if n else "Nothing selected")
        self.btn_download.configure(state="normal" if n else "disabled")

    # ------------------------------------------------------------------ selection
    def _toggle(self, paths, value: Optional[bool] = None) -> None:
        paths = [p for p in paths if p in self.files_by_path]
        if value is None:
            value = not all(p in self.selected for p in paths)
        for p in paths:
            (self.selected.add if value else self.selected.discard)(p)
        self._refresh_rows(paths)

    def _on_tree_click(self, ev):
        if self.tree.identify_region(ev.x, ev.y) not in ("tree", "cell"):
            return
        if self.tree.identify_column(ev.x) != "#0":
            return
        if self.tree.identify_element(ev.x, ev.y).endswith("indicator"):
            return   # expand/collapse a group
        iid = self.tree.identify_row(ev.y)
        if not iid:
            return
        self._toggle(self.tree.get_children(iid) if iid.startswith("g:") else [iid])
        return "break"

    def _on_space(self, ev):
        paths = []
        for iid in self.tree.selection():
            paths.extend(self.tree.get_children(iid) if iid.startswith("g:") else [iid])
        self._toggle(paths)
        return "break"

    def _select_visible(self, value: bool) -> None:
        self._toggle([f.remote_path for f in self._visible], value)

    def _select_all(self, value: bool) -> None:
        if not self.result:
            return
        self.selected = set(self.files_by_path) if value else set()
        self._refresh_rows(list(self.files_by_path))

    def _select_object(self) -> None:
        if not self.result:
            return
        obj = self._current_object()
        foc = self.tree.focus()
        if obj is None and foc in self.files_by_path:
            obj = self.files_by_path[foc].object_name
        if obj is None:
            messagebox.showinfo("Select object", "Pick an object in the list on the left, or click a file row.")
            return
        self._toggle([f.remote_path for f in self.result.files if f.object_name == obj], True)

    def _select_session(self) -> None:
        if not self.result:
            return
        sess = self.session_var.get()
        foc = self.tree.focus()
        if sess == ALL_SESSIONS:
            if foc in self.files_by_path:
                sess = self.files_by_path[foc].session_date
            elif foc.startswith("g:") and self.group_var.get() == "session":
                sess = foc[2:]
            else:
                messagebox.showinfo("Select session", "Pick a session in the filter, or click a file row from that session.")
                return
        obj = self._current_object()
        self._toggle([f.remote_path for f in self.result.files
                      if f.session_date == sess and (obj is None or f.object_name == obj)], True)

    # ------------------------------------------------------------------ headers on demand
    def read_selected_headers(self) -> None:
        targets = [self.files_by_path[p] for p in self.selected if self.files_by_path[p].fits is None]
        if not targets:
            messagebox.showinfo("FITS headers", "Select files whose headers have not been read yet.")
            return
        self.btn_headers.configure(state="disabled")

        def work():
            try:
                n = read_headers(targets, self._require_conn().connect_with_retry,
                                 placeholders=self.settings.placeholders,
                                 progress=lambda a, b: self.events.put(("scan_progress", f"FITS headers: {a}/{b}")))
                self.events.put(("headers_done", [f.remote_path for f in targets], n))
            except Exception as e:
                self.events.put(("headers_done", [], 0))
                self.events.put(("conn_error", e, self.read_selected_headers))
        threading.Thread(target=work, daemon=True).start()

    def show_unrecognized(self) -> None:
        if not self.result:
            return
        w = tk.Toplevel(self.root, bg=C["bg"])
        w.title("Unrecognized files and folders (not downloadable)")
        w.geometry("1000x450")
        ttk.Label(w, text="These entries have no unambiguous object or are not frame-type folders – they are never downloaded.",
                  style="Muted.TLabel", padding=10).pack(anchor="w")
        fr = ttk.Frame(w, style="Panel.TFrame", padding=8)
        fr.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        tv = tree_with_scroll(fr, columns=("reason",), show="tree headings")
        tv.heading("#0", text="Path", anchor="w")
        tv.heading("reason", text="Reason", anchor="w")
        tv.column("#0", width=520)
        tv.column("reason", width=440)
        for u in self.result.unrecognized:
            tv.insert("", "end", text=u.remote_path, values=(u.reason,))

    # ================================================================== downloading
    def confirm_download(self) -> None:
        if not self.selected:
            return
        if self.dest_root is None:
            messagebox.showinfo("Destination folder", "Choose where frames should be saved first.\n"
                                                     "Frames go to <folder>/<OBJECT>/subs/.")
            if not self._choose_dest():
                return
        root_dir = self.dest_root
        files = [self.files_by_path[p] for p in self.selected]
        buckets: dict[LocalStatus, list] = {}
        for f in files:
            buckets.setdefault(self.status[f.remote_path][0], []).append(f)
        active_paths = {j.file.remote_path for j in self.jobs.values() if j.state not in FINAL_STATES}

        w = tk.Toplevel(self.root, bg=C["bg"])
        w.title("Confirm download")
        w.geometry("900x560")
        w.transient(self.root)
        w.grab_set()
        body = ttk.Frame(w, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Review your selection", style="Title.TLabel").pack(anchor="w")

        vars_ = {
            LocalStatus.PRESENT: tk.BooleanVar(value=False),
            LocalStatus.CONFLICT: tk.BooleanVar(value=False),
            LocalStatus.HISTORY_ONLY: tk.BooleanVar(value=False),
        }
        labels = {
            LocalStatus.PRESENT: "Download again files already present locally (same size) – OVERWRITES them",
            LocalStatus.CONFLICT: "Overwrite local files that differ in size – OVERWRITES them",
            LocalStatus.HISTORY_ONLY: "Download again files from history that are no longer on disk",
        }
        opts = ttk.Frame(body)
        opts.pack(fill="x", pady=(10, 6))
        for st, v in vars_.items():
            n = len(buckets.get(st, []))
            if n:
                ttk.Checkbutton(opts, text=f"{labels[st]}  ({n})", variable=v,
                                command=lambda: refresh()).pack(anchor="w")
        clash = buckets.get(LocalStatus.NAME_CLASH, [])
        if clash:
            ttk.Label(opts, text=f"⚠ {len(clash)} files skipped: name clash in the subs folder or a name that is not "
                                 "valid on Windows (names are never changed automatically).",
                      foreground=C["err"]).pack(anchor="w", pady=(4, 0))
        fr = ttk.Frame(body, style="Panel.TFrame", padding=8)
        fr.pack(fill="both", expand=True, pady=6)
        tv = tree_with_scroll(fr, columns=("n", "size", "skip", "dest"), show="tree headings")
        tv.heading("#0", text="Object", anchor="w")
        for c, t, wd in (("n", "To download", 90), ("size", "Size", 90), ("skip", "Skipped", 80),
                         ("dest", "Destination folder", 420)):
            tv.heading(c, text=t, anchor="w")
            tv.column(c, width=wd, anchor="w")
        tv.column("#0", width=150)
        summary = ttk.Label(body, text="")
        summary.pack(anchor="w", pady=(6, 0))
        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(12, 0))
        state = {}

        def plan():
            todo, skipped = [], []
            for f in files:
                st = self.status[f.remote_path][0]
                if f.remote_path in active_paths or st == LocalStatus.NAME_CLASH:
                    skipped.append(f)
                elif st in vars_ and not vars_[st].get():
                    skipped.append(f)
                else:
                    todo.append(f)
            return todo, skipped

        def refresh():
            todo, skipped = plan()
            state["todo"] = todo
            tv.delete(*tv.get_children())
            per = {}
            for f in todo:
                per.setdefault(f.object_name, [0, 0, 0])
                per[f.object_name][0] += 1
                per[f.object_name][1] += f.size
            for f in skipped:
                per.setdefault(f.object_name, [0, 0, 0])[2] += 1
            for obj in sorted(per, key=str.lower):
                n, size, sk = per[obj]
                try:
                    d = local_state.dest_dir(obj, root_dir)
                    where = f"{d}" + ("" if d.parent.exists() else "   (new folder)")
                except InvalidName:
                    where = "— invalid folder name —"
                tv.insert("", "end", text=obj, values=(n, fmt_size(size), sk, where))
            total = sum(f.size for f in todo)
            queued = len(active_paths & self.selected)
            summary.configure(text=f"To download: {len(todo)} files · {fmt_size(total)}    Skipped: {len(skipped)}"
                              + (f" (incl. {queued} already queued)" if queued else ""))
            ok_btn.configure(text=f"Download {len(todo)} files", state="normal" if todo else "disabled")

        def go():
            todo = state.get("todo", [])
            overwrite_states = {st for st in (LocalStatus.PRESENT, LocalStatus.CONFLICT) if vars_[st].get()}
            n_over = sum(1 for f in todo if self.status[f.remote_path][0] in overwrite_states)
            if n_over and not messagebox.askyesno(
                    "Overwrite", f"{n_over} existing local files will be overwritten.\nContinue?",
                    icon="warning", parent=w):
                return
            w.grab_release()
            w.destroy()
            self.start_download(todo, overwrite_states)

        ttk.Button(btns, text="Cancel", command=w.destroy).pack(side="right")
        ok_btn = ttk.Button(btns, text="Download", style="Accent.TButton", command=go)
        ok_btn.pack(side="right", padx=8)
        refresh()

    def start_download(self, files: list, overwrite_states: set) -> None:
        if self.dest_root is None:
            return
        jobs = []
        for f in sorted(files, key=lambda f: (f.object_name, f.session_date, f.name)):
            try:
                dest = local_state.dest_path(f.object_name, f.name, self.dest_root)
            except InvalidName as e:
                log.error("%s", e)
                continue
            overwrite = self.status[f.remote_path][0] in overwrite_states and dest.exists()
            jobs.append(TransferJob(f, dest, overwrite=overwrite))
        if not jobs:
            return
        if all(j.state in FINAL_STATES for j in self.jobs.values()):
            self.batch_ids = set()
        for j in jobs:
            self.jobs[j.id] = j
            self.batch_ids.add(j.id)
            self.qtree.insert("", "end", iid=str(j.id), text=j.file.name)
        self.history.log_operation("download", f"queued {len(jobs)} files · {fmt_size(sum(j.total for j in jobs))}")
        log.info("Download started: %d files", len(jobs))
        self.btn_stop.configure(state="normal")
        self.notebook.select(0)
        self.manager.submit(jobs)
        self._update_queue_info()

    def stop_all(self) -> None:
        self.manager.cancel_all()
        log.info("Stopping all transfers (partial files are kept for resume)")

    def cancel_selected_jobs(self) -> None:
        for iid in self.qtree.selection():
            self.manager.cancel_job(int(iid))

    def retry_failed(self) -> None:
        failed = [j for j in self.jobs.values() if j.state in (ERROR, CANCELLED)]
        if not failed:
            return
        for j in failed:
            if self.qtree.exists(str(j.id)):
                self.qtree.delete(str(j.id))
            self.jobs.pop(j.id, None)
            self.batch_ids.discard(j.id)
        self._recompute_status()
        self.start_download([j.file for j in failed],
                            {LocalStatus.PRESENT, LocalStatus.CONFLICT} if any(j.overwrite for j in failed) else set())

    def clear_finished(self) -> None:
        for j in list(self.jobs.values()):
            if j.state in FINAL_STATES:
                if self.qtree.exists(str(j.id)):
                    self.qtree.delete(str(j.id))
                del self.jobs[j.id]
                self.batch_ids.discard(j.id)
        self._update_queue_info()

    def _on_job(self, job: TransferJob) -> None:
        self.jobs[job.id] = job
        iid = str(job.id)
        if not self.qtree.exists(iid):
            return
        frac = (job.done / job.total) if job.total else (1.0 if job.state == DONE else 0.0)
        self.qtree.item(iid, values=(job.file.object_name, job.state, bar(frac),
                                     f"{fmt_size(job.speed)}/s" if job.state == ACTIVE and job.speed else "",
                                     fmt_size(job.total), job.message), tags=(job.state,))
        if job.state in (DONE, SKIPPED, CANCELLED, ERROR) and self.dest_root and job.file.remote_path in self.files_by_path:
            hist = self.history.downloaded_remote_paths() if job.state == DONE else {}
            self.status[job.file.remote_path] = local_state.local_status(job.file, hist, set(), self.dest_root)
            self._refresh_rows([job.file.remote_path])

    def _update_queue_info(self) -> None:
        batch = [self.jobs[i] for i in self.batch_ids if i in self.jobs]
        if not batch:
            self.queue_info.configure(text="No transfers")
            self.global_pb["value"] = 0
            return
        total = sum(j.total for j in batch if j.state != SKIPPED)
        done_b = sum(j.total if j.state == DONE else j.done for j in batch if j.state != SKIPPED)
        n_done = sum(1 for j in batch if j.state == DONE)
        n_err = sum(1 for j in batch if j.state == ERROR)
        n_skip = sum(1 for j in batch if j.state == SKIPPED)
        n_act = sum(1 for j in batch if j.state in (ACTIVE, VERIFYING))
        speed = sum(j.speed for j in batch if j.state == ACTIVE)
        remaining = sum(max(0, j.total - j.done) for j in batch if j.state in (QUEUED, ACTIVE, VERIFYING))
        eta = f" · ~{int(remaining / speed // 60)} min {int(remaining / speed % 60)} s" if speed > 0 and remaining else ""
        self.global_pb["value"] = 1000 * done_b / max(1, total)
        self.queue_info.configure(
            text=f"Downloaded {n_done}/{len(batch) - n_skip} · active {n_act} · {fmt_size(speed)}/s · "
                 f"{fmt_size(remaining)} left{eta} · skipped {n_skip} · errors {n_err}")

    def _on_idle(self) -> None:
        self.btn_stop.configure(state="disabled")
        batch = [self.jobs[i] for i in self.batch_ids if i in self.jobs]
        count = lambda s: sum(1 for j in batch if j.state == s)
        msg = (f"finished: {count(DONE)} downloaded, {count(SKIPPED)} skipped, "
               f"{count(CANCELLED)} cancelled, {count(ERROR)} errors")
        self.history.log_operation("download", msg)
        log.info("Download %s", msg)
        self._refresh_history()
        self._fill_objects()
        self._update_queue_info()

    # ================================================================== history
    def _refresh_history(self) -> None:
        q = self.hist_search.get().strip().lower()
        self.htree.delete(*self.htree.get_children())
        for r in self.history.list_downloads():
            if q and q not in (r["object"] + r["filename"] + (r["session_date"] or "")).lower():
                continue
            self.htree.insert("", "end", text=fmt_time(r["downloaded_at"]),
                              values=(r["object"], r["session_date"], r["frame_type"] or "?", fmt_size(r["size"] or 0),
                                      r["verified"], r["remote_path"], r["local_path"]))
        self.optree.delete(*self.optree.get_children())
        for r in self.history.list_operations():
            self.optree.insert("", "end", text=f"{fmt_time(r['ts'])}  {r['kind']}", values=(r["summary"],))

    # ================================================================== event loop
    def _pump(self) -> None:
        dirty_queue = False
        try:
            for _ in range(2000):
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "log":
                    self._append_log(ev[1], ev[2])
                elif kind == "conn":
                    self._set_conn_state(ev[1], ev[2])
                    if ev[1] == "ok":
                        self.btn_test.configure(state="normal")
                elif kind == "conn_error":
                    self._handle_conn_error(ev[1], ev[2])
                elif kind == "scan_progress":
                    self.scan_info.configure(text=ev[1])
                elif kind == "scan_done":
                    self._scan_done(ev[1], ev[2])
                elif kind == "scan_failed":
                    self.btn_scan.configure(state="normal")
                    self.btn_scan_cancel.configure(state="disabled")
                    self._refresh_last_scan()
                    self._handle_conn_error(ev[1], self.start_scan)
                elif kind == "headers_done":
                    self.btn_headers.configure(state="normal")
                    self._refresh_rows(ev[1])
                    if ev[1]:
                        self.scan_info.configure(text=f"Read {ev[2]} FITS headers")
                elif kind == "dl":
                    if ev[1] == "job":
                        self._on_job(ev[2])
                        dirty_queue = True
                    elif ev[1] == "idle":
                        self._on_idle()
        except queue.Empty:
            pass
        if dirty_queue:
            self._update_queue_info()
        self.root.after(100, self._pump)

    def _append_log(self, line: str, level: int) -> None:
        self.log_text.configure(state="normal")
        tag = "err" if level >= logging.ERROR else ("warn" if level >= logging.WARNING else "")
        self.log_text.insert("end", line + "\n", tag)
        if int(self.log_text.index("end-1c").split(".")[0]) > 5000:
            self.log_text.delete("1.0", "1000.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_close(self) -> None:
        if self.manager.running:
            if not messagebox.askyesno("Quit", "Downloads are running. Stop them and quit?\n"
                                               "Partial files are kept so downloads can resume."):
                return
            self.manager.cancel_all()
            self.manager.wait(5)
        self.scan_cancel.set()
        self.history.close()
        self.root.destroy()


def main() -> None:
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[logging.FileHandler(config.LOG_FILE, encoding="utf-8")],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

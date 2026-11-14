# SubPull architecture

## GUI technology decision (v0.2)

SubPull 0.1 used Tkinter/ttk. Version 0.2 replaces it with **PySide6 (Qt Widgets)**.

| Criterion | PySide6 (Qt Widgets) | PyQt6 | CustomTkinter | Improved Tk/ttk |
|---|---|---|---|---|
| Modern desktop UI | Full stylesheet + palette system, delegates for custom cells | Same as PySide6 | Modern-looking widgets, few complex ones | Limited theming, no per-cell painting in `ttk.Treeview` |
| Windows / macOS / Linux | Yes | Yes | Yes | Yes |
| High-DPI / 4K, fractional scaling | Per-monitor DPI built in (Qt 6), fractional factors pass through | Same | Own scaling layer on top of Tk, uneven | Tk scaling is global and coarse; fonts/images often blurry |
| Large lists (thousands of frames) | Model/view with virtualized rows, sorting, tri-state checks | Same | No virtualized tree; widget-per-row approaches | `ttk.Treeview` holds all rows in Tcl, no custom painting |
| Threads | `QThread`, signals/slots with queued cross-thread delivery | Same | Manual `after()` polling | Manual `after()` polling |
| Packaging | pip wheel; PyInstaller/Nuitka supported | Same | Small | Smallest (stdlib) |
| Licensing | LGPLv3 – fine for an MIT app that links dynamically | GPLv3 or commercial – would force GPL on SubPull | MIT | Stdlib |
| Dependency footprint | `PySide6-Essentials` ≈ 200 MB installed | Similar | Small | None |
| Long-term maintenance | Official Qt project binding | Third-party | Single maintainer | Stable but stagnant |

PySide6 wins on everything that matters for this app (DPI correctness, model/view for large
datasets, thread-safe signals) and keeps the MIT license viable thanks to LGPL. PyQt6 is
technically equivalent but its GPL license is incompatible with an MIT project. The size cost
is mitigated by depending on `PySide6-Essentials` (no WebEngine, 3D, multimedia…), not the
full `PySide6` meta-package. QML was not used: Qt Widgets fit a data-dense desktop tool better
and keep everything in Python.

## Layers

```
subpull/                 core – plain Python, never imports Qt
  config.py              constants, per-OS paths, Settings (settings.json)
  filezilla.py           read-only FileZilla Site Manager / host keys
  connection.py          the only network module (read-only SFTP RemoteFS)
  classify.py            dates, frame types, file kinds
  fits_header.py         remote FITS header reading (header blocks only)
  scanner.py             directory layouts A / B / A0, FrameFile records
  local_state.py         destination paths, duplicate / conflict detection
  history.py             SQLite history (downloads, partials, operations)
  downloader.py          transfer workers, resume, verification, overwrite rules
  workflow.py            filtering, grouping, download planning, queue statistics

subpull/i18n/            localization – plain Python, no Qt
  manager.py             t(key, **values), language selection, English fallback
  plurals.py             plural categories per language
  locale_file.py         reading a locale file (shared by app, validator, statistics)
  validate.py            python -m subpull.i18n.validate
  stats.py               python -m subpull.i18n.stats
  locales/<code>.json    one file per language; en.json is the source

subpull/ui/              Qt Widgets front end (PySide6)
  app.py                 QApplication setup, DPI policy, logging bridge, entry point
  controller.py          single bridge to the core: state, background tasks, signals
  workers.py             QThread task runner, logging → GUI bridge
  language.py            system language, layout direction, Qt's own translations
  models.py              item models (frame tree, objects, queue, history, log)
  widgets.py             reusable widgets and painting delegates
  theme.py / icons.py    color tokens, stylesheet, vector icons
  main_window.py         sidebar, top bar, page stack
  library.py             objects list, frame browser, details panel
  queue_view.py          transfers
  history_view.py        download history and activity
  logs_view.py           searchable log
  settings_view.py       grouped settings
  dialogs.py             download confirmation, unrecognized items, error prompts
```

Rules:

* The core has no Qt imports and is fully covered by `tests/test_core.py` and `tests/test_workflow.py`.
* Views never touch the network or the filesystem state directly; they call the `Controller`.
* Worker threads never touch widgets. Scans and header reads run in `QThread`s
  (`TaskThread`); download workers report through a signal, which Qt delivers to the GUI
  thread through a queued connection.
* Item views use models, so thousands of frames are painted on demand rather than created as widgets.

## Localization

Every user-facing text has a stable key in `subpull/i18n/locales/en.json`; the other files in that
folder are community translations with the same keys (contributor guide: `translations/README.md`).
The folder is the single source of truth and is shipped as package data – nothing is fetched at
run time.

* **Lookup:** `t("actions.download")`, `t("count.files", count=5)`. Missing or broken translations
  fall back to English, unknown keys come back as the key; `t()` never raises. A value may be an
  object of plural forms (`one`, `few`, `many`, `other`…) selected by `count`.
  `SUBPULL_I18N_DEBUG=1` logs every fallback.
* **Stable identifiers stay untranslated:** enum values (`LocalStatus`, transfer states, FITS
  status, history event kinds) are stored and compared as before and mapped to a key only for
  display (`status_label()`, `t(f"transfer.state.{state}")`).
* **Runtime switching:** `Controller.set_language()` saves the setting, activates the language
  and emits `languageChanged`. `MainWindow.retranslate()` then calls `retranslate()` on the
  sidebar, top bar and every page. Static texts are registered once with `Texts` (`widgets.py`),
  which re-applies them to the existing widgets; models emit `headerDataChanged`; menus are
  rebuilt; dynamic labels re-run their normal refresh. No window or page is recreated, so
  selection, scan results and running transfers are untouched.
* **Texts recorded with an event** – transfer messages, notes on scanned files, the activity
  history and exception messages – are written in the language active at that moment and are not
  translated afterwards. The log file mixes them with English diagnostic messages by design:
  developer-facing log lines are not translated so that bug reports stay readable.
* **Right-to-left:** `_meta.direction` of the locale file sets the application's layout direction.
* **Qt's own texts** (text-field context menus, the non-native file dialog) come from the
  `qtbase_<code>.qm` files that ship with Qt, when one exists for the language.
* **Adding a language** needs no code change: a valid `<code>.json` in the folder appears in
  Settings → Language. Completion percentages are calculated, never stored.

## Safety guarantees (unchanged)

* Manual only: no timers, schedules, background scans or automatic downloads.
* Read-only on the server: `RemoteFS` exposes `listdir`, `stat`, `open_read` (mode `rb`) and an optional checksum query – nothing else.
* Files go to `<destination>/<OBJECT>/subs/<original name>`; names are never changed.
* Existing local files are never overwritten unless the user opts in for that file category
  in the confirmation dialog **and** confirms the overwrite warning.
* Partial downloads (`*.subpull-part`) resume when the remote size and mtime are unchanged.
* Host keys are verified against FileZilla's trusted keys and `known_hosts`.

## High-DPI

Qt 6 handles per-monitor scaling; `app.py` sets the rounding policy to `PassThrough` so
125 % / 150 % / 175 % are not rounded. All sizes in code derive from font metrics, icons are
rendered from SVG at several resolutions, and layouts use splitters and stretch factors instead
of fixed pixel geometry.

## Packaging

`pip install .` installs the `subpull` GUI command. Windows releases are built by
`.github/workflows/release.yml` when a `v*` tag is pushed: tests, a PyInstaller one-folder build
(`packaging/subpull.spec`), an Inno Setup per-user installer (`packaging/subpull.iss`), a portable
zip and `SHA256SUMS.txt`, published as a GitHub release. Local build:

```bash
pip install pyinstaller
pyinstaller packaging/subpull.spec --noconfirm --distpath build/dist --workpath build/work
iscc /DAppVersion=1.0.0 packaging/subpull.iss
```

The app icon is generated from the logo with `python packaging/make_icon.py`.

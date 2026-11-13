<p align="center">
  <img src="docs/assets/logo-128.png" width="96" alt="SubPull logo">
</p>

<h1 align="center">SubPull</h1>

A small desktop tool for **manually** pulling astrophotography frames from an SFTP server
(remote observatory, imaging PC, NAS…) into a tidy local library:

```
<destination>/<OBJECT>/subs/<original file name>
```

It scans the server only when you click **Scan**, shows every frame grouped by object,
session or frame type, and downloads only what you select and confirm. No background sync, no
schedule, nothing is ever written to, renamed on, or deleted from the server.

Built with Python and Qt (PySide6): a dark, high-DPI-aware desktop UI for Windows, macOS and Linux.

## Features

**Interface**

- **Library** – objects list with frame/new counts, a virtualized frame browser grouped by object,
  session or type, colored frame-type badges, filters (type, session, new only, search),
  tri-state checkbox selection, sortable and configurable columns, and a details panel with the
  FITS header, local status, destination and server path of the current frame.
- **Transfers** – overall progress, speed, remaining size, time left, per-file progress bars and
  states (queued, downloading, verifying, done, skipped, error, cancelled); stop, cancel, retry.
- **History** – every downloaded file (searchable) plus an activity list of scans, batches and errors.
- **Activity log** – searchable log with severity filter.
- **Settings** – connection (FileZilla site, server, user, auth method, test), library location,
  scanning, transfers and data folders, each in its own section.
- Keyboard: `F5` scan, `Ctrl+1…5` pages, `Ctrl+F` search files, `Space` toggles highlighted rows.

**Core**

- Reuses an existing **FileZilla Site Manager** entry (SFTP, key file or saved password) and the
  host keys FileZilla already trusts – no second credential store.
- Understands several directory layouts at the same time (see below).
- Reads FITS headers (`OBJECT`, `IMAGETYP`, `FILTER`) **remotely** – only the header blocks are
  transferred – to fill in missing information and flag inconsistencies.
- Filter by object, session, frame type; search; select per file, object, session or all.
- Parallel downloads with per-file and overall progress, speed and remaining size.
- Interrupted transfers resume where they stopped; every file is verified by size
  (and by server-side SHA-256 when the server supports the SFTP `check-file` extension).
- Never overwrites a local file without an explicit confirmation; flags duplicates by name and size.
- Download history in a local SQLite database (nothing extra is written next to your frames).

## Supported layouts

The layout is detected per object directory, so different layouts can coexist on one server.

| Scheme | Layout | Object from | Frame type from |
|---|---|---|---|
| A  | `DATE/OBJECT/TYPE/file` | directory | directory (`LIGHT`, `lights`, `DARK`, `FLAT`, `biases`, `DARKFLAT`…) |
| B  | `DATE/OBJECT/TYPE_xxx.fits` | directory | file prefix (`LIGHT_`, `DARK_`, `FLAT_`, `BIAS_`, `DARKFLAT_`) |
| A0 | `DATE/TYPE/file` | FITS `OBJECT` keyword | directory |

`DATE` is a folder named like `2025-08-15` (also `20250815`, `2025_08_15`); it can sit at any
depth below the remote folder you scan (up to 4 levels). Files whose object cannot be determined
(for example `OBJECT = 'Target'`, the N.I.N.A. default) are listed as *Unrecognized* and never
downloaded.

## Install

### Windows installer

Download `SubPull-<version>-Setup.exe` from the
[latest release](https://github.com/kopersyn/subpull/releases/latest) and run it. It installs
for the current user only (no administrator rights) and adds a Start-menu entry; a portable zip
is attached as well. The builds are not code-signed, so Windows SmartScreen may ask you to
confirm (*More info → Run anyway*). Checksums are in `SHA256SUMS.txt`.

### From source (Windows, macOS, Linux)

Requires Python 3.10+. Dependencies (`paramiko`, `PySide6-Essentials`) are installed by pip.
On minimal Linux systems Qt may need `libegl1 libxkbcommon0 libfontconfig1`.

```bash
pip install git+https://github.com/kopersyn/subpull
subpull
```

Or from a checkout:

```bash
python -m venv .venv
.venv/bin/pip install -e .        # Windows: .venv\Scripts\pip install -e .
.venv/bin/subpull                 # Windows: run.bat
```

## First run

1. Create an SFTP site in FileZilla's Site Manager (key file or saved password) and connect to it
   once so FileZilla stores the server's host key.
2. Start SubPull. The first SFTP site is preselected; switch sites from the site button in the
   toolbar or in **Settings → Connection**.
3. Choose your library folder from the destination button (top right) or the banner in the Library.
4. Enter the remote folder in the toolbar and press **Scan** (`F5`).
5. Tick frames, objects or sessions and press **Download**. Review the plan – destinations,
   skipped files and any overwrite – and confirm.

## Settings and data

| What | Windows | macOS | Linux |
|---|---|---|---|
| Settings, history, log | `%LOCALAPPDATA%\SubPull` | `~/Library/Application Support/SubPull` | `~/.local/share/subpull` |
| FileZilla sites (read-only) | `%APPDATA%\FileZilla` | `~/.config/filezilla` | `~/.config/filezilla` |

`ui.ini` stores window layout (columns, splitters, sidebar). `settings.json` keys: `profile_name`, `dest_root`, `source_dir`, `read_fits`,
`parallel_transfers`, `placeholder_objects` (OBJECT values treated as "unknown").

While a file downloads it is named `<file>.subpull-part`; it is renamed to the original name after
verification. A leftover `.subpull-part` is resumed the next time you download that file.

## Development

```bash
pip install -e .
python -m unittest discover -s tests -t .
```

The tests use a local directory as a fake server, so they need no network; the Qt tests run
headless (`QT_QPA_PLATFORM=offscreen`). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the
module layout, the GUI technology decision and packaging notes (PyInstaller).

## License

MIT – see [LICENSE](LICENSE).

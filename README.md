# SubPull

A small desktop tool for **manually** pulling astrophotography frames from an SFTP server
(remote observatory, imaging PC, NAS…) into a tidy local library:

```
<destination>/<OBJECT>/subs/<original file name>
```

It scans the server only when you click **Scan server**, shows every frame grouped by
object and session, and downloads only what you select. No background sync, no schedule,
nothing is ever written to, renamed on, or deleted from the server.

## Features

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

Requires Python 3.10+ with Tk (included in the python.org installers; on Debian/Ubuntu
`sudo apt install python3-tk`).

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
2. Start SubPull and pick the site in the **Site** box (the first SFTP site is preselected).
3. Click **Change…** next to the destination and choose your library folder.
4. Enter the remote folder to scan and click **Scan server**.

## Settings and data

| What | Windows | macOS | Linux |
|---|---|---|---|
| Settings, history, log | `%LOCALAPPDATA%\SubPull` | `~/Library/Application Support/SubPull` | `~/.local/share/subpull` |
| FileZilla sites (read-only) | `%APPDATA%\FileZilla` | `~/.config/filezilla` | `~/.config/filezilla` |

`settings.json` keys: `profile_name`, `dest_root`, `source_dir`, `read_fits`,
`parallel_transfers`, `placeholder_objects` (OBJECT values treated as "unknown").

While a file downloads it is named `<file>.subpull-part`; it is renamed to the original name after
verification. A leftover `.subpull-part` is resumed the next time you download that file.

## Development

```bash
pip install -e .
python -m unittest discover -s tests -t .
```

The tests use a local directory as a fake server, so they need no network.
The code is split into `connection` (all network access), `scanner`/`classify`/`fits_header`
(recognition), `downloader` (transfers), `local_state`/`history` (local side) and `gui`.

## License

MIT – see [LICENSE](LICENSE).

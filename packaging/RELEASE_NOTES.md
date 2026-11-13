First stable release.

**Download:** `SubPull-1.0.0-Setup.exe` (Windows 10/11, 64-bit, installs per user, no admin rights)
or the portable zip. Not code-signed – SmartScreen may ask you to confirm (*More info → Run anyway*).
Other platforms: `pip install git+https://github.com/kopersyn/subpull` (Python 3.10+).

### Highlights
- Modern Qt (PySide6) interface: library browser with per-object overview, grouping by object /
  session / frame type, filters and search, transfer queue, history, activity log and settings.
- Correct on high-DPI displays (100–200 % scaling) and resizable down to small windows.
- Reuses a FileZilla Site Manager entry and its trusted host keys; read-only on the server.
- Multiple directory layouts detected per object; FITS headers read remotely.
- Parallel downloads with resume and verification; never overwrites local files without
  explicit confirmation; local download history.

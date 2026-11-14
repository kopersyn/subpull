SubPull 1.0.1 – now in Polish, and ready for your language.

**Download:** `SubPull-1.0.1-Setup.exe` (Windows 10/11, 64-bit, installs per user, no admin rights)
or the portable zip. Not code-signed – SmartScreen may ask you to confirm (*More info → Run anyway*).
Other platforms: `pip install git+https://github.com/kopersyn/subpull` (Python 3.10+).

### New
- **Interface languages:** English and Polish (*Polski*). Choose in **Settings → Language**; the
  default *Automatic* follows the system language and falls back to English. The language changes
  immediately, without a restart.
- **Community translations:** every language is one JSON file that can be translated directly on
  GitHub – see [Translate SubPull](https://github.com/kopersyn/subpull/blob/main/translations/README.md).
  Pull requests are validated automatically; partial translations fall back to English.
- All translations are bundled with the application; nothing is downloaded.

### Unchanged
- Read-only on the server, manual-only workflow, downloads only after confirmation, no silent
  overwrites. Existing settings and download history are kept; `settings.json` gains a `language` key.

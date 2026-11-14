# Contributing to SubPull

Thank you for your interest in SubPull! Bug reports, ideas, translations and code are all welcome.

## Localization (translations)

**You do not need to be a programmer to translate SubPull.** Each language is a single text file that can be
edited in the browser, and every contribution is checked automatically.

- 🌍 **Step-by-step guide:** [translations/README.md](translations/README.md)
- Language files: [`subpull/i18n/locales`](subpull/i18n/locales) – `en.json` is the English source and the template
- Report a wrong or awkward translation: [open a translation issue](https://github.com/kopersyn/subpull/issues/new?template=translation.yml)

The short version:

1. Copy `en.json` to `<your language code>.json` (for example `de.json`, `pt-BR.json`).
2. Fill in the `_meta` header and translate the texts on the right-hand side. Keys and `{placeholders}` stay as they are.
3. Open a pull request. The **translations** check validates the file; missing texts are fine and fall back to English.

Partial translations and small corrections are just as welcome as complete new languages.

For developers: user-facing texts never go into the code. Add a key to `en.json` and use `t("your.key", name=value)`
from `subpull.i18n` (in widgets, bind it with `Texts` from `subpull/ui/widgets.py` so it follows a language change).
See the [architecture notes](docs/ARCHITECTURE.md#localization).

## Bugs and ideas

Open an [issue](https://github.com/kopersyn/subpull/issues). For bugs, include your operating system, the SubPull
version (Settings → Data) and the relevant lines of the Activity log.

## Code

```bash
pip install -e .
python -m unittest discover -s tests -t .
python -m subpull.i18n.validate
```

The tests need no network and no server (`QT_QPA_PLATFORM=offscreen` runs the Qt tests headless).
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) describes the layers and the safety guarantees – SubPull never writes
to the server and never downloads without an explicit confirmation; please keep it that way.

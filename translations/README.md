# Translate SubPull

SubPull is translated by its users. If you speak a language that is missing or incomplete, you can
add it **right here on GitHub, in your browser** – you do not need to know Python or install anything.

Every language is one text file in [`subpull/i18n/locales`](../subpull/i18n/locales). That folder is
the only place translations live; the application bundles those files and never downloads anything.

## Supported languages

<!-- translation-status:start -->
| Language | Code | Translated | Status | Maintainers |
|---|---|---|---|---|
| English | `en` | 100% (343/343) | Source language | kopersyn |
| Polski (Polish) | `pl` | 100% (343/343) | Complete |  |
<!-- translation-status:end -->

The numbers are calculated from the files (`python -m subpull.i18n.stats`). Texts that are not
translated yet are simply shown in English.

Quick links:

- **Improve an existing translation:** open the language file in
  [`subpull/i18n/locales`](https://github.com/kopersyn/subpull/tree/main/subpull/i18n/locales)
  and click the pencil icon (for example [edit Polish](https://github.com/kopersyn/subpull/edit/main/subpull/i18n/locales/pl.json)).
- **Start a new language:** follow the steps below.
- **Report a mistake without fixing it yourself:**
  [open a translation issue](https://github.com/kopersyn/subpull/issues/new?template=translation.yml).

## Add a new language – step by step

### 1. Choose the language code

The file name is the language code, in the standard [BCP 47](https://en.wikipedia.org/wiki/IETF_language_tag) form:

| Use | Examples |
|---|---|
| Two-letter [ISO 639-1](https://en.wikipedia.org/wiki/List_of_ISO_639_language_codes) code | `de` (German), `fr` (French), `es` (Spanish), `ja` (Japanese) |
| Add a region only when the variant really differs | `pt-BR` (Brazilian Portuguese), `en-GB` |
| Add a script when a language is written in several | `zh-Hans` (Simplified Chinese), `zh-Hant`, `sr-Latn` |

Lowercase language, capitalised script, uppercase region, joined with hyphens.

### 2. Copy the English file

English is the template – it contains every text of the application.

1. Open the [English file as plain text](https://raw.githubusercontent.com/kopersyn/subpull/main/subpull/i18n/locales/en.json),
   select everything and copy it.
2. Open the [new-file editor](https://github.com/kopersyn/subpull/new/main?filename=subpull/i18n/locales/xx.json).
   GitHub asks you to *fork* the repository first – accept; that gives you your own copy to work in.
3. Change `xx.json` in the file-name box to your code (for example `de.json`) and paste the text.

### 3. Fill in the header

At the top of the file:

```json
"_meta": {
    "code": "de",
    "name": "Deutsch",
    "english_name": "German",
    "direction": "ltr",
    "maintainers": ["your-github-name"]
},
```

| Field | Meaning |
|---|---|
| `code` | The same code as the file name. |
| `name` | The language's name **in that language** – this is what users see in the settings. |
| `english_name` | Its name in English. |
| `direction` | `ltr` for left-to-right languages, `rtl` for right-to-left ones (Arabic, Hebrew, Persian…). |
| `maintainers` | Optional: who to ask about this translation. |

You never write a completion percentage – it is counted automatically.

### 4. Translate the texts

Each line has a **key** on the left and the **text** on the right. Translate only the text on the right:

```json
"actions.download": "Download",          ←  English
"actions.download": "Herunterladen",     ←  German
```

Rules – the automatic check enforces the first four:

1. **Never change, rename, add or remove keys** (the part before the colon). The application finds texts by key.
2. **Keep placeholders exactly as they are.** Words in curly braces such as `{count}`, `{path}` or `{size}` are
   replaced by real values. You may move them within the sentence, but not translate, rename or delete them:
   `"Connecting to {server}…"` → `"Verbindung zu {server} wird hergestellt…"`.
3. **Keep the punctuation of the file:** every text in straight double quotes `"…"`, a comma after every line
   except the last one. Inside a text, write a double quote as `\"` and a line break as `\n` (keep the `\n`
   that English has). Typographic quotes such as „…“ or «…» can be used freely.
4. **Not sure about a text? Make it empty** (`""`) or delete the line. SubPull shows English for it and the
   progress number stays honest. A partial translation is welcome – others can continue it.
5. Keep `…` (one character) where English has it, and keep texts about as long as the English ones where you
   can – buttons and table headers have limited room.
6. Do not translate: **SubPull**, FileZilla, SFTP, SSH, SHA-256, FITS, the FITS keywords `OBJECT`, `IMAGETYP`,
   `FILTER`, `EXPTIME`, the folder name `subs`, and file names such as `known_hosts`.

#### Texts that depend on a number

Some texts have several forms because the wording depends on a number:

```json
"count.files": {"one": "{count} file", "other": "{count} files"},
```

Write one form for each plural category your language uses. Polish, for example, needs four:

```json
"count.files": {"one": "{count} plik", "few": "{count} pliki", "many": "{count} plików", "other": "{count} pliku"},
```

| Languages | Forms to write |
|---|---|
| English, German, Dutch, Spanish, Italian, Swedish, Greek, Hungarian, Turkish and most others | `one`, `other` |
| French, Portuguese, Hindi, Persian | `one` (used for 0 and 1), `other` |
| Polish, Russian, Ukrainian, Belarusian | `one`, `few`, `many`, `other` |
| Czech, Slovak, Serbian, Croatian, Bosnian, Lithuanian, Romanian | `one`, `few`, `other` |
| Slovenian | `one`, `two`, `few`, `other` |
| Latvian | `zero`, `one`, `other` |
| Hebrew | `one`, `two`, `other` |
| Arabic | `zero`, `one`, `two`, `few`, `many`, `other` |
| Japanese, Chinese, Korean, Vietnamese, Thai, Indonesian, Malay | `other` only – or simply one text in quotes |

`other` is always required; it is also used for fractions. Any text with a `{count}` placeholder may be given
plural forms, even where English uses a single text (see `count.new` in the Polish file). If your language is
missing from the table it follows the English rule – mention it in your pull request if that is wrong.

#### What some texts mean

| Keys | Context |
|---|---|
| `format.decimal_separator` | Not a text: the character between whole and fractional part of a number – `.` or `,`. |
| `units.*`, `duration.*` | File sizes, speeds and durations. Change them only if your language writes units differently (French `Ko`, `Mo`). |
| `count.*` | Short counters joined with “·”, e.g. “5 frames · 3 objects · 2 sessions”. Keep them short and lowercase. |
| `nav.*` | The five pages in the sidebar; also used as page titles. |
| `…site…` | A *site* is an entry in FileZilla's Site Manager (a saved server). Use the word FileZilla uses in your language. |
| *frame* | One image file from the camera. *Sub* / `subs` is astrophotography slang for the same; `subs` is a folder name – keep it. |
| *object* | The astronomical target (M31, NGC 7000), **not** a programming term. |
| *session* | One night of imaging – a dated folder such as `2025-08-15`. |
| `frame_type.*` | Light, Dark, Flat, Bias, Dark flat: calibration terms. Many communities keep the English words – use what astrophotographers in your language say. The coloured `LIGHT` / `DARK` badges are file-format values and are not translated. |
| `column.local`, `status.local.*` | “Local” is about the copy on the user's own disk: `new` = not downloaded yet, `already local` = already in the library. Lowercase – they appear inside tables. |
| `transfer.state.*`, `fits.status.*`, `history.event.*` | One-word table cells, lowercase. |
| `transfer.message.*`, `note.*`, `status.detail.*` | Short remarks in table cells – no capital letter, no full stop. |
| `queue.*` *batch* | All files queued by one click on Download. |
| `queue.tile.*` | Captions under large numbers; shown in capitals automatically. |
| `details.source.*` | Says where a value was taken from, e.g. “M31  (from FITS header)”. |
| `details.layout.*`, `settings.library.layout_value`, `<OBJECT>`, `<folder>` | Folder patterns. The words in capitals / angle brackets stand for real names and may be translated; `subs` may not. |
| `settings.scanning.subtitle` | `YYYY-MM-DD` is the date pattern (year-month-day); use the letters customary in your language. |
| `hostkey.*`, `error.hostkey.*` | The *host key* identifies an SSH server. Security warnings: translate precisely, keep the emphasis. |
| `{suffix}` | Becomes `.subpull-part`, the ending of unfinished downloads. |
| `{error}` | A technical message from the system, usually in English; it is inserted as is. |
| `settings.language.auto` | `{language}` becomes the name of the detected system language. |

Open the application next to the file if you can – seeing a text in place is the best context.
If a text is unclear, ask in an [issue](https://github.com/kopersyn/subpull/issues/new?template=translation.yml);
that usually means the notes above need another line.

### 5. Send it in (pull request)

1. Below the editor choose **Commit changes…**, write a short description such as `Add German translation`
   and confirm.
2. GitHub then offers **Create pull request** – click it twice. A *pull request* asks the maintainer to
   include your file.
3. Within a minute a check called **translations** runs. A green tick means the file is valid.
   A red cross means something needs fixing: click **Details** – every problem is listed with the file,
   the key and what is wrong, for example

   ```
   ERROR    de.json: [connection.connecting_to] placeholders differ from English: expected {server}, found {servr}
   ```

   Fix it by editing the file in your pull request (pencil icon); the check runs again by itself.

That is all. After the pull request is merged, your language appears in SubPull's settings in the next release.

### 6. Check and try your translation yourself (optional)

Checking only the file format needs no installation: paste the file into any online “JSON validator”.

For the full check and to see your texts in the application you need [Python 3.10+](https://www.python.org/downloads/)
and a copy of the repository:

```bash
git clone https://github.com/<your-name>/subpull
cd subpull
pip install -e .
python -m subpull.i18n.validate        # checks every language; "python -m subpull.i18n.validate de" only yours
subpull                                # then: Settings → Language
```

The language can be switched while SubPull is running. To find texts you missed, start it with the environment
variable `SUBPULL_I18N_DEBUG=1`; every text that fell back to English is then written to the Activity log.

## For maintainers

| Command | What it does |
|---|---|
| `python -m subpull.i18n.validate` | Validates all files; exit status 1 on invalid JSON, duplicate or unknown keys, wrong placeholders, wrong plural forms or a broken header. Missing translations are listed but allowed (`--strict` makes them fail). |
| `python -m subpull.i18n.stats` | Prints the status table. `--write` updates it in this file and in the main README, `--check` fails when they are out of date. |

- The check runs automatically for every pull request and every push to `main` that touches `subpull/i18n`
  ([workflow](../.github/workflows/translations.yml)).
- After merging a translation, run `python -m subpull.i18n.stats --write` and commit the result.
- **Changing English texts:** edit `en.json`. When the meaning of a text changes, give it a new key so that old
  translations do not silently keep the old meaning; remove the old key from all files. The test suite fails
  when the code uses a key that is missing from `en.json`, or when `en.json` contains a key the code does not use.
- New texts need no changes anywhere else: a language is picked up as soon as its file is in the folder.

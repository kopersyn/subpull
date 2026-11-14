"""Localization: translation manager, bundled locale files, validator and statistics."""
from __future__ import annotations

import io
import json
import logging
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from subpull import i18n, workflow
from subpull.config import Settings
from subpull.i18n import locale_file, plurals, stats, validate
from subpull.i18n.manager import t
from subpull.local_state import LocalStatus, status_label

logging.getLogger("subpull.i18n").addHandler(logging.NullHandler())      # expected warnings stay off the console
ROOT = Path(__file__).resolve().parents[1]
EN = {"_meta": {"code": "en", "name": "English", "english_name": "English", "direction": "ltr"},
      "translations": {"greeting": "Hello {name}", "plain": "Plain", "only.english": "Only English",
                       "files": {"one": "{count} file", "other": "{count} files"}}}


def write_locale(directory: Path, stem: str, translations, **meta) -> Path:
    meta = {"code": stem, "name": stem, "english_name": stem, "direction": "ltr", **meta}
    path = directory / f"{stem}.json"
    path.write_text(json.dumps({"_meta": meta, "translations": translations}, ensure_ascii=False), encoding="utf-8")
    return path


class TempLocales(unittest.TestCase):
    """Runs against a private locale directory; the bundled translations come back afterwards."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        (self.dir / "en.json").write_text(json.dumps(EN), encoding="utf-8")

    def tearDown(self):
        i18n.reload()
        i18n.set_language("en")
        self._td.cleanup()

    def use(self, code: str) -> None:
        i18n.reload(self.dir)
        self.assertEqual(i18n.set_language(code), code)

    def validate(self, *args) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            status = validate.main(["--dir", str(self.dir), *args])
        return status, out.getvalue()


class ManagerTests(TempLocales):
    def test_switching_and_formatting(self):
        write_locale(self.dir, "pl", {"greeting": "Cześć {name}", "plain": "Zwykły"}, name="Polski")
        self.use("pl")
        self.assertEqual(t("greeting", name="Zażółć"), "Cześć Zażółć")           # Unicode in text and values
        self.assertEqual(i18n.set_language("en"), "en")
        self.assertEqual(t("greeting", name="Ann"), "Hello Ann")

    def test_missing_translation_falls_back_to_english(self):
        write_locale(self.dir, "pl", {"plain": "Zwykły", "greeting": ""})
        self.use("pl")
        self.assertEqual(t("only.english"), "Only English")                      # key absent
        self.assertEqual(t("greeting", name="X"), "Hello X")                     # empty = not translated yet
        self.assertEqual(t("files", count=3), "3 files")
        info = {i.code: i for i in i18n.available_languages()}["pl"]
        self.assertEqual(info.completion, 25)

    def test_unknown_key_and_bad_placeholders_never_raise(self):
        write_locale(self.dir, "pl", {"greeting": "Cześć {nmae}"})               # typo in the placeholder
        self.use("pl")
        self.assertEqual(t("no.such.key"), "no.such.key")
        self.assertEqual(t("greeting", name="Ann"), "Hello Ann")                 # broken translation → English
        self.assertEqual(t("greeting"), "Cześć {nmae}")                          # nothing to fill in → raw text
        i18n.set_language("en")
        self.assertEqual(t("greeting", wrong=1), "Hello {name}")                 # English itself cannot be filled

    def test_missing_keys_are_logged_in_debug_mode(self):
        write_locale(self.dir, "pl", {"plain": "Zwykły"})
        self.use("pl")
        with mock.patch.dict("os.environ", {"SUBPULL_I18N_DEBUG": "1"}), self.assertLogs("subpull.i18n", "WARNING") as cm:
            t("only.english")
        self.assertIn("only.english", cm.output[0])

    def test_plural_forms(self):
        write_locale(self.dir, "pl", {"files": {"one": "{count} plik", "few": "{count} pliki",
                                                "many": "{count} plików", "other": "{count} pliku"}})
        self.use("pl")
        self.assertEqual([t("files", count=n) for n in (1, 2, 5, 12, 22, 25)],
                         ["1 plik", "2 pliki", "5 plików", "12 plików", "22 pliki", "25 plików"])
        i18n.set_language("en")
        self.assertEqual([t("files", count=n) for n in (0, 1, 2)], ["0 files", "1 file", "2 files"])
        self.assertEqual(plurals.categories("pl"), ("one", "few", "many", "other"))
        self.assertEqual(plurals.categories("ja"), ("other",))
        self.assertEqual(plurals.category("ar", 2), "two")

    def test_invalid_json_file_is_ignored(self):
        (self.dir / "de.json").write_text('{"_meta": {"code": "de",}, "translations": {', encoding="utf-8")
        write_locale(self.dir, "pl", {"plain": "Zwykły"})
        with self.assertLogs("subpull.i18n", "WARNING") as cm:
            i18n.reload(self.dir)
        self.assertIn("de.json", cm.output[0])
        self.assertEqual([i.code for i in i18n.available_languages()], ["en", "pl"])
        self.assertEqual(i18n.set_language("de"), "en")                          # unusable → system language → English

    def test_broken_english_file_does_not_crash(self):
        (self.dir / "en.json").write_text("not json", encoding="utf-8")
        with self.assertLogs("subpull.i18n", "WARNING"):
            i18n.reload(self.dir)
        self.assertEqual(t("plain"), "plain")
        self.assertEqual([i.code for i in i18n.available_languages()], ["en"])   # English is always offered

    def test_language_detection(self):
        write_locale(self.dir, "pl", {"plain": "Zwykły"})
        write_locale(self.dir, "pt-BR", {"plain": "Simples"})
        i18n.reload(self.dir)
        self.assertEqual(i18n.match_language(["pl_PL.UTF-8"]), "pl")
        self.assertEqual(i18n.match_language(["pt-BR"]), "pt-BR")
        self.assertEqual(i18n.match_language(["de-DE", "pl"]), "pl")             # first supported preference wins
        self.assertEqual(i18n.match_language(["de_DE.UTF-8", "fr"]), "en")       # unsupported → English
        self.assertEqual(i18n.set_language("auto", system=["pl-PL"]), "pl")
        self.assertEqual(i18n.set_language("auto", system=["xx"]), "en")

    def test_direction_and_metadata(self):
        write_locale(self.dir, "ar", {"plain": "عادي"}, name="العربية", english_name="Arabic", direction="rtl")
        self.use("ar")
        self.assertEqual(i18n.direction(), "rtl")
        info = {i.code: i for i in i18n.available_languages()}["ar"]
        self.assertEqual((info.name, info.english_name, info.label), ("العربية", "Arabic", "العربية – Arabic"))
        i18n.set_language("en")
        self.assertEqual(i18n.direction(), "ltr")


class ValidatorTests(TempLocales):
    def test_valid_and_missing(self):
        write_locale(self.dir, "pl", {"plain": "Zwykły", "greeting": "Cześć {name}"})
        status, out = self.validate()
        self.assertEqual(status, 0, out)
        self.assertIn("pl.json: [only.english] not translated yet", out)
        self.assertIn("pl.json: 2/4 translated (50%)", out)
        self.assertEqual(self.validate("--strict")[0], 1)                        # missing translations fail only here

    def test_invalid_json(self):
        (self.dir / "pl.json").write_text('{"_meta": {"code": "pl"}, "translations": {"plain": "x",}}', encoding="utf-8")
        status, out = self.validate()
        self.assertEqual(status, 1)
        self.assertRegex(out, r"ERROR\s+pl\.json: invalid JSON")

    def test_placeholder_mismatch(self):
        write_locale(self.dir, "pl", {"greeting": "Cześć {imie}", "plain": "ok {"})
        status, out = self.validate("pl")
        self.assertEqual(status, 1)
        self.assertIn("pl.json: [greeting] placeholders differ from English: expected {name}, found {imie}", out)
        self.assertIn("pl.json: [plain] unbalanced braces", out)

    def test_unknown_duplicate_and_wrong_type(self):
        (self.dir / "pl.json").write_text(
            '{"_meta": {"code": "pl", "name": "Polski", "english_name": "Polish", "direction": "ltr"},'
            ' "translations": {"plain": "a", "plain": "b", "renamed.key": "x", "greeting": 5}}', encoding="utf-8")
        status, out = self.validate()
        self.assertEqual(status, 1)
        self.assertIn("[plain] appears more than once", out)
        self.assertIn("[renamed.key] does not exist in English", out)
        self.assertIn("[greeting] must be text in quotes", out)

    def test_plural_rules(self):
        write_locale(self.dir, "pl", {"files": {"one": "{count} plik", "few": "{count} pliki", "many": "{count} plików",
                                                "other": "{count} pliku"}, "plain": {"other": "x"}})
        write_locale(self.dir, "de", {"files": {"one": "Eine Datei", "few": "{count} Dateien", "other": "{count} Dateien"}})
        write_locale(self.dir, "ja", {"files": "{count} ファイル"})                 # one text for all numbers is fine
        status, out = self.validate()
        self.assertEqual(status, 1)
        self.assertIn("pl.json: [plain] plural forms are only possible", out)
        self.assertIn('de.json: [files] plural form "few" is not used in this language', out)
        self.assertNotIn("ERROR    ja.json", out)
        self.assertNotIn("pl.json: [files]", out)

    def test_metadata(self):
        write_locale(self.dir, "pl", {"plain": "x"}, code="pl-PL", direction="up", name="")
        write_locale(self.dir, "Polish", {"plain": "x"})
        status, out = self.validate()
        self.assertEqual(status, 1)
        for expected in ("_meta.code is 'pl-PL' but the file is named pl.json", "_meta.direction must be",
                         "_meta.name is missing or empty", "'Polish' is not a valid language code"):
            self.assertIn(expected, out)


class BundledTranslationsTests(unittest.TestCase):
    """The files that ship with the application."""

    def tearDown(self):
        i18n.set_language("en")

    def test_all_files_are_valid(self):
        for report in validate.check_all():
            self.assertEqual(report.errors, [], f"{report.path.name}: {report.errors}")

    def test_english_and_polish_are_complete(self):
        reports = {r.code: r for r in validate.check_all()}
        self.assertEqual(reports["pl"].missing, [])
        self.assertEqual(reports["pl"].completion, 100)
        infos = {i.code: i for i in i18n.available_languages()}
        self.assertEqual((infos["en"].name, infos["pl"].name, infos["pl"].english_name), ("English", "Polski", "Polish"))
        self.assertEqual(i18n.available_languages()[0].code, "en")

    def test_switching_between_english_and_polish(self):
        self.assertEqual(t("actions.download"), "Download")
        self.assertEqual(i18n.set_language("pl"), "pl")
        self.assertEqual(t("actions.download"), "Pobierz")
        self.assertEqual(t("actions.download_count", count=3), "Pobierz 3 pliki")
        self.assertEqual(workflow.fmt_size(1536), "1,5 KB")
        self.assertEqual(status_label(LocalStatus.PRESENT), "już lokalnie")
        i18n.set_language("en")
        self.assertEqual(workflow.fmt_size(1536), "1.5 KB")
        self.assertEqual(status_label(LocalStatus.PRESENT), LocalStatus.PRESENT.value)

    def test_every_key_in_the_code_exists_and_every_key_is_used(self):
        keys = set(locale_file.load(locale_file.LOCALES_DIR / "en.json").translations)
        code = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "subpull").rglob("*.py"))
        literals = set(re.findall(r'["\']([a-z0-9_]+(?:\.[a-z0-9_]+)+)["\']', code))
        prefixes = set(re.findall(r'(?:\bt\(|\btx\.\w+\([^"\n]*?)\s*f"([a-z0-9_.]+)\{', code))
        # 1. texts requested by a literal key must exist
        requested = set(re.findall(r'\bt\(\s*["\']([^"\']+)["\']', code))
        requested |= set(re.findall(r'\btx\.(?:label|button)\(\s*"([^"]+)"', code))
        requested |= set(re.findall(r'\btx\.(?:bind|tool_button)\([^,]+,\s*"([^"]+)"', code))
        requested |= set(re.findall(r'\b(?:key|tooltip)="([^"]+)"', code))
        # ... as must every other literal that looks like a key ("a.b" in one of the key namespaces)
        namespaces = {k.split(".")[0] for k in keys}
        file_names = {"settings.json", "history.db"}
        requested |= {s for s in literals if s.split(".")[0] in namespaces} - file_names
        self.assertEqual(sorted(requested - keys), [], "keys used in the code but missing from en.json")
        # 2. keys built at run time (t(f"status.local.{name}")) must have at least one match
        for prefix in prefixes:
            self.assertTrue(any(k.startswith(prefix) for k in keys), f"no key starts with {prefix}")
        # 3. no dead texts for translators to waste time on
        unused = sorted(k for k in keys if k not in literals and not any(k.startswith(p) for p in prefixes))
        self.assertEqual(unused, [], "keys in en.json that the code never uses")

    def test_enum_like_values_have_texts(self):
        from subpull import downloader, scanner
        keys = set(locale_file.load(locale_file.LOCALES_DIR / "en.json").translations)
        expected = {f"status.local.{s.name.lower()}" for s in LocalStatus}
        expected |= {f"transfer.state.{s}" for s in (downloader.QUEUED, downloader.ACTIVE, downloader.VERIFYING,
                                                     downloader.DONE, downloader.SKIPPED, downloader.ERROR,
                                                     downloader.CANCELLED)}
        expected |= {f"fits.status.{k}" for k in scanner._FITS_STATUS_KEYS.values()}
        expected |= {f"connection.{s}" for s in ("idle", "busy", "ok", "error")}
        expected |= {f"units.{u}" for u in ("b", "kb", "mb", "gb", "tb")}
        self.assertEqual(sorted(expected - keys), [])

    def test_polish_plural_forms(self):
        """Every countable text in pl.json uses all Polish forms and picks the right one."""
        pl = locale_file.load(locale_file.LOCALES_DIR / "pl.json").translations
        en = locale_file.load(locale_file.LOCALES_DIR / "en.json").translations
        for key, value in pl.items():
            if isinstance(en[key], dict):        # plural in English → must be plural in Polish too
                self.assertIsInstance(value, dict, key)
            if isinstance(value, dict):
                self.assertEqual(set(value), {"one", "few", "many", "other"}, key)
        self.assertEqual([plurals.category("pl", n) for n in (0, 1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 101, 102, 112, 122)],
                         ["many", "one", "few", "few", "many", "many", "many", "many", "many", "few", "many",
                          "many", "few", "many", "few"])
        self.assertEqual(plurals.category("pl", 1.5), "other")
        i18n.set_language("pl")
        frames = [t("count.frames", count=n) for n in (1, 2, 5, 12, 22, 25)]
        self.assertEqual(frames, ["1 klatka", "2 klatki", "5 klatek", "12 klatek", "22 klatki", "25 klatek"])
        self.assertEqual([t("count.files", count=n) for n in (1, 3, 5, 21, 24)],
                         ["1 plik", "3 pliki", "5 plików", "21 plików", "24 pliki"])
        self.assertEqual([t("count.sessions", count=n) for n in (1, 2, 5)], ["1 sesja", "2 sesje", "5 sesji"])
        self.assertEqual([t("count.errors", count=n) for n in (0, 1, 3, 13)], ["0 błędów", "1 błąd", "3 błędy", "13 błędów"])
        self.assertEqual([t("count.local", count=n) for n in (1, 2, 7)], ["1 lokalny", "2 lokalne", "7 lokalnych"])
        self.assertEqual(t("history.downloads.count", count=22, size="1 KB"), "Pobrano 22 pliki · 1 KB")
        self.assertEqual(t("unrecognized.title", count=5), "5 nierozpoznanych elementów")

    def test_statistics_table(self):
        table = stats.table()
        self.assertIn("| English | `en` | 100% ", table)
        self.assertRegex(table, r"\| Polski \(Polish\) \| `pl` \| 100% \(\d+/\d+\) \| Complete \|")

    def test_settings_remember_the_language(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            self.assertEqual(Settings.load(path).language, "auto")
            s = Settings(language="pl")
            s.save(path)
            self.assertEqual(Settings.load(path).language, "pl")


if __name__ == "__main__":
    unittest.main()

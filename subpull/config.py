"""Constants, platform paths and persisted user settings."""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

APP_NAME = "SubPull"
SUBS_DIRNAME = "subs"
PART_SUFFIX = ".subpull-part"       # temporary file while downloading; renamed to the original name when verified


# --------------------------------------------------------------------------- platform paths
def _filezilla_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "FileZilla"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "filezilla"


def _app_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "SubPull"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SubPull"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "subpull"


FILEZILLA_DIR = _filezilla_dir()
SITEMANAGER_XML = FILEZILLA_DIR / "sitemanager.xml"
HOSTKEYS_XML = FILEZILLA_DIR / "hostkeys.xml"
KNOWN_HOSTS = Path.home() / ".ssh" / "known_hosts"

APP_DIR = _app_dir()
HISTORY_DB = APP_DIR / "history.db"
LOG_FILE = APP_DIR / "subpull.log"
SETTINGS_JSON = APP_DIR / "settings.json"

# --------------------------------------------------------------------------- network / transfer
CONNECT_TIMEOUT = 20          # s – TCP + SSH handshake
IO_TIMEOUT = 60               # s – a single SFTP operation
CONNECT_RETRIES = 3
CONNECT_SPACING = 0.6         # s – some servers reject several simultaneous SSH handshakes
TRANSFER_RETRIES = 3
RETRY_BACKOFF = (2, 5, 10)    # s
CHUNK_SIZE = 1024 * 1024
HEADER_READERS = 4

# OBJECT header values that do not identify a target (e.g. the N.I.N.A. default "Target").
DEFAULT_PLACEHOLDER_OBJECTS = ["", "TARGET", "OBJECT", "UNKNOWN"]


# --------------------------------------------------------------------------- user settings
@dataclass
class Settings:
    profile_name: str = ""                   # FileZilla Site Manager entry ("" = first SFTP site)
    dest_root: Optional[str] = None          # frames go to <dest_root>/<OBJECT>/subs/
    source_dir: str = "/"                    # remote directory to scan
    read_fits: bool = True                   # verify every FITS file against its header while scanning
    parallel_transfers: int = 3
    placeholder_objects: list = field(default_factory=lambda: list(DEFAULT_PLACEHOLDER_OBJECTS))

    @property
    def dest_path(self) -> Optional[Path]:
        return Path(self.dest_root) if self.dest_root else None

    @property
    def placeholders(self) -> set[str]:
        return {str(p).strip().upper() for p in self.placeholder_objects}

    @classmethod
    def load(cls, path: Path = SETTINGS_JSON) -> "Settings":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            s = cls()
            s._path = path
            return s
        except (OSError, ValueError) as e:
            log.warning("Could not read settings (%s) – using defaults", e)
            s = cls()
            s._path = path
            return s
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in data.items() if k in known})
        s._path = path
        return s

    def save(self, path: Optional[Path] = None) -> None:
        path = path or getattr(self, "_path", None) or SETTINGS_JSON
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except OSError as e:
            log.warning("Could not save settings: %s", e)

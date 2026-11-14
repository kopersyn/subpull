"""Read-only access to connection profiles saved in FileZilla's Site Manager.

Sites live in ``sitemanager.xml`` and host keys trusted by FileZilla in
``hostkeys.xml``. This module never writes to either file.
"""
from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import config
from .i18n import t

PROTOCOLS = {0: "ftp", 1: "sftp", 3: "ftps", 4: "ftpes", 6: "insecure-ftp"}
LOGON_NORMAL = 1
LOGON_ASK = 2
LOGON_INTERACTIVE = 3
LOGON_KEYFILE = 5


class ProfileError(Exception):
    pass


@dataclass(frozen=True)
class SiteProfile:
    name: str
    host: str
    port: int
    protocol: str
    user: str
    logontype: int
    keyfile: Optional[str]
    password: Optional[str] = None

    @property
    def auth_label(self) -> str:
        if self.logontype == LOGON_KEYFILE:
            return t("auth.key_file", file=Path(self.keyfile).name) if self.keyfile else t("auth.key")
        if self.logontype == LOGON_NORMAL:
            return t("auth.password")
        return t("auth.logon_type", type=self.logontype)

    def __repr__(self) -> str:  # never leak the password into logs
        return (f"SiteProfile(name={self.name!r}, host={self.host!r}, port={self.port}, "
                f"protocol={self.protocol!r}, user={self.user!r}, auth={self.auth_label!r})")


def _text(el: ET.Element, tag: str) -> Optional[str]:
    child = el.find(tag)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def _parse(server: ET.Element) -> SiteProfile:
    password = None
    pass_el = server.find("Pass")
    if pass_el is not None and pass_el.text:
        if pass_el.get("encoding") == "base64":
            password = base64.b64decode(pass_el.text).decode("utf-8")
        elif pass_el.get("encoding") in (None, "plain"):
            password = pass_el.text
        # encoding="crypt" (protected by a FileZilla master password) is not decrypted
    return SiteProfile(
        name=_text(server, "Name") or "",
        host=_text(server, "Host") or "",
        port=int(_text(server, "Port") or 22),
        protocol=PROTOCOLS.get(int(_text(server, "Protocol") or 0), "unknown"),
        user=_text(server, "User") or "",
        logontype=int(_text(server, "Logontype") or 0),
        keyfile=_text(server, "Keyfile"),
        password=password,
    )


def _servers(path: Path) -> list[ET.Element]:
    if not path.exists():
        raise ProfileError(t("error.site.no_sitemanager", path=path))
    try:
        return list(ET.parse(path).getroot().iter("Server"))
    except ET.ParseError as e:
        raise ProfileError(t("error.site.parse", path=path, error=e)) from e


def list_sites(path: Path = config.SITEMANAGER_XML, sftp_only: bool = True) -> list[str]:
    try:
        sites = [_parse(s) for s in _servers(path)]
    except ProfileError:
        return []
    return sorted(s.name for s in sites if s.name and (s.protocol == "sftp" or not sftp_only))


def load_site(name: str, path: Path = config.SITEMANAGER_XML) -> SiteProfile:
    for server in _servers(path):
        if _text(server, "Name") == name:
            return _parse(server)
    raise ProfileError(t("error.site.not_found", site=name, path=path))


def load_trusted_hostkeys(host: str, port: int,
                          hostkeys_xml: Path = config.HOSTKEYS_XML,
                          known_hosts: Path = config.KNOWN_HOSTS) -> list[bytes]:
    """Trusted host keys (raw SSH key blobs) from FileZilla and ~/.ssh/known_hosts."""
    keys: list[bytes] = []
    if hostkeys_xml.exists():
        try:
            for server in ET.parse(hostkeys_xml).getroot().iter("Server"):
                if server.get("Host") == host and int(server.get("Port", "22")) == port:
                    for hk in server.iter("Hostkey"):
                        if hk.text:
                            keys.append(base64.b64decode(hk.text.strip()))
        except (ET.ParseError, ValueError):
            pass
    if known_hosts.exists():
        lookup = host if port == 22 else f"[{host}]:{port}"
        try:
            import paramiko
            entry = paramiko.HostKeys(str(known_hosts)).lookup(lookup)
            if entry:
                keys.extend(k.asbytes() for k in entry.values())
        except Exception:
            pass
    return keys

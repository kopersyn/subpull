"""SFTP communication – the only module that talks to the network.

Exposes a minimal, read-only ``RemoteFS`` interface (``listdir``, ``stat``,
``open_read``, ``remote_hash``). The scanner and downloader use nothing else,
so nothing is ever written to the server.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import socket
import stat as statmod
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Optional, Protocol

from . import config
from .filezilla import LOGON_KEYFILE, LOGON_NORMAL, SiteProfile, load_trusted_hostkeys

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- errors
class ConnectionFailed(Exception):
    """Network/SSH failure – the operation may be retried."""


class AuthFailed(ConnectionFailed):
    """Authentication failure – retrying will not help."""


class HostKeyError(ConnectionFailed):
    def __init__(self, msg: str, fingerprint: str, key_blob: bytes, mismatch: bool):
        super().__init__(msg)
        self.fingerprint = fingerprint
        self.key_blob = key_blob
        self.mismatch = mismatch


class RemotePathError(Exception):
    """Problem with a single path (missing, no permission) – the connection is fine."""


# --------------------------------------------------------------------------- interface
@dataclass(frozen=True)
class RemoteEntry:
    name: str
    is_dir: bool
    size: int
    mtime: int


class RemoteFS(Protocol):
    def listdir(self, path: str) -> list[RemoteEntry]: ...
    def stat(self, path: str) -> RemoteEntry: ...
    def open_read(self, path: str, offset: int = 0, prefetch_size: Optional[int] = None) -> BinaryIO: ...
    def remote_hash(self, path: str) -> Optional[tuple[str, str]]: ...
    def close(self) -> None: ...


def join(*parts: str) -> str:
    out = parts[0].rstrip("/") or "/"
    for p in parts[1:]:
        out = (out.rstrip("/") + "/" + p.strip("/")) if out != "/" else "/" + p.strip("/")
    return out


def is_network_error(exc: BaseException) -> bool:
    import paramiko
    return isinstance(exc, (ConnectionFailed, socket.error, socket.timeout, EOFError,
                            paramiko.SSHException, ConnectionError, TimeoutError))


# --------------------------------------------------------------------------- SFTP
class SFTPSession:
    """One SFTP session on its own SSH transport. Do not share between threads."""

    def __init__(self, transport, sftp):
        self._transport = transport
        self._sftp = sftp
        self._hash_supported: Optional[bool] = None

    def _wrap(self, fn, path):
        try:
            return fn()
        except FileNotFoundError as e:
            raise RemotePathError(f"Not found on server: {path}") from e
        except PermissionError as e:
            raise RemotePathError(f"Permission denied: {path}") from e
        except IOError as e:
            if is_network_error(e) or not self._transport.is_active():
                raise ConnectionFailed(f"Connection lost ({path}): {e}") from e
            raise RemotePathError(f"{path}: {e}") from e
        except Exception as e:
            if is_network_error(e):
                raise ConnectionFailed(f"Connection lost ({path}): {e}") from e
            raise

    def listdir(self, path: str) -> list[RemoteEntry]:
        attrs = self._wrap(lambda: self._sftp.listdir_attr(path), path)
        out = []
        for a in attrs:
            if a.filename in (".", ".."):
                continue
            is_dir = statmod.S_ISDIR(a.st_mode or 0)
            if statmod.S_ISLNK(a.st_mode or 0):  # symlink – check the target
                try:
                    is_dir = statmod.S_ISDIR(self._sftp.stat(join(path, a.filename)).st_mode or 0)
                except IOError:
                    continue
            out.append(RemoteEntry(a.filename, is_dir, int(a.st_size or 0), int(a.st_mtime or 0)))
        return out

    def stat(self, path: str) -> RemoteEntry:
        a = self._wrap(lambda: self._sftp.stat(path), path)
        return RemoteEntry(path.rsplit("/", 1)[-1], statmod.S_ISDIR(a.st_mode or 0),
                           int(a.st_size or 0), int(a.st_mtime or 0))

    def open_read(self, path: str, offset: int = 0, prefetch_size: Optional[int] = None) -> BinaryIO:
        def _open():
            f = self._sftp.open(path, "rb")   # read-only, always
            if offset:
                f.seek(offset)
            if prefetch_size:
                f.prefetch(prefetch_size)
            return f
        return self._wrap(_open, path)

    def remote_hash(self, path: str) -> Optional[tuple[str, str]]:
        """Server-side checksum via the SFTP ``check-file`` extension, if supported."""
        if self._hash_supported is False:
            return None
        try:
            with self._sftp.open(path, "rb") as f:
                digest = f.check("sha256")
            self._hash_supported = True
            return ("sha256", digest.hex())
        except Exception:
            self._hash_supported = False
            return None

    def close(self) -> None:
        try:
            self._sftp.close()
        finally:
            self._transport.close()


class SFTPConnection:
    """Creates SFTP sessions from a saved FileZilla site profile."""

    def __init__(self, profile: SiteProfile, timeout: int = config.CONNECT_TIMEOUT):
        if profile.protocol != "sftp":
            raise ConnectionFailed(f"Site '{profile.name}' uses {profile.protocol}; only SFTP is supported.")
        self.profile = profile
        self.timeout = timeout
        self._accepted_hostkeys: set[bytes] = set()   # accepted for this program session only
        self._pkey = None
        self._lock = threading.Lock()
        self._connect_lock = threading.Lock()
        self._last_connect = 0.0

    @property
    def label(self) -> str:
        p = self.profile
        return f"{p.user}@{p.host}:{p.port}"

    def accept_hostkey_for_session(self, blob: bytes) -> None:
        self._accepted_hostkeys.add(blob)

    # -- private key -----------------------------------------------------------
    def _load_pkey(self):
        import paramiko
        with self._lock:
            if self._pkey is not None:
                return self._pkey
            kf = self.profile.keyfile
            if not kf:
                raise AuthFailed(f"Site '{self.profile.name}' does not specify a key file.")
            candidates = [Path(kf)]
            if kf.lower().endswith(".ppk"):
                candidates.append(Path(kf[:-4]))  # OpenSSH copy next to the .ppk file
            errors = []
            for c in candidates:
                if not c.exists():
                    continue
                try:
                    if c.read_bytes()[:40].startswith(b"PuTTY-User-Key-File"):
                        errors.append(f"{c.name}: PuTTY PPK format (not supported directly)")
                        continue
                    self._pkey = paramiko.PKey.from_path(str(c))
                    return self._pkey
                except paramiko.PasswordRequiredException:
                    errors.append(f"{c.name}: key is passphrase-protected")
                except Exception as e:
                    errors.append(f"{c.name}: {e}")
            self._pkey = False   # fall back to an SSH agent (Pageant / ssh-agent)
            log.warning("Could not load the key from the site profile (%s) – trying the SSH agent", "; ".join(errors))
            return self._pkey

    # -- connecting ------------------------------------------------------------
    def _verify_hostkey(self, key) -> None:
        blob = key.asbytes()
        trusted = load_trusted_hostkeys(self.profile.host, self.profile.port)
        if blob in trusted or blob in self._accepted_hostkeys:
            return
        fp = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
        if trusted:
            raise HostKeyError(f"Host key of {self.profile.host} does NOT match the trusted key! ({fp})",
                               fp, blob, mismatch=True)
        raise HostKeyError(f"Unknown host key for {self.profile.host} ({key.get_name()} {fp})", fp, blob, mismatch=False)

    def connect(self) -> SFTPSession:
        """Open a new session. Handshakes are serialized with a short gap because
        some servers reject several simultaneous SSH handshakes."""
        with self._connect_lock:
            wait = self._last_connect + config.CONNECT_SPACING - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                return self._connect()
            finally:
                self._last_connect = time.time()

    def _connect(self) -> SFTPSession:
        import paramiko
        p = self.profile
        try:
            sock = socket.create_connection((p.host, p.port), timeout=self.timeout)
        except socket.gaierror as e:
            raise ConnectionFailed(f"Cannot resolve {p.host}: {e}") from e
        except (socket.timeout, TimeoutError) as e:
            raise ConnectionFailed(f"Timed out connecting to {p.host}:{p.port}") from e
        except OSError as e:
            raise ConnectionFailed(f"Cannot connect to {p.host}:{p.port}: {e}") from e
        transport = paramiko.Transport(sock, default_window_size=64 * 1024 * 1024)
        try:
            transport.banner_timeout = self.timeout
            transport.auth_timeout = self.timeout
            transport.start_client(timeout=self.timeout)
            self._verify_hostkey(transport.get_remote_server_key())
            if p.logontype == LOGON_KEYFILE:
                pkey = self._load_pkey()
                if pkey:
                    transport.auth_publickey(p.user, pkey)
                else:
                    for k in paramiko.Agent().get_keys():
                        try:
                            transport.auth_publickey(p.user, k)
                            break
                        except paramiko.AuthenticationException:
                            continue
                    if not transport.is_authenticated():
                        raise AuthFailed("Cannot use the site's key file (or any SSH agent key).")
            elif p.logontype == LOGON_NORMAL and p.password is not None:
                transport.auth_password(p.user, p.password)
            else:
                raise AuthFailed(f"Site '{p.name}' has no saved credentials usable without prompting "
                                 "(use a key file or a saved password).")
            transport.set_keepalive(30)
            sftp = paramiko.SFTPClient.from_transport(transport)
            sftp.get_channel().settimeout(config.IO_TIMEOUT)
            return SFTPSession(transport, sftp)
        except paramiko.AuthenticationException as e:
            transport.close()
            raise AuthFailed(f"Authentication rejected for {p.user}@{p.host}: {e}") from e
        except ConnectionFailed:
            transport.close()
            raise
        except Exception as e:
            transport.close()
            raise ConnectionFailed(f"SSH error: {e}") from e

    def connect_with_retry(self, retries: int = config.CONNECT_RETRIES,
                           cancel: Optional[threading.Event] = None,
                           on_retry: Optional[Callable[[int, Exception], None]] = None) -> SFTPSession:
        return with_retry(self.connect, retries, cancel, on_retry)


def with_retry(fn: Callable[[], SFTPSession], retries: int, cancel: Optional[threading.Event] = None,
               on_retry: Optional[Callable[[int, Exception], None]] = None):
    last: Optional[Exception] = None
    for attempt in range(retries):
        if cancel is not None and cancel.is_set():
            break
        try:
            return fn()
        except (AuthFailed, HostKeyError):
            raise
        except ConnectionFailed as e:
            last = e
            if attempt + 1 >= retries:
                break
            delay = config.RETRY_BACKOFF[min(attempt, len(config.RETRY_BACKOFF) - 1)]
            log.warning("Connection failed (attempt %d/%d): %s – retrying in %ss", attempt + 1, retries, e, delay)
            if on_retry:
                on_retry(attempt + 1, e)
            if cancel is not None:
                if cancel.wait(delay):
                    break
            else:
                time.sleep(delay)
    if last is None:
        raise ConnectionFailed("Operation cancelled")
    raise last

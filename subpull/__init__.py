"""SubPull – manual, safe downloader for astrophotography frames on SFTP servers."""
try:
    from importlib.metadata import version as _version
    __version__ = _version("subpull")
except Exception:  # running from a source tree without installation
    __version__ = "1.0.0"

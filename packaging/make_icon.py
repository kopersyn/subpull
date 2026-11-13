"""Render the SubPull logo into a multi-resolution Windows .ico (PNG-compressed entries)."""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QGuiApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
app = QGuiApplication.instance() or QGuiApplication([])
from subpull.ui.icons import _logo_svg, _render  # noqa: E402

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def png_bytes(size: int) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    _render(_logo_svg(), size).save(buf, "PNG")
    return bytes(buf.data())


def main(out: Path) -> None:
    images = [png_bytes(s) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, data = b"", b""
    for size, img in zip(SIZES, images):
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(img), offset + len(data))
        data += img
    out.write_bytes(header + entries + data)
    print(f"wrote {out} ({len(SIZES)} sizes)")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("subpull.ico"))

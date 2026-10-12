#!/usr/bin/env python3
"""Save the test VM's screen as PNG: tools/vm-screenshot.py OUT.png (uses the QEMU monitor socket)."""
import socket
import struct
import sys
import time
import zlib
from pathlib import Path

out_dir = Path(__file__).resolve().parents[1] / "image" / "out"
png = Path(sys.argv[1] if len(sys.argv) > 1 else out_dir / "screen.png")
ppm = out_dir / "screen.ppm"
with socket.socket(socket.AF_UNIX) as s:
    s.connect(str(out_dir / "monitor.sock"))
    s.sendall(f"screendump {ppm}\n".encode())
    time.sleep(1.5)
data = ppm.read_bytes()
parts = data.split(maxsplit=4)
w, h, body = int(parts[1]), int(parts[2]), parts[4]
raw = b"".join(b"\x00" + body[y * w * 3:(y + 1) * w * 3] for y in range(h))
chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))  # noqa: E731
png.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
print(png)

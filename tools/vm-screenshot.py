#!/usr/bin/env python3
"""Save the test VM's screen as PNG: tools/vm-screenshot.py [OUT.png] (QEMU's QMP socket)."""
import json
import socket
import sys
from pathlib import Path

out_dir = Path(__file__).resolve().parents[1] / "image" / "out"
png = Path(sys.argv[1] if len(sys.argv) > 1 else out_dir / "screen.png").resolve()
with socket.socket(socket.AF_UNIX) as s:
    s.settimeout(30)
    s.connect(str(out_dir / "qmp.sock"))
    f = s.makefile("rw")
    f.readline()  # greeting
    for cmd in ({"execute": "qmp_capabilities"},
                {"execute": "screendump", "arguments": {"filename": str(png), "format": "png"}}):
        f.write(json.dumps(cmd) + "\n")
        f.flush()
        while True:
            reply = json.loads(f.readline())
            if "return" in reply or "error" in reply:
                break
        if "error" in reply:
            sys.exit(f"screendump failed: {reply['error']}")
print(png)

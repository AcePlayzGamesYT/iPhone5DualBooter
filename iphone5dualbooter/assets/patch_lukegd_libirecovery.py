#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import sys


FIXED_LINE = (
    'snprintf(cmdstr, sizeof(cmdstr), '
    '"setenv filesize %d", (int)length);'
)

PATTERN = re.compile(
    r'snprintf\s*\(\s*&cmdstr\s*,\s*'
    r'sizeof\s*\(\s*cmdstr\s*\)\s*,\s*'
    r'"setenv filesize %d"\s*,\s*length\s*\)\s*;'
)


def patch_file(path: Path) -> None:
    source = path.read_text(encoding="utf-8")

    if FIXED_LINE in source:
        print(f"libirecovery compatibility fix already present: {path}")
        return

    patched, count = PATTERN.subn(FIXED_LINE, source, count=1)
    if count != 1:
        nearby = "\n".join(
            line
            for line in source.splitlines()
            if "setenv filesize" in line or "snprintf" in line
        )
        raise SystemExit(
            "Could not find the known LukeeGD/libirecovery snprintf typo. "
            "Refusing to patch an unknown source revision."
            + (f"\nNearby source:\n{nearby}" if nearby else "")
        )

    path.write_text(patched, encoding="utf-8")
    print(f"Patched legacy libirecovery snprintf call for modern GCC: {path}")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch_lukegd_libirecovery.py PATH/TO/src/libirecovery.c")
        return 2
    patch_file(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

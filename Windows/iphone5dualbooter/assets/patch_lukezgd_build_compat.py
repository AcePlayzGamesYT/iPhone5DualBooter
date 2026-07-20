#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import argparse
import re

COMPAT_MARKER = "IPHONE5DUALBOOTER_GCC15_COMPAT_V1"


def patch_configure_ac(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    original = text
    changed = 0

    replacements = (
        (
            r'''if test "\$ac_cv_sys_file_offset_bits" != ['\"]no['\"]; then''',
            '''if test -n "$ac_cv_sys_file_offset_bits" && test "$ac_cv_sys_file_offset_bits" != 'no'; then''',
        ),
        (
            r'''if test "\$ac_cv_sys_large_files" != ['\"]no['\"]; then''',
            '''if test -n "$ac_cv_sys_large_files" && test "$ac_cv_sys_large_files" != 'no'; then''',
        ),
        (
            r'''if test "\$ac_cv_sys_largefile_source" != ['\"]no['\"]; then''',
            '''if test -n "$ac_cv_sys_largefile_source" && test "$ac_cv_sys_largefile_source" != 'no'; then''',
        ),
    )

    for pattern, replacement in replacements:
        text, count = re.subn(pattern, replacement, text, count=1)
        changed += count

    if COMPAT_MARKER not in text:
        anchor = "# check for large file support"
        if anchor in text:
            text = text.replace(anchor, f"# {COMPAT_MARKER}\n{anchor}", 1)
        else:
            text = f"# {COMPAT_MARKER}\n" + text

    if text != original:
        path.write_text(text, encoding="utf-8")
    return changed


def patch_restore_c(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    original = text

    text, count = re.subn(
        r"\bthread_t\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*NULL\s*;",
        r"thread_t \1 = (thread_t)0;",
        text,
    )

    if COMPAT_MARKER not in text:
        include = '#include "restore.h"'
        if include in text:
            text = text.replace(include, include + f"\n/* {COMPAT_MARKER} */", 1)

    if text != original:
        path.write_text(text, encoding="utf-8")
    return count


def sanitize_generated_makefiles(root: Path) -> int:
    changed = 0
    for path in root.rglob("Makefile"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fixed = re.sub(
            r"-D_FILE_OFFSET_BITS=(?=\s|$)",
            "-D_FILE_OFFSET_BITS=64",
            text,
        )
        if fixed != text:
            path.write_text(fixed, encoding="utf-8")
            changed += 1
    return changed


def patch_tree(root: Path) -> None:
    configure = root / "configure.ac"
    restore = root / "src" / "restore.c"
    if not configure.is_file() or not restore.is_file():
        raise SystemExit(f"Not an idevicerestore source tree: {root}")

    configure_count = patch_configure_ac(configure)
    thread_count = patch_restore_c(restore)
    print(
        "Applied GCC 15 compatibility patch: "
        f"large-file checks={configure_count}, thread initializers={thread_count}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--sanitize-makefiles", action="store_true")
    args = parser.parse_args()

    if args.sanitize_makefiles:
        count = sanitize_generated_makefiles(args.root)
        print(f"Sanitized generated Makefiles: {count}")
    else:
        patch_tree(args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from collections.abc import Callable, Sequence
import os
from pathlib import Path
import shlex
import subprocess
import sys


LogFn = Callable[[str], None]


class ProcessError(RuntimeError):
    pass


def command_to_text(command: Sequence[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(list(command))
    return shlex.join(list(command))


def run_streaming(
    command: Sequence[str],
    log: LogFn,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    interactive: bool = False,
) -> None:
    log(f"$ {command_to_text(command)}")
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    process = subprocess.Popen(
        list(command),
        cwd=str(cwd) if cwd else None,
        env=env,
        stdin=None if interactive else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
        creationflags=creationflags,
    )
    assert process.stdout is not None
    for line in process.stdout:
        log(line.rstrip())
    code = process.wait()
    if code != 0:
        raise ProcessError(f"Command failed with exit code {code}.")

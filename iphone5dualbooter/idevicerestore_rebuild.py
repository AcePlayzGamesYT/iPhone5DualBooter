from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re
import shutil
import subprocess
import sys


LogFn = Callable[[str], None]

PATCH_MARKER = b"IPHONE5DUALBOOTER_DFU_RECOVERY_RECONNECT_V12"
PATCH_RUNTIME_SIGNATURES = (
    b"[iPhone5DualBooter] Initial iBEC upload failed:",
    b"[iPhone5DualBooter] Retrying iBEC with freshly reopened DFU handles until one complete upload succeeds",
    b"[iPhone5DualBooter] iBEC upload succeeded with the original send mode",
    b"[iPhone5DualBooter] LIVE_HOST_USB_HANDOFF_REQUIRED",
    b"[iPhone5DualBooter] Waiting inside the same idevicerestore process",
    b"[iPhone5DualBooter] Windows USB handoff acknowledged",
    b"[iPhone5DualBooter] Connected to post-iBEC Recovery mode",
    b"[iPhone5DualBooter] ASR validation receive timed out",
    b"[iPhone5DualBooter] ERROR: the initial ASR Initiate handshake failed",
    b"[iPhone5DualBooter] ASR validation reached the Payload request",
    b"[iPhone5DualBooter] ASR OOB request #",
    b"[iPhone5DualBooter] ASR OOB request #%u send would block",
    b"[iPhone5DualBooter] ASR OOB request #%u completed successfully",
    b"[iPhone5DualBooter] ASR OOB request #%u is temporarily backpressured",
    b"[iPhone5DualBooter] Opening one ASR session for validation and payload",
    b"[iPhone5DualBooter] ASR validation is still quiet",
    b"[iPhone5DualBooter] WSL 64 KiB OOB guard active",
    b"[iPhone5DualBooter] Restored transfer %s progress",
    b"[iPhone5DualBooter] ERROR: restored transport timed out waiting 60 seconds",
)
BUILD_SCRIPT_NAME = "build_patched_idevicerestore.sh"
PATCHER_NAME = "patch_lukezgd_idevicerestore.py"
LIBIRECOVERY_PATCHER_NAME = "patch_lukegd_libirecovery.py"
COMPAT_PATCHER_NAME = "patch_lukezgd_build_compat.py"
LIBIMOBILEDEVICE_TRANSPORT_PATCHER_NAME = "patch_libimobiledevice_restored_transport.py"

WSL_BUILD_BOOTSTRAP = r"""
set -u

log_file="$1"
shift
main_script="$1"
shift

mkdir -p "$(dirname "$log_file")"
: > "$log_file"
exec > >(tee -a "$log_file") 2>&1

echo "============================================================"
echo "iPhone5DualBooter WSL build bootstrap v0.4.21"
echo "Main script: $main_script"
echo "Build argument count: $#"
echo "============================================================"

if [[ $# -ne 9 ]]; then
    echo "ERROR: expected 9 build arguments, received $#."
    index=1
    for value in "$@"; do
        printf '  argument %d: <%q>\n' "$index" "$value"
        index=$((index + 1))
    done
    exit 64
fi

export IPHONE5DUALBOOTER_LOG_INITIALIZED=1
exec /bin/bash "$main_script" "$@"
"""


class IDeviceRestoreRebuildError(RuntimeError):
    pass


def _asset_path(name: str) -> Path:
    candidates = [Path(__file__).resolve().parent / "assets" / name]
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.insert(
            0,
            Path(bundle) / "iphone5dualbooter" / "assets" / name,
        )

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise IDeviceRestoreRebuildError(
        f"Bundled rebuild asset is missing: {name}"
    )


def find_legacy_idevicerestore(legacy_root: Path) -> Path | None:
    preferred = (
        Path(legacy_root)
        / "bin"
        / "linux"
        / "x86_64"
        / "idevicerestore"
    )
    return preferred if preferred.is_file() else None


def _contains(path: Path, marker: bytes) -> bool:
    try:
        return marker in path.read_bytes()
    except OSError:
        return False


def is_v049_patched_binary(path: Path) -> bool:
    """Detect compiled patch code rather than a source-only comment marker."""
    try:
        content = path.read_bytes()
    except OSError:
        return False
    return all(signature in content for signature in PATCH_RUNTIME_SIGNATURES)


def migrate_old_delay_wrapper(
    legacy_root: Path,
    log: LogFn,
) -> None:
    binary = find_legacy_idevicerestore(legacy_root)
    if binary is None:
        return

    old_markers = (
        b"IPHONE5DUALBOOTER_LD_PRELOAD_IBSS_DELAY_V1",
        b"IPHONE5DUALBOOTER_LD_PRELOAD_PRE_IBEC_DELAY_V2",
    )
    if not any(_contains(binary, marker) for marker in old_markers):
        return

    real = binary.with_name("idevicerestore.real")
    if not real.is_file():
        raise IDeviceRestoreRebuildError(
            "An old delay wrapper was found, but idevicerestore.real "
            f"is missing: {real}"
        )

    binary.unlink()
    real.replace(binary)

    for name in (
        "libiphone5dualbooter_pre_ibec_delay.so",
        "idevicerestore_pre_ibec_delay.c",
        "libiphone5dualbooter_ibss_delay.so",
        "idevicerestore_ibss_delay.c",
    ):
        binary.with_name(name).unlink(missing_ok=True)

    log(
        "Removed the old timing-only wrapper and restored the real "
        "Legacy idevicerestore before source patching."
    )


def _default_windows_mount_path(raw_path: str) -> str | None:
    """
    Convert a normal drive-letter Windows path using WSL's default automount
    convention. This is only a fallback for distributions where wslpath fails.
    """
    match = re.fullmatch(
        r"([A-Za-z]):[\\/](.*)",
        raw_path.strip(),
        flags=re.DOTALL,
    )
    if not match:
        return None

    drive = match.group(1).lower()
    tail = match.group(2).replace("\\", "/")
    while "//" in tail:
        tail = tail.replace("//", "/")
    return f"/mnt/{drive}/{tail}"


def _windows_to_wsl_path(
    wsl: Path,
    distro: str,
    path: Path,
) -> str:
    raw_path = str(path.resolve())

    # Do not put the Windows path in a shell command. Bash interprets each
    # backslash as an escape, which turns C:\\Users\\Name into C:UsersName.
    # Instead, transmit it through stdin and use `read -r` so every backslash
    # arrives at wslpath literally.
    completed = subprocess.run(
        [
            str(wsl),
            "-d",
            distro,
            "--exec",
            "sh",
            "-c",
            'IFS= read -r windows_path; exec wslpath -a -u "$windows_path"',
        ],
        input=raw_path + "\n",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        check=False,
    )
    output = (completed.stdout or "").strip()
    converted = output.splitlines()[-1].strip() if output else ""
    if completed.returncode == 0 and converted.startswith("/"):
        return converted

    fallback = _default_windows_mount_path(raw_path)
    if fallback is not None:
        return fallback

    raise IDeviceRestoreRebuildError(
        f"Could not convert path for WSL: {path}\n\n"
        f"{output or '(no output)'}"
    )


def _prepare_build_directory(
    app_root: Path,
) -> tuple[Path, Path, Path, Path, Path, Path, Path]:
    build_root = app_root / "tools" / "idevicerestore-rebuild"
    build_root.mkdir(parents=True, exist_ok=True)

    script = build_root / BUILD_SCRIPT_NAME
    patcher = build_root / PATCHER_NAME
    libirecovery_patcher = build_root / LIBIRECOVERY_PATCHER_NAME
    compat_patcher = build_root / COMPAT_PATCHER_NAME
    transport_patcher = build_root / LIBIMOBILEDEVICE_TRANSPORT_PATCHER_NAME
    shutil.copy2(_asset_path(BUILD_SCRIPT_NAME), script)
    shutil.copy2(_asset_path(PATCHER_NAME), patcher)
    shutil.copy2(
        _asset_path(LIBIRECOVERY_PATCHER_NAME),
        libirecovery_patcher,
    )
    shutil.copy2(_asset_path(COMPAT_PATCHER_NAME), compat_patcher)
    shutil.copy2(_asset_path(LIBIMOBILEDEVICE_TRANSPORT_PATCHER_NAME), transport_patcher)

    try:
        script.chmod(0o755)
        patcher.chmod(0o755)
        libirecovery_patcher.chmod(0o755)
        compat_patcher.chmod(0o755)
        transport_patcher.chmod(0o755)
    except OSError:
        pass

    log_file = build_root / "build.log"
    try:
        log_file.write_text(
            "iPhone5DualBooter Windows launcher prepared the WSL build.\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise IDeviceRestoreRebuildError(
            f"Could not create build log before launching WSL: {log_file}\n{exc}"
        ) from exc

    return (
        build_root,
        script,
        patcher,
        libirecovery_patcher,
        compat_patcher,
        transport_patcher,
        log_file,
    )


def _tail_build_log(path: Path, lines: int = 100) -> str:
    try:
        content = path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()
    except OSError:
        return ""
    return "\n".join(content[-max(1, int(lines)):])


def ensure_patched_idevicerestore(
    legacy_root: Path,
    app_root: Path,
    wsl: Path,
    distro: str,
    retry_seconds: int,
    allow_full_fallback: bool,
    log: LogFn,
) -> bool:
    migrate_old_delay_wrapper(legacy_root, log)

    binary = find_legacy_idevicerestore(legacy_root)
    if binary is None:
        log(
            "Legacy's idevicerestore is not present yet. It will be "
            "source-patched and compiled before the next Legacy launch."
        )
        return False

    if is_v049_patched_binary(binary):
        log(
            "Patched idevicerestore is already installed. "
            "Runtime mode: retry iBEC until success, then pause the same idevicerestore process for a live Windows USB handoff."
        )
        return True

    (
        build_root,
        script,
        patcher,
        libirecovery_patcher,
        compat_patcher,
        transport_patcher,
        log_file,
    ) = _prepare_build_directory(app_root)
    cache_root = build_root / "cache"
    cache_root.mkdir(parents=True, exist_ok=True)

    legacy_wsl = _windows_to_wsl_path(wsl, distro, legacy_root)
    cache_wsl = _windows_to_wsl_path(wsl, distro, cache_root)
    script_wsl = _windows_to_wsl_path(wsl, distro, script)
    patcher_wsl = _windows_to_wsl_path(wsl, distro, patcher)
    libirecovery_patcher_wsl = _windows_to_wsl_path(
        wsl,
        distro,
        libirecovery_patcher,
    )
    compat_patcher_wsl = _windows_to_wsl_path(
        wsl, distro, compat_patcher
    )
    transport_patcher_wsl = _windows_to_wsl_path(wsl, distro, transport_patcher)
    log_wsl = _windows_to_wsl_path(wsl, distro, log_file)

    command = [
        str(wsl),
        "-d",
        distro,
        "--exec",
        "/bin/bash",
        "-c",
        WSL_BUILD_BOOTSTRAP,
        "iphone5dualbooter-build-bootstrap",
        log_wsl,
        script_wsl,
        legacy_wsl,
        cache_wsl,
        patcher_wsl,
        str(max(5, min(180, int(retry_seconds)))),
        "1" if allow_full_fallback else "0",
        log_wsl,
        libirecovery_patcher_wsl,
        compat_patcher_wsl,
        transport_patcher_wsl,
    ]

    log(
        "Building LukeZGD's idevicerestore fork with forced DFU/iBEC "
        "and post-iBEC recovery reconnect handling. A WSL terminal "
        "will open."
    )
    log(
        "The fast build uses Ubuntu development libraries. If enabled, "
        "a slower full dependency/static build runs automatically when "
        "the fast build fails."
    )
    log(f"Build log: {log_file}")
    log(
        "Launching through WSL --exec with a pre-build logging bootstrap. "
        "Paths containing spaces or parentheses are passed as positional "
        "arguments instead of being parsed as shell syntax."
    )

    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    exit_code = subprocess.Popen(
        command,
        creationflags=creationflags,
    ).wait()

    if exit_code != 0:
        tail = _tail_build_log(log_file)
        if not tail.strip():
            fallback = (
                "WSL exited before the logging bootstrap produced output.\n"
                f"Exit code: {exit_code}\n"
                f"Distribution: {distro}\n"
                "Launch mode: wsl.exe --exec /bin/bash -c <bootstrap>\n"
            )
            try:
                log_file.write_text(fallback, encoding="utf-8")
            except OSError:
                pass
            tail = _tail_build_log(log_file)

        apt_note = ""
        if exit_code == 100:
            apt_note = (
                "\n\nExit code 100 is from APT. v0.4.15 installs "
                "packages defensively, but the log tail below identifies "
                "any broken repository or package that still blocked it."
            )
        raise IDeviceRestoreRebuildError(
            "Patched idevicerestore build failed with exit code "
            f"{exit_code}. Build log: {log_file}"
            f"{apt_note}"
            + ("\n\n--- build.log tail ---\n" + tail if tail else "")
        )

    binary = find_legacy_idevicerestore(legacy_root)
    if binary is None or not is_v049_patched_binary(binary):
        raise IDeviceRestoreRebuildError(
            "The build process exited successfully, but the installed "
            "idevicerestore does not contain all compiled DFU/iBEC/recovery "
            "transition signatures."
        )

    log(
        "Installed the source-patched idevicerestore. It keeps the original "
        "iBEC/live USB handoff behavior and adds timed ASR validation receives, "
        "repeated-Initiate support, and clean ASR reconnects instead of an "
        "indefinite filesystem-validation block."
    )
    return True

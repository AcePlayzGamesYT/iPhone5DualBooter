from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile

from .idevicerestore_rebuild import ensure_patched_idevicerestore
from .windows_idevicerestore import (
    ensure_windows_idevicerestore_suite,
)
from .ipsw import inspect_ipsw
from .models import WorkflowSettings
from .usbipd import (
    AppleUSBWatcher,
    USBIPDDevice,
    USBIPDError,
    detach_attached_apple_devices,
    detach_from_wsl,
    list_devices,
    stop_wsl_keepalive,
    wsl_lsusb,
    wait_for_windows_recovery,
)


LogFn = Callable[[str], None]
LegacyPromptFn = Callable[[int, Path, int, str], str]
ExternalPwnPromptFn = Callable[[int], bool]

LEGACY_RELEASE_API = "https://api.github.com/repos/LukeZGD/Legacy-iOS-Kit/releases/latest"
LEGACY_CODE_ZIP = "https://github.com/LukeZGD/Legacy-iOS-Kit/archive/refs/heads/main.zip"
_VALID_DISTRO = re.compile(r"^[^\r\n\x00]+$")


class LegacyWSLError(RuntimeError):
    pass


@dataclass(frozen=True)
class LegacyRestoreResult:
    legacy_root: Path
    transcript: Path
    exit_code: int


def _request_bytes(url: str, timeout: int = 180) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "iPhone5DualBooter/0.4.41",
            "Accept": "application/vnd.github+json, application/octet-stream",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if target != destination and destination not in target.parents:
            raise LegacyWSLError("Unsafe path found in the Legacy iOS Kit ZIP.")
    archive.extractall(destination)


def find_legacy_root(directory: Path) -> Path | None:
    directory = Path(directory)
    if (directory / "restore.sh").is_file() and (directory / "resources").is_dir():
        return directory.resolve()

    candidates = [
        path.parent
        for path in directory.rglob("restore.sh")
        if path.is_file() and (path.parent / "resources").is_dir()
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0].resolve()


def _latest_release_asset_url() -> str:
    payload = json.loads(_request_bytes(LEGACY_RELEASE_API, timeout=45).decode("utf-8"))
    assets = payload.get("assets") or []
    zip_assets = [
        asset
        for asset in assets
        if str(asset.get("name", "")).lower().endswith(".zip")
        and "legacy-ios-kit" in str(asset.get("name", "")).lower()
        and asset.get("browser_download_url")
    ]
    if not zip_assets:
        raise LegacyWSLError("The latest Legacy iOS Kit release has no ZIP asset.")
    zip_assets.sort(key=lambda asset: int(asset.get("size") or 0), reverse=True)
    return str(zip_assets[0]["browser_download_url"])


def ensure_legacy_kit(
    app_root: Path,
    explicit_directory: Path | None,
    auto_download: bool,
    log: LogFn,
) -> Path:
    if explicit_directory:
        root = find_legacy_root(explicit_directory)
        if root:
            return root
        raise LegacyWSLError(
            f"restore.sh and resources were not found in {explicit_directory}."
        )

    tools = app_root / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    existing = find_legacy_root(tools)
    if existing:
        log(f"Using Legacy iOS Kit: {existing}")
        return existing

    if not auto_download:
        raise LegacyWSLError(
            "Select a Legacy iOS Kit folder or enable automatic download."
        )

    download_root = tools / "Legacy-iOS-Kit"
    download_root.mkdir(parents=True, exist_ok=True)
    try:
        url = _latest_release_asset_url()
        log("Downloading the latest official Legacy iOS Kit ZIP...")
    except Exception as exc:
        url = LEGACY_CODE_ZIP
        log(f"Release lookup failed; using the official main-branch ZIP: {exc}")

    try:
        data = _request_bytes(url)
    except Exception as exc:
        raise LegacyWSLError(f"Could not download Legacy iOS Kit: {exc}") from exc

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp:
        temp.write(data)
        temp_path = Path(temp.name)
    try:
        with zipfile.ZipFile(temp_path, "r") as archive:
            _safe_extract(archive, download_root)
    except zipfile.BadZipFile as exc:
        raise LegacyWSLError("The Legacy iOS Kit download was not a valid ZIP.") from exc
    finally:
        temp_path.unlink(missing_ok=True)

    root = find_legacy_root(download_root)
    if not root:
        raise LegacyWSLError("Legacy downloaded, but restore.sh/resources were not found.")
    log(f"Using Legacy iOS Kit: {root}")
    return root


def find_wsl() -> Path:
    located = shutil.which("wsl.exe") or shutil.which("wsl")
    if located:
        return Path(located)
    candidate = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wsl.exe"
    if candidate.is_file():
        return candidate
    raise LegacyWSLError("WSL was not found. Install WSL 2 and an Ubuntu distro first.")


def _decode_wsl_output(payload: bytes) -> str:
    if b"\x00" in payload:
        return payload.decode("utf-16le", errors="replace")
    return payload.decode(errors="replace")


def list_wsl_distros(wsl: Path | None = None) -> list[str]:
    executable = wsl or find_wsl()
    completed = subprocess.run(
        [str(executable), "--list", "--quiet"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    text = _decode_wsl_output(completed.stdout)
    return [
        line.strip().replace("\x00", "").lstrip("\ufeff")
        for line in text.splitlines()
        if line.strip().replace("\x00", "").lstrip("\ufeff")
    ]


def _direct_windows_path_to_wsl(path_text: str, distro: str) -> str | None:
    value = path_text.strip().replace("\x00", "")
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]

    drive_match = re.fullmatch(r"([A-Za-z]):[\\/](.*)", value, flags=re.DOTALL)
    if drive_match:
        drive = drive_match.group(1).lower()
        remainder = drive_match.group(2).replace("\\", "/")
        remainder = "/".join(part for part in remainder.split("/") if part)
        return f"/mnt/{drive}" + (f"/{remainder}" if remainder else "")

    unc_match = re.fullmatch(
        r"\\\\(?:wsl\\$|wsl\.localhost)\\([^\\]+)\\?(.*)",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if unc_match and unc_match.group(1).casefold() == distro.casefold():
        remainder = unc_match.group(2).replace("\\", "/")
        remainder = "/".join(part for part in remainder.split("/") if part)
        return "/" + remainder if remainder else "/"
    return None


def windows_to_wsl_path(wsl: Path, distro: str, path: Path) -> str:
    resolved = str(path.resolve())
    direct = _direct_windows_path_to_wsl(resolved, distro)
    if direct:
        return direct

    completed = subprocess.run(
        [
            str(wsl), "-d", distro, "--", "bash", "-lc",
            'IFS= read -r winpath; wslpath -a -u -- "$winpath"',
        ],
        input=(resolved + "\n").encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        error = _decode_wsl_output(completed.stderr).strip()
        raise LegacyWSLError(f"WSL could not convert {resolved!r}: {error}")
    converted = _decode_wsl_output(completed.stdout).strip().replace("\x00", "")
    if not converted:
        raise LegacyWSLError("WSL returned an empty converted path.")
    return converted



WINDOWS_HOST_WRAPPER_MARKER = (
    "IPHONE5DUALBOOTER_WINDOWS_HOST_IDEVICERESTORE_WRAPPER_V1"
)


def build_windows_host_idevicerestore_wrapper(
    suite_wsl: str,
    request_marker_wsl: str,
    ack_marker_wsl: str,
) -> str:
    return f"""#!/usr/bin/env bash
set -Eeuo pipefail
# {WINDOWS_HOST_WRAPPER_MARKER}

suite={shlex.quote(suite_wsl)}
exe="$suite/idevicerestore.exe"
request_marker={shlex.quote(request_marker_wsl)}
ack_marker={shlex.quote(ack_marker_wsl)}

if [[ ! -f "$exe" ]]; then
    echo "[iPhone5DualBooter] ERROR: Windows idevicerestore.exe is missing: $exe" >&2
    exit 90
fi

converted_args=()
restore_invocation=0
for argument in "$@"; do
    if [[ "$argument" == *.ipsw || "$argument" == *.IPSW ]]; then
        restore_invocation=1
    fi

    if [[ "$argument" == -* ]]; then
        converted_args+=("$argument")
    elif [[ -e "$argument" ]]; then
        absolute_path="$(realpath -m -- "$argument")"
        converted_args+=("$(wslpath -a -w -- "$absolute_path")")
    elif [[ "$argument" == /mnt/* ]]; then
        converted_args+=("$(wslpath -a -w -- "$argument")")
    else
        converted_args+=("$argument")
    fi
done

if [[ "$restore_invocation" -eq 1 ]]; then
    rm -f "$ack_marker"
    request_tmp="${{request_marker}}.tmp"
    {{
        printf 'WINDOWS_HOST_IDEVICERESTORE_REQUEST\\n'
        printf 'WSL_PID=%s\\n' "$$"
        printf 'ORIGINAL_ARGUMENTS='
        printf '%q ' "$@"
        printf '\\nWINDOWS_ARGUMENTS='
        printf '%q ' "${{converted_args[@]}}"
        printf '\\n'
    }} > "$request_tmp"
    mv -f "$request_tmp" "$request_marker"

    echo "[iPhone5DualBooter] Legacy requested idevicerestore. Waiting for the GUI to detach USB from WSL and acknowledge Windows ownership."
    checks=0
    while [[ ! -f "$ack_marker" ]]; do
        checks=$((checks + 1))
        if (( checks == 1 || checks % 50 == 0 )); then
            echo "[iPhone5DualBooter] Still waiting for Windows-host idevicerestore handoff acknowledgement ($checks checks)."
        fi
        sleep 0.1
    done
    rm -f "$request_marker" "$ack_marker"
fi

echo "[iPhone5DualBooter] Launching Windows-host idevicerestore.exe through WSL interoperability."
printf '[iPhone5DualBooter] Windows command: idevicerestore.exe'
printf ' %q' "${{converted_args[@]}}"
printf '\\n'

cd "$suite"
chmod +x "$exe" 2>/dev/null || true
set +e
"$exe" "${{converted_args[@]}}"
status=$?
set -e

echo "[iPhone5DualBooter] Windows-host idevicerestore.exe exited with code $status."
exit "$status"
"""


def install_windows_host_idevicerestore_wrapper(
    legacy_root: Path,
    suite_wsl: str,
    request_marker_wsl: str,
    ack_marker_wsl: str,
    log: LogFn,
) -> Path:
    target = (
        Path(legacy_root)
        / "bin"
        / "linux"
        / "x86_64"
        / "idevicerestore"
    )
    backup = target.with_name("idevicerestore.wsl-backup")

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        data = target.read_bytes()
        if WINDOWS_HOST_WRAPPER_MARKER.encode("utf-8") not in data:
            if not backup.is_file():
                shutil.copy2(target, backup)
                log(
                    "Saved the current Linux idevicerestore as: "
                    f"{backup}"
                )
    elif not backup.is_file():
        raise LegacyWSLError(
            "Legacy's Linux idevicerestore is missing, so the Windows-host "
            "wrapper cannot preserve a fallback copy."
        )

    wrapper = build_windows_host_idevicerestore_wrapper(
        suite_wsl,
        request_marker_wsl,
        ack_marker_wsl,
    )
    temporary = target.with_suffix(".windows-host-wrapper.tmp")
    temporary.write_text(wrapper, encoding="utf-8", newline="\n")
    temporary.chmod(0o755)
    temporary.replace(target)

    log(
        "Installed the Legacy interception wrapper. Legacy still calls its "
        "normal Linux path, but that wrapper launches Windows "
        "idevicerestore.exe with converted copies of the same arguments."
    )
    return backup


def restore_linux_idevicerestore_if_wrapped(
    legacy_root: Path,
    log: LogFn,
) -> None:
    target = (
        Path(legacy_root)
        / "bin"
        / "linux"
        / "x86_64"
        / "idevicerestore"
    )
    backup = target.with_name("idevicerestore.wsl-backup")

    if not target.is_file():
        return
    try:
        data = target.read_bytes()
    except OSError:
        return

    if WINDOWS_HOST_WRAPPER_MARKER.encode("utf-8") not in data:
        return
    if not backup.is_file():
        raise LegacyWSLError(
            "The Windows-host wrapper is installed, but its saved Linux "
            "idevicerestore fallback is missing."
        )

    shutil.copy2(backup, target)
    target.chmod(0o755)
    log("Restored Legacy's saved Linux idevicerestore fallback.")


def wait_for_windows_apple_device(
    preferred_busid: str,
    log: LogFn,
    timeout: float = 30.0,
    poll_interval: float = 0.15,
    device_supplier: Callable[[], list[USBIPDDevice]] = list_devices,
) -> USBIPDDevice:
    deadline = time.monotonic() + max(1.0, float(timeout))
    preferred = preferred_busid.strip()
    last_snapshot = ""

    while time.monotonic() < deadline:
        devices = device_supplier()
        candidates = [
            device
            for device in devices
            if device.is_apple and not device.is_attached
        ]

        exact = [
            device
            for device in candidates
            if device.busid == preferred
        ]
        if len(exact) == 1:
            return exact[0]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise USBIPDError(
                "More than one Apple USB device is visible to Windows. "
                "Disconnect the unrelated Apple device and retry."
            )

        snapshot = "; ".join(
            f"{device.mode_name} {device.busid} "
            f"{device.hardware_id or '(no VID:PID)'} [{device.state}]"
            for device in devices
            if device.is_apple
        ) or "no Apple USB device visible to Windows"

        if snapshot != last_snapshot:
            log(
                "Windows-host idevicerestore handoff is waiting for USB "
                "ownership: " + snapshot
            )
            last_snapshot = snapshot

        time.sleep(max(0.05, float(poll_interval)))

    raise USBIPDError(
        "Timed out waiting for the detached iPhone to become visible to "
        "Windows for host idevicerestore."
    )


def build_legacy_full_restore_shell_command(
    legacy_root_wsl: str,
    stock_ipsw_wsl: str,
    transcript_wsl: str,
    host_handoff_marker_wsl: str = "/tmp/iphone5dualbooter-host-handoff.txt",
    host_handoff_ack_wsl: str = "/tmp/iphone5dualbooter-host-handoff-ack.txt",
    ibec_retry_seconds: int = 45,
    windows_host_idevicerestore: bool = False,
) -> str:
    invocation = " ".join(
        shlex.quote(value)
        for value in (
            "stdbuf",
            "-oL",
            "-eL",
            "bash",
            "./restore.sh",
            "--jailbreak",
            stock_ipsw_wsl,
        )
    )
    lines = [
        "set -o pipefail",
        f"cd {shlex.quote(legacy_root_wsl)}",
        "printf '\\n============================================================\\n'",
        "printf 'iPhone5DualBooter: FULL Legacy iOS Kit restore\\n'",
        "printf 'The --jailbreak flag is ON. Legacy handles blobs, CustomJ,\\n'",
        "printf 'baseband, and the complete iOS 8.4.1 restore.\\n'",
        "printf '\\nBefore this window opened, the GUI required external pwnDFU.\\n'",
        "printf 'Leave the phone in pwnDFU; do not force-restart or repeat DFU buttons.\\n'",
        "printf '\\nInside Legacy iOS Kit:\\n'",
        "printf '  1. If it installs dependencies or updates and exits, rerun it.\\n'",
        "printf '  2. Confirm the screen says Jailbreak flag detected/enabled.\\n'",
        "printf '  3. Choose Restore/Downgrade, then iOS 8.4.1 / OTA Downgrade.\\n'",
        "printf '  4. When Legacy checks pwnDFU, it should detect PWND and skip exploit.\\n'",
        "printf '  5. Do not re-enter DFU or run a6meowing again in WSL.\\n'",
        "printf '  6. Do not close Legacy until it says the restore is complete.\\n'",
        (
            "printf '  7. Legacy idevicerestore backend: "
            + (
                "Windows host via Devjam81 wrapper.\\n'"
                if windows_host_idevicerestore
                else "patched Linux/WSL binary.\\n'"
            )
        ),
        "printf '============================================================\\n\\n'",
        (
            "export IPHONE5DUALBOOTER_IBEC_RETRY_SECONDS="
            f"{max(5, min(180, int(ibec_retry_seconds)))}"
        ),
        (
            "export IPHONE5DUALBOOTER_HOST_HANDOFF_FILE="
            f"{shlex.quote(host_handoff_marker_wsl)}"
        ),
        (
            "export IPHONE5DUALBOOTER_HOST_HANDOFF_ACK_FILE="
            f"{shlex.quote(host_handoff_ack_wsl)}"
        ),
        f"{invocation} 2>&1 | tee -a {shlex.quote(transcript_wsl)}",
        "exit ${PIPESTATUS[0]}",
    ]
    return "\n".join(lines)


def build_legacy_wsl_command(wsl: Path, distro: str, shell_command: str) -> list[str]:
    return [str(wsl), "-d", distro, "--", "bash", "-lc", shell_command]


def _tail(path: Path, lines: int = 180) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])



_ANSI_ESCAPE_RE = re.compile(
    r"""
    \x1B
    (?:
        \[[0-?]*[ -/]*[@-~]
        |
        \][^\x07]*(?:\x07|\x1B\\)
    )
    """,
    re.VERBOSE,
)
_TERMINAL_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_PROGRESS_RE = re.compile(r"\]\s*(\d{1,3}(?:\.\d+)?)%")


@dataclass
class LegacyTranscriptFollower:
    offset: int = 0
    remainder: str = ""
    last_progress_bucket: int = -1
    last_activity: float = 0.0
    waiting_for_device_since: float = 0.0
    replug_popup_shown: bool = False

    def _clean_line(self, line: str) -> str:
        cleaned = _ANSI_ESCAPE_RE.sub("", line)
        cleaned = _TERMINAL_CONTROL_RE.sub("", cleaned)
        cleaned = cleaned.replace("\r", "").strip()
        cleaned = re.sub(r"\[\d+(?:;\d+)*[A-Za-z]", "", cleaned)
        return cleaned.strip()

    def poll(self, path: Path, log: LogFn) -> int:
        try:
            with path.open("rb") as stream:
                stream.seek(self.offset)
                payload = stream.read()
                self.offset = stream.tell()
        except OSError:
            return 0

        if not payload:
            return 0

        text = self.remainder + payload.decode("utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        self.remainder = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            self.remainder = lines.pop()

        emitted = 0
        for raw in lines:
            line = self._clean_line(raw)
            if not line:
                continue

            progress = _PROGRESS_RE.search(line)
            if progress:
                value = min(100.0, float(progress.group(1)))
                bucket = int(value // 5) * 5
                if value >= 100:
                    bucket = 100
                if bucket <= self.last_progress_bucket:
                    continue
                self.last_progress_bucket = bucket
                log(f"Legacy progress: {value:.1f}%")
                emitted += 1
                continue

            if re.fullmatch(r"[-=>\s*]+", line):
                continue
            if line.startswith(("Updating files:", "PING ")):
                continue

            lower = line.casefold()
            if "waiting for device..." in lower:
                self.waiting_for_device_since = time.monotonic()
                self.replug_popup_shown = False
            elif (
                "is now connected in restore mode" in lower
                or "successfully entered restore mode" in lower
                or "connected to device in restore mode" in lower
            ):
                self.waiting_for_device_since = 0.0
                self.replug_popup_shown = True

            log(f"Legacy: {line}")
            emitted += 1

        if emitted:
            self.last_activity = time.monotonic()
        return emitted

    def replug_popup_due(
        self,
        now: float | None = None,
        delay_seconds: float = 5.0,
    ) -> bool:
        if (
            self.waiting_for_device_since <= 0.0
            or self.replug_popup_shown
        ):
            return False

        current = time.monotonic() if now is None else float(now)
        if current - self.waiting_for_device_since < max(
            0.0,
            float(delay_seconds),
        ):
            return False

        self.replug_popup_shown = True
        return True


def show_restore_mode_replug_popup(log: LogFn) -> None:
    """
    Display one instruction popup only. USB handling remains exactly v0.4.28.
    """
    message = (
        "Legacy has been waiting for the iPhone for 5 seconds.\n\n"
        "1. Unplug the iPhone USB cable.\n"
        "2. Wait 2 seconds.\n"
        "3. Plug the same cable back in.\n"
        "4. Keep Legacy iOS Kit open.\n"
        "5. Click OK.\n"
        "6. After pressing OK, if the restore still says it is waiting "
        "for the device, repeat: unplug the cable, wait 2 seconds, and "
        "plug it back in. Keep repeating that cycle until the restore "
        "detects the iPhone.\n\n"
        "Do not close or restart Legacy during these reconnects."
    )

    log(
        "MANUAL USB REPLUG: unplug, wait two seconds, reconnect, and click "
        "OK. If the restore still waits for the device afterward, repeat "
        "unplug -> wait two seconds -> reconnect until it detects the "
        "iPhone. Keep Legacy open."
    )

    if os.name != "nt":
        return

    try:
        import ctypes

        MB_OK = 0x00000000
        MB_ICONINFORMATION = 0x00000040
        MB_SETFOREGROUND = 0x00010000
        MB_TOPMOST = 0x00040000
        ctypes.windll.user32.MessageBoxW(
            None,
            message,
            "iPhone5DualBooter — Reconnect iPhone",
            MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST,
        )
    except Exception as exc:
        log(
            "Could not display the Windows popup. Follow the manual "
            f"replug instruction in this log. Popup error: {exc}"
        )

def validate_wsl_restore_inputs(settings: WorkflowSettings) -> None:
    if os.name != "nt":
        raise LegacyWSLError("The WSL restore mode is only available on Windows.")
    if not settings.stock_host_ipsw or not settings.stock_host_ipsw.is_file():
        raise LegacyWSLError("Select the stock iOS 8.4.1 Restore IPSW.")
    distro = settings.legacy_wsl_distro.strip()
    if not distro or not _VALID_DISTRO.fullmatch(distro):
        raise LegacyWSLError("Enter a valid WSL distro name.")

    stock = inspect_ipsw(settings.stock_host_ipsw)
    if stock.product_version != "8.4.1":
        raise LegacyWSLError(
            f"The host IPSW must be iOS 8.4.1, but it reports {stock.product_version}."
        )


def run_legacy_wsl_restore(
    settings: WorkflowSettings,
    app_root: Path,
    log: LogFn,
    manual_prompt: LegacyPromptFn | None,
    external_pwn_prompt: ExternalPwnPromptFn | None = None,
) -> LegacyRestoreResult:
    validate_wsl_restore_inputs(settings)
    assert settings.stock_host_ipsw is not None

    wsl = find_wsl()
    distro = settings.legacy_wsl_distro.strip()
    installed = list_wsl_distros(wsl)
    if distro not in installed:
        raise LegacyWSLError(
            f"WSL distro '{distro}' was not found. Installed: "
            + (", ".join(installed) if installed else "none")
        )

    legacy_root = ensure_legacy_kit(
        app_root,
        settings.legacy_kit_dir,
        settings.auto_download_legacy_kit,
        log,
    )

    watcher: AppleUSBWatcher | None = None
    cleanup_completed = False

    if settings.auto_attach_usb_to_wsl:
        if not settings.usbipd_busid.strip():
            raise LegacyWSLError(
                "Select the Apple USB device/BUSID before starting."
            )
        watcher = AppleUSBWatcher(
            distro=distro,
            preferred_busid=settings.usbipd_busid,
            log=log,
            wsl=wsl,
        )

    try:
        transcript = app_root / "tools" / "legacy-wsl-full-restore.log"
        handoff_marker = (
            app_root / "tools" / "iphone5dualbooter-host-handoff.txt"
        )
        handoff_ack = (
            app_root / "tools" / "iphone5dualbooter-host-handoff-ack.txt"
        )
        windows_idr_marker = (
            app_root
            / "tools"
            / "iphone5dualbooter-windows-idevicerestore-request.txt"
        )
        windows_idr_ack = (
            app_root
            / "tools"
            / "iphone5dualbooter-windows-idevicerestore-ack.txt"
        )
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.unlink(missing_ok=True)
        handoff_marker.unlink(missing_ok=True)
        handoff_ack.unlink(missing_ok=True)
        windows_idr_marker.unlink(missing_ok=True)
        windows_idr_ack.unlink(missing_ok=True)

        legacy_wsl = windows_to_wsl_path(wsl, distro, legacy_root)
        stock_wsl = windows_to_wsl_path(wsl, distro, settings.stock_host_ipsw)
        transcript_wsl = windows_to_wsl_path(wsl, distro, transcript)
        handoff_marker_wsl = windows_to_wsl_path(
            wsl,
            distro,
            handoff_marker,
        )
        handoff_ack_wsl = windows_to_wsl_path(
            wsl,
            distro,
            handoff_ack,
        )
        windows_idr_marker_wsl = windows_to_wsl_path(
            wsl,
            distro,
            windows_idr_marker,
        )
        windows_idr_ack_wsl = windows_to_wsl_path(
            wsl,
            distro,
            windows_idr_ack,
        )
        use_windows_host_idevicerestore = False
        if settings.use_windows_idevicerestore:
            log(
                "Transition-preservation hotfix: ignoring the saved Windows-"
                "host idevicerestore setting because that executable is not "
                "patched with the iBEC/Recovery/ASR fixes. Using the source-"
                "patched Linux/WSL backend instead."
            )

        if use_windows_host_idevicerestore:
            suite = ensure_windows_idevicerestore_suite(
                app_root=app_root,
                configured_directory=settings.windows_idevicerestore_dir,
                auto_download=settings.auto_download_windows_idevicerestore,
                log=log,
            )
            suite_wsl = windows_to_wsl_path(wsl, distro, suite)
            install_windows_host_idevicerestore_wrapper(
                legacy_root=legacy_root,
                suite_wsl=suite_wsl,
                request_marker_wsl=windows_idr_marker_wsl,
                ack_marker_wsl=windows_idr_ack_wsl,
                log=log,
            )
            log(
                "Windows-host idevicerestore mode is enabled. WSL remains "
                "responsible for Legacy preparation and pwned iBSS, then "
                "USB ownership moves to Windows immediately before "
                "idevicerestore.exe."
            )
        else:
            restore_linux_idevicerestore_if_wrapped(legacy_root, log)

        shell = build_legacy_full_restore_shell_command(
            legacy_wsl,
            stock_wsl,
            transcript_wsl,
            handoff_marker_wsl,
            handoff_ack_wsl,
            settings.ibec_retry_seconds,
            use_windows_host_idevicerestore,
        )
        command = build_legacy_wsl_command(wsl, distro, shell)

        attempt = 0
        live_handoff_count = 0
        while True:
            attempt += 1

            if not use_windows_host_idevicerestore:
                ensure_patched_idevicerestore(
                    legacy_root=legacy_root,
                    app_root=app_root,
                    wsl=wsl,
                    distro=distro,
                    retry_seconds=settings.ibec_retry_seconds,
                    allow_full_fallback=settings.full_rebuild_fallback,
                    log=log,
                )

            if settings.require_external_pwndfu:
                if watcher is not None and watcher.is_running:
                    watcher.stop()

                try:
                    detach_attached_apple_devices(
                        log,
                        watcher.current_busid if watcher else settings.usbipd_busid,
                        distro,
                        wsl,
                    )
                except Exception as exc:
                    log(f"Pre-pwn USB detach note: {exc}")

                if external_pwn_prompt is None:
                    raise LegacyWSLError(
                        "External pwnDFU confirmation is required, but no "
                        "confirmation UI is available."
                    )

                log(
                    f"Waiting for external A6 pwnDFU confirmation before "
                    f"Legacy launch #{attempt}..."
                )
                if not external_pwn_prompt(attempt):
                    raise LegacyWSLError(
                        "External pwnDFU confirmation was cancelled."
                    )

                log(
                    "User confirmed the iPhone is externally pwned. "
                    "Reconnect it to Windows without force-restarting it."
                )
            if watcher is not None:
                watcher.start()
                attach_checks = 0
                log(
                    "Actively retrying USB attachment until the pwnDFU iPhone "
                    "is actually visible inside WSL. Legacy will not launch early."
                )
                while True:
                    attach_checks += 1
                    watcher.wait_until_attached(timeout=0.50)
                    visible, lsusb_output = wsl_lsusb(wsl, distro)
                    if visible is True:
                        log(
                            "The externally pwned Apple DFU device is attached "
                            "and visible inside WSL."
                        )
                        break
                    if visible is None and watcher.wait_until_attached(timeout=0.0):
                        log(
                            "usbipd reports the iPhone attached; lsusb is not "
                            "available yet, so Legacy may install it."
                        )
                        break
                    if (attach_checks == 1) or ((attach_checks % 20) == 0):
                        log(
                            "USB attachment is not ready yet; continuing active "
                            f"attach/visibility retry #{attach_checks}."
                        )
                        if lsusb_output.strip() and (attach_checks % 20) == 0:
                            log(lsusb_output.strip())

            handoff_marker.unlink(missing_ok=True)
            handoff_ack.unlink(missing_ok=True)
            windows_idr_marker.unlink(missing_ok=True)
            windows_idr_ack.unlink(missing_ok=True)

            with transcript.open("a", encoding="utf-8") as stream:
                stream.write(
                    f"\n\n===== Full Legacy restore launch #{attempt} =====\n"
                )

            log(
                f"Opening the FULL Legacy iOS Kit restore in WSL "
                f"(launch #{attempt})..."
            )
            log("The jailbreak flag is enabled by the launcher.")
            if settings.require_external_pwndfu:
                log(
                    "Do not repeat the DFU button sequence inside Legacy. "
                    "Its pwn helper should read the existing PWND marker and "
                    "skip executing the exploit."
                )
            log(
                "The Apple USB watcher will automatically follow recovery, "
                "DFU/pwnDFU, and normal-mode re-enumerations."
            )

            creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            process = subprocess.Popen(command, creationflags=creationflags)
            try:
                initial_offset = transcript.stat().st_size
            except OSError:
                initial_offset = 0
            transcript_follower = LegacyTranscriptFollower(
                offset=initial_offset,
                last_activity=time.monotonic(),
            )
            last_heartbeat = time.monotonic()
            windows_host_restore_active = False
            windows_host_handoff_count = 0

            while process.poll() is None:
                transcript_follower.poll(transcript, log)

                now = time.monotonic()

                if transcript_follower.replug_popup_due(
                    now,
                    delay_seconds=5.0,
                ):
                    show_restore_mode_replug_popup(log)

                if now - last_heartbeat >= 15.0:
                    if windows_host_restore_active:
                        usb_state = (
                            "owned by Windows-host idevicerestore.exe"
                        )
                    else:
                        visible, _ = wsl_lsusb(wsl, distro)
                        usb_state = (
                            "visible inside WSL"
                            if visible is True
                            else "not currently visible inside WSL"
                            if visible is False
                            else "WSL USB visibility unavailable"
                        )
                    log(
                        "Legacy is still running; live transcript monitoring "
                        f"is active and the iPhone is {usb_state}."
                    )
                    last_heartbeat = now

                if (
                    use_windows_host_idevicerestore
                    and windows_idr_marker.is_file()
                    and not windows_host_restore_active
                ):
                    windows_host_handoff_count += 1
                    log(
                        "Legacy reached its idevicerestore call. Performing "
                        "Windows-host USB handoff "
                        f"#{windows_host_handoff_count}: stopping usbipd "
                        "auto-attach before the Windows executable starts."
                    )

                    if watcher is not None and watcher.is_running:
                        watcher.stop()

                    preferred_busid = (
                        watcher.current_busid
                        if watcher
                        else settings.usbipd_busid
                    )
                    detach_attached_apple_devices(
                        log,
                        preferred_busid,
                        distro,
                        wsl,
                        stop_keepalive=False,
                    )

                    windows_device = wait_for_windows_apple_device(
                        preferred_busid,
                        log,
                    )
                    if watcher is not None:
                        watcher.current_busid = windows_device.busid
                        watcher.preferred_busid = windows_device.busid

                    windows_idr_marker.unlink(missing_ok=True)
                    temporary_ack = windows_idr_ack.with_suffix(".tmp")
                    temporary_ack.write_text(
                        "WINDOWS_HOST_IDEVICERESTORE_READY\n",
                        encoding="utf-8",
                    )
                    temporary_ack.replace(windows_idr_ack)
                    windows_host_restore_active = True

                    log(
                        "The iPhone is detached from WSL and visible to "
                        "Windows at BUSID "
                        f"{windows_device.busid} "
                        f"({windows_device.hardware_id or 'unknown VID:PID'}). "
                        "Acknowledged the wrapper; Windows "
                        "idevicerestore.exe now receives Legacy's original "
                        "flags and a converted Windows IPSW path. The WSL "
                        "USB watcher will remain stopped for the entire "
                        "host restore."
                    )
                    time.sleep(0.10)
                    continue

                if not handoff_marker.is_file():
                    time.sleep(0.10)
                    continue

                live_handoff_count += 1
                log(
                    "The running idevicerestore process closed its USB "
                    "handles and paused. Performing live usbipd handoff "
                    f"#{live_handoff_count} without quitting or relaunching "
                    "Legacy."
                )

                if watcher is not None and watcher.is_running:
                    watcher.stop(timeout=10.0)

                preferred_busid = (
                    watcher.current_busid
                    if watcher
                    else settings.usbipd_busid
                )
                if not preferred_busid:
                    raise LegacyWSLError(
                        "The live DFU-to-Recovery handoff has no USB BUSID."
                    )

                log(
                    "Performing one Detach to Windows action for BUSID "
                    f"{preferred_busid}. This uses the same detach_from_wsl() "
                    "path as the GUI button and does not globally terminate "
                    "unrelated WSL processes."
                )
                detach_from_wsl(
                    preferred_busid,
                    log,
                    distro=distro,
                    wsl=wsl,
                )

                recovery_device = wait_for_windows_recovery(
                    preferred_busid,
                    log,
                )
                log(
                    "Windows detected the post-iBEC Recovery device at "
                    f"BUSID {recovery_device.busid} "
                    f"({recovery_device.hardware_id or 'unknown VID:PID'})."
                )

                if watcher is None:
                    raise LegacyWSLError(
                        "Live USB handoff requires the Apple USB watcher."
                    )

                watcher.current_busid = recovery_device.busid
                watcher.preferred_busid = recovery_device.busid
                watcher.start()

                attach_checks = 0
                while True:
                    attach_checks += 1
                    watcher.wait_until_attached(timeout=0.50)
                    visible, lsusb_output = wsl_lsusb(wsl, distro)
                    if visible is True:
                        break
                    if (
                        visible is None
                        and watcher.wait_until_attached(timeout=0.0)
                    ):
                        break
                    if attach_checks == 1 or attach_checks % 20 == 0:
                        log(
                            "Recovery is visible to Windows but is not ready "
                            "inside WSL yet; continuing live reattach check "
                            f"#{attach_checks}."
                        )
                        if (
                            lsusb_output.strip()
                            and attach_checks % 20 == 0
                        ):
                            log(lsusb_output.strip())

                handoff_marker.unlink(missing_ok=True)
                temporary_ack = handoff_ack.with_suffix(".tmp")
                temporary_ack.write_text(
                    "LIVE_HOST_USB_HANDOFF_READY\n",
                    encoding="utf-8",
                )
                temporary_ack.replace(handoff_ack)

                log(
                    "Recovery is attached and visible inside WSL. Signalled "
                    "the paused idevicerestore process to continue at the "
                    "same restore step; Legacy was never restarted."
                )

            exit_code = process.wait()
            transcript_follower.poll(transcript, log)

            tail = _tail(transcript)
            if tail:
                log("--- Legacy transcript tail ---")
                for line in tail.splitlines():
                    log(line)
                log("--- End Legacy transcript ---")

            if manual_prompt is None:
                if exit_code != 0:
                    raise LegacyWSLError(
                        f"Legacy exited with code {exit_code}. "
                        f"Transcript: {transcript}"
                    )
                action = "continue"
            else:
                action = manual_prompt(
                    exit_code,
                    transcript,
                    attempt,
                    "restore_exited",
                )

            if action == "rerun":
                continue

            if action == "cancel":
                raise LegacyWSLError(
                    f"Legacy restore was cancelled by the user. "
                    f"Transcript: {transcript}"
                )

            if action != "continue":
                raise LegacyWSLError(f"Unknown Legacy action: {action!r}")

            if watcher is not None:
                watcher.stop()

            if settings.auto_detach_usb_after_restore:
                detach_attached_apple_devices(
                    log,
                    watcher.current_busid if watcher else settings.usbipd_busid,
                    distro,
                    wsl,
                )
            else:
                stop_wsl_keepalive(distro, log, wsl)

            windows_idr_marker.unlink(missing_ok=True)
            windows_idr_ack.unlink(missing_ok=True)
            cleanup_completed = True
            time.sleep(3)

            return LegacyRestoreResult(
                legacy_root=legacy_root,
                transcript=transcript,
                exit_code=exit_code,
            )

    except BaseException:
        try:
            windows_idr_marker.unlink(missing_ok=True)
            windows_idr_ack.unlink(missing_ok=True)
        except Exception:
            pass
        if watcher is not None:
            watcher.stop()
        if not cleanup_completed and settings.auto_attach_usb_to_wsl:
            try:
                detach_attached_apple_devices(
                    log,
                    watcher.current_busid if watcher else settings.usbipd_busid,
                    distro,
                    wsl,
                )
            except Exception as exc:
                log(f"USB watcher cleanup warning: {exc}")
        raise


def run_legacy_native_restore(
    settings: WorkflowSettings,
    app_root: Path,
    log: LogFn,
) -> None:
    if os.name == "nt":
        raise LegacyWSLError("Use the WSL full restore mode on Windows.")
    if not settings.stock_host_ipsw or not settings.stock_host_ipsw.is_file():
        raise LegacyWSLError("Select the stock iOS 8.4.1 Restore IPSW.")

    legacy_root = ensure_legacy_kit(
        app_root,
        settings.legacy_kit_dir,
        settings.auto_download_legacy_kit,
        log,
    )
    restore_sh = legacy_root / "restore.sh"
    command = [
        "/bin/bash",
        str(restore_sh),
        "--jailbreak",
        str(settings.stock_host_ipsw),
    ]
    log("Launching Legacy iOS Kit with the jailbreak flag enabled.")
    completed = subprocess.run(command, cwd=str(legacy_root), check=False)
    if completed.returncode != 0:
        raise LegacyWSLError(
            f"Legacy iOS Kit exited with code {completed.returncode}."
        )

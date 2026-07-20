from __future__ import annotations

from dataclasses import dataclass
import atexit
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable


LogFn = Callable[[str], None]
_VALID_BUSID = re.compile(r"^\d+-\d+(?:\.\d+)*$")
_VALID_DISTRO = re.compile(r"^[^\r\n\x00]+$")
_HARDWARE_ID = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{4}$")
_WSL_KEEPALIVES: dict[str, subprocess.Popen[bytes]] = {}
_USBIPD_OPERATION_LOCK = threading.RLock()
_USBIPD_AUTO_ATTACH: dict[str, subprocess.Popen[str]] = {}
_USBIPD_AUTO_ATTACH_THREADS: dict[str, threading.Thread] = {}
_USBIPD_AUTO_ATTACH_HARDWARE_IDS: dict[str, str] = {}
_APPLE_WORDS = (
    "apple",
    "iphone",
    "ipad",
    "ipod",
    "mobile device",
    "recovery mode",
    "dfu",
)


class USBIPDError(RuntimeError):
    pass


@dataclass(frozen=True)
class USBIPDDevice:
    busid: str
    device: str
    state: str
    hardware_id: str = ""

    @property
    def is_apple(self) -> bool:
        if self.hardware_id.casefold().startswith("05ac:"):
            return True
        text = f"{self.hardware_id} {self.device}".casefold()
        return any(word in text for word in _APPLE_WORDS)

    @property
    def is_attached(self) -> bool:
        return self.state.casefold().startswith("attached")

    @property
    def is_shared(self) -> bool:
        state = self.state.casefold()
        return (
            self.is_attached
            or state.startswith("shared")
            or state.startswith("persisted")
        )

    @property
    def mode_name(self) -> str:
        text = f"{self.hardware_id} {self.device}".casefold()
        if "dfu" in text or self.hardware_id.casefold() == "05ac:1227":
            return "DFU/pwnDFU"
        if (
            "recovery" in text
            or "iboot" in text
            or self.hardware_id.casefold() in {
                "05ac:1280", "05ac:1281", "05ac:1282", "05ac:1283"
            }
        ):
            return "Recovery"
        if "mobile device" in text or "iphone" in text:
            return "Normal"
        return "Apple USB"


def validate_busid(busid: str) -> str:
    value = busid.strip()
    if not _VALID_BUSID.fullmatch(value):
        raise USBIPDError("USBIPD bus ID must look like 2-4 or 1-3.2.")
    return value



def validate_distro(distro: str) -> str:
    value = distro.strip()
    if not value or not _VALID_DISTRO.fullmatch(value):
        raise USBIPDError("Enter a valid WSL distribution name.")
    return value


def find_wsl() -> Path:
    located = shutil.which("wsl.exe") or shutil.which("wsl")
    if located:
        return Path(located)
    candidate = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "wsl.exe"
    )
    if candidate.is_file():
        return candidate
    raise USBIPDError("WSL was not found. Install WSL 2 and Ubuntu first.")


def build_wsl_keepalive_commands(
    wsl: Path,
    distro: str,
) -> list[list[str]]:
    """
    Return commands that keep a WSL distro alive without passing a quoted shell
    program through wsl.exe. The second command is a compatibility fallback.
    """
    distro_name = validate_distro(distro)
    executable = str(wsl)
    return [
        [
            executable,
            "-d",
            distro_name,
            "--exec",
            "sleep",
            "infinity",
        ],
        [
            executable,
            "-d",
            distro_name,
            "--exec",
            "tail",
            "-f",
            "/dev/null",
        ],
    ]


def _hidden_creationflags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _read_immediate_process_error(
    process: subprocess.Popen[bytes],
) -> str:
    if process.stderr is None:
        return ""
    try:
        payload = process.stderr.read() or b""
    except OSError:
        return ""
    return payload.decode(errors="replace").strip()


def _terminate_keepalive_process(
    process: subprocess.Popen[bytes],
) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def _cleanup_all_wsl_keepalives() -> None:
    for busid, process in list(_USBIPD_AUTO_ATTACH.items()):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        _USBIPD_AUTO_ATTACH.pop(busid, None)
        _USBIPD_AUTO_ATTACH_THREADS.pop(busid, None)
        _USBIPD_AUTO_ATTACH_HARDWARE_IDS.pop(busid, None)

    for distro_name, process in list(_WSL_KEEPALIVES.items()):
        _terminate_keepalive_process(process)
        _WSL_KEEPALIVES.pop(distro_name, None)


atexit.register(_cleanup_all_wsl_keepalives)


def ensure_wsl_running(
    distro: str,
    log: LogFn,
    wsl: Path | None = None,
) -> Path:
    distro_name = validate_distro(distro)
    executable = wsl or find_wsl()

    existing = _WSL_KEEPALIVES.get(distro_name)
    if existing is not None and existing.poll() is None:
        log(f"WSL distro '{distro_name}' is already being kept running.")
        return executable
    _WSL_KEEPALIVES.pop(distro_name, None)

    log(f"Starting WSL distro '{distro_name}' for USB passthrough...")

    errors: list[str] = []
    for command in build_wsl_keepalive_commands(executable, distro_name):
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=_hidden_creationflags(),
            )
        except OSError as exc:
            errors.append(f"{' '.join(command)}: {exc}")
            continue

        time.sleep(1.0)
        if process.poll() is None:
            _WSL_KEEPALIVES[distro_name] = process
            log(
                f"WSL distro '{distro_name}' is running "
                f"(keepalive PID {process.pid})."
            )
            return executable

        output = _read_immediate_process_error(process)
        errors.append(
            f"{' '.join(command)} exited with code {process.returncode}: "
            f"{output or '(no output)'}"
        )

    raise USBIPDError(
        f"Could not keep WSL distro '{distro_name}' running.\n\n"
        + "\n".join(errors)
    )


def stop_wsl_keepalive(
    distro: str,
    log: LogFn,
    wsl: Path | None = None,
) -> None:
    distro_name = validate_distro(distro)
    process = _WSL_KEEPALIVES.pop(distro_name, None)
    if process is None:
        return

    _terminate_keepalive_process(process)
    log(f"Stopped the temporary WSL keepalive for '{distro_name}'.")


def is_no_running_wsl_error(output: str) -> bool:
    lower = output.casefold()
    return (
        "there is no wsl 2 distribution running" in lower
        or "keep a command prompt to a wsl 2 distribution open" in lower
    )


def find_usbipd() -> Path:
    located = shutil.which("usbipd.exe") or shutil.which("usbipd")
    if located:
        return Path(located)
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "usbipd-win"
        / "usbipd.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "usbipd"
        / "usbipd.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise USBIPDError(
        "usbipd-win was not found. Install it from an Administrator terminal with:\n\n"
        "winget install usbipd\n\nThen restart this app."
    )


def parse_usbipd_list(text: str) -> list[USBIPDDevice]:
    devices: list[USBIPDDevice] = []
    for raw in text.replace("\r\n", "\n").splitlines():
        line = raw.strip()
        if not line or line.casefold().startswith(("connected:", "persisted:", "busid")):
            continue

        columns = [part.strip() for part in re.split(r"\s{2,}", line) if part.strip()]
        if len(columns) < 2 or not _VALID_BUSID.fullmatch(columns[0]):
            continue

        busid = columns[0]
        hardware_id = ""
        body_start = 1
        if len(columns) >= 3 and _HARDWARE_ID.fullmatch(columns[1]):
            hardware_id = columns[1].lower()
            body_start = 2

        if len(columns) <= body_start:
            device, state = "", ""
        elif len(columns) == body_start + 1:
            device, state = columns[body_start], ""
        else:
            device = "  ".join(columns[body_start:-1])
            state = columns[-1]

        devices.append(
            USBIPDDevice(
                busid=busid,
                hardware_id=hardware_id,
                device=device,
                state=state,
            )
        )
    return devices


def list_devices() -> list[USBIPDDevice]:
    executable = find_usbipd()
    completed = subprocess.run(
        [str(executable), "list"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise USBIPDError(
            "usbipd list failed:\n" + (completed.stdout or "(no output)")
        )
    return parse_usbipd_list(completed.stdout or "")


def _device_for_busid(busid: str) -> USBIPDDevice | None:
    wanted = validate_busid(busid)
    for device in list_devices():
        if device.busid == wanted:
            return device
    return None


def bind_elevated(busid: str, log: LogFn) -> None:
    value = validate_busid(busid)
    executable = find_usbipd()
    log(f"Requesting Administrator permission to share USB device {value}...")
    script = (
        "$p = Start-Process "
        f"-FilePath '{str(executable).replace(chr(39), chr(39)*2)}' "
        f"-ArgumentList @('bind','--busid','{value}') "
        "-Verb RunAs -Wait -PassThru; exit $p.ExitCode"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=False,
    )
    if completed.returncode != 0:
        raise USBIPDError(
            "USB sharing was not completed. Accept the UAC prompt, or run this once "
            f"from an Administrator terminal:\n\nusbipd bind --busid {value}"
        )



def build_usbipd_auto_attach_command(
    executable: Path,
    busid: str,
) -> list[str]:
    return [
        str(executable),
        "attach",
        "--wsl",
        "--auto-attach",
        "--busid",
        validate_busid(busid),
    ]


def usbipd_supports_auto_attach(
    executable: Path | None = None,
) -> bool:
    usbipd = executable or find_usbipd()
    completed = subprocess.run(
        [str(usbipd), "attach", "--help"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        check=False,
    )
    return "--auto-attach" in (completed.stdout or "")


def _auto_attach_log_reader(
    busid: str,
    process: subprocess.Popen[str],
    log: LogFn,
) -> None:
    stream = process.stdout
    if stream is None:
        return
    try:
        for raw in stream:
            line = raw.strip()
            if line:
                log(f"usbipd auto-attach {busid}: {line}")
    except Exception as exc:
        log(f"usbipd auto-attach {busid} log warning: {exc}")


def stop_usbipd_auto_attach(
    busid: str,
    log: LogFn,
) -> None:
    value = validate_busid(busid)
    process = _USBIPD_AUTO_ATTACH.pop(value, None)
    _USBIPD_AUTO_ATTACH_THREADS.pop(value, None)
    _USBIPD_AUTO_ATTACH_HARDWARE_IDS.pop(value, None)
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
    log(f"Stopped usbipd native auto-attach for BUSID {value}.")


def stop_all_usbipd_auto_attach(log: LogFn) -> None:
    for busid in list(_USBIPD_AUTO_ATTACH):
        stop_usbipd_auto_attach(busid, log)


def _find_external_usbipd_wsl_attach_processes() -> list[tuple[int, str, str]]:
    """
    Find usbipd auto-attach client process trees that are not necessarily
    tracked by this Python process.

    This catches stale loops left by a crashed/older app instance. We avoid
    matching the usbipd Windows service and ordinary `usbipd list`/`detach`
    commands; only attach-to-WSL command lines are selected.
    """
    if os.name != "nt":
        return []

    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        return []

    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-CimInstance Win32_Process | ForEach-Object {
    $name = [string]$_.Name
    $cmd = [string]$_.CommandLine
    $lower = $cmd.ToLowerInvariant()
    $isUsbipdAttach = (
        $name -ieq 'usbipd.exe' -and
        $lower -match '\battach\b' -and
        ($lower.Contains('--wsl') -or $lower.Contains('--auto-attach'))
    )
    $isUsbipdWslChild = (
        $name -ieq 'wsl.exe' -and
        ($lower.Contains('usbipd') -or $lower.Contains('--auto-attach'))
    )
    if ($isUsbipdAttach -or $isUsbipdWslChild) {
        $safeCmd = $cmd.Replace("`r", ' ').Replace("`n", ' ')
        Write-Output ("{0}|{1}|{2}" -f $_.ProcessId, $name, $safeCmd)
    }
}
"""
    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        check=False,
        creationflags=_hidden_creationflags(),
    )
    matches: list[tuple[int, str, str]] = []
    for raw in (completed.stdout or "").splitlines():
        parts = raw.strip().split("|", 2)
        if len(parts) != 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        matches.append((pid, parts[1], parts[2]))
    return matches


def terminate_all_usbipd_wsl_attach_processes(log: LogFn) -> None:
    """
    Stop both this app's tracked auto-attach loops and any stale external
    usbipd/wsl attach process trees left by previous runs.

    taskkill /T is intentional: usbipd --auto-attach starts a WSL-side child
    monitor, and terminating only the parent can leave that child able to
    reclaim the iPhone immediately after detach.
    """
    stop_all_usbipd_auto_attach(log)
    if os.name != "nt":
        return

    taskkill = shutil.which("taskkill.exe") or shutil.which("taskkill")
    if not taskkill:
        log(
            "USB handoff warning: taskkill.exe was not found, so stale "
            "usbipd auto-attach process trees could not be force-terminated."
        )
        return

    killed: set[int] = set()
    for _ in range(4):
        matches = [
            item for item in _find_external_usbipd_wsl_attach_processes()
            if item[0] not in killed
        ]
        if not matches:
            break
        for pid, name, command_line in matches:
            completed = subprocess.run(
                [taskkill, "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                check=False,
                creationflags=_hidden_creationflags(),
            )
            output = (completed.stdout or "").strip()
            if completed.returncode == 0 or "not found" in output.casefold():
                killed.add(pid)
                log(
                    "Force-stopped stale WSL USB attach process tree: "
                    f"{name} PID {pid}."
                )
            else:
                log(
                    "USB handoff warning: could not terminate stale attach "
                    f"process {name} PID {pid}: {output or '(no output)'}"
                )
        time.sleep(0.35)

    leftovers = _find_external_usbipd_wsl_attach_processes()
    if leftovers:
        description = "; ".join(
            f"{name} PID {pid}" for pid, name, _ in leftovers
        )
        raise USBIPDError(
            "Could not stop every usbipd/WSL auto-attach process before the "
            "Windows-host restore. Remaining process(es): " + description
        )
    if killed:
        log(
            "Confirmed that all detected usbipd/WSL auto-attach process "
            "trees are terminated."
        )


def start_usbipd_auto_attach(
    busid: str,
    distro: str,
    log: LogFn,
    wsl: Path | None = None,
) -> subprocess.Popen[str]:
    value = validate_busid(busid)
    distro_name = validate_distro(distro)

    with _USBIPD_OPERATION_LOCK:
        usbipd = find_usbipd()
        ensure_wsl_running(distro_name, log, wsl)

        device = _device_for_busid(value)
        if device is None:
            raise USBIPDError(
                f"USB bus ID {value} is not currently present."
            )

        existing = _USBIPD_AUTO_ATTACH.get(value)
        existing_hardware_id = _USBIPD_AUTO_ATTACH_HARDWARE_IDS.get(
            value,
            "",
        )
        current_hardware_id = device.hardware_id.casefold()

        if existing is not None and existing.poll() is None:
            same_generation = (
                not existing_hardware_id
                or existing_hardware_id == current_hardware_id
            )
            if same_generation and device.is_shared:
                return existing

            reason = (
                "Apple USB mode changed"
                if existing_hardware_id != current_hardware_id
                else "the new enumeration is not shared"
            )
            log(
                f"Restarting usbipd auto-attach for BUSID {value}: "
                f"{reason} "
                f"({existing_hardware_id or 'unknown'} -> "
                f"{current_hardware_id or 'unknown'})."
            )
            stop_usbipd_auto_attach(value, log)

        _USBIPD_AUTO_ATTACH.pop(value, None)

        if not usbipd_supports_auto_attach(usbipd):
            raise USBIPDError(
                "This usbipd-win version does not support --auto-attach. "
                "Update usbipd-win, or the app will use its slower fallback watcher."
            )

        if not device.is_shared:
            bind_elevated(value, log)

        command = build_usbipd_auto_attach_command(usbipd, value)
        log(
            f"Starting usbipd native auto-attach for BUSID {value}. "
            "This stays active through USB resets and re-enumeration."
        )
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            creationflags=_hidden_creationflags(),
        )

        time.sleep(0.75)
        if process.poll() is not None:
            output = ""
            if process.stdout is not None:
                try:
                    output = process.stdout.read() or ""
                except OSError:
                    output = ""
            raise USBIPDError(
                "usbipd native auto-attach exited immediately:\n\n"
                + (output.strip() or f"exit code {process.returncode}")
            )

        _USBIPD_AUTO_ATTACH[value] = process
        _USBIPD_AUTO_ATTACH_HARDWARE_IDS[value] = current_hardware_id
        thread = threading.Thread(
            target=_auto_attach_log_reader,
            args=(value, process, log),
            name=f"USBIPDAutoAttach-{value}",
            daemon=True,
        )
        _USBIPD_AUTO_ATTACH_THREADS[value] = thread
        thread.start()
        return process



def attach_to_wsl(
    busid: str,
    distro: str,
    log: LogFn,
    wsl: Path | None = None,
    stop_keepalive_on_error: bool = True,
) -> None:
    value = validate_busid(busid)
    distro_name = validate_distro(distro)

    with _USBIPD_OPERATION_LOCK:
        usbipd = find_usbipd()
        wsl_executable = ensure_wsl_running(distro_name, log, wsl)

        device = _device_for_busid(value)
        if device is None:
            if stop_keepalive_on_error:
                stop_wsl_keepalive(distro_name, log, wsl_executable)
            raise USBIPDError(
                f"USB bus ID {value} is no longer present. The phone may be "
                "changing USB modes."
            )

        if device.is_attached:
            return

        if not device.is_shared:
            log(
                f"{device.mode_name} device {value} is not shared yet; "
                "requesting a one-time USBIPD bind."
            )
            bind_elevated(value, log)

        command = [str(usbipd), "attach", "--wsl", "--busid", value]
        log(
            f"Attaching {device.mode_name} device {value} to running "
            f"WSL distro '{distro_name}'..."
        )

        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            check=False,
        )
        output = (completed.stdout or "").strip()

        if completed.returncode != 0 and is_no_running_wsl_error(output):
            log(
                "WSL stopped during USB attachment. Restarting it and "
                "retrying once..."
            )
            ensure_wsl_running(distro_name, log, wsl_executable)
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                check=False,
            )
            output = (completed.stdout or "").strip()

        if (
            completed.returncode != 0
            and "already attached" not in output.casefold()
        ):
            if stop_keepalive_on_error:
                stop_wsl_keepalive(distro_name, log, wsl_executable)
            raise USBIPDError(
                f"Could not attach {device.mode_name} device {value} to "
                f"WSL distro '{distro_name}':\n\n"
                f"{output or '(no output)'}"
            )

        if output:
            log(output)
        log(f"{device.mode_name} device {value} is attached to WSL.")


def detach_from_wsl(
    busid: str,
    log: LogFn,
    distro: str = "",
    wsl: Path | None = None,
    stop_keepalive: bool = True,
) -> None:
    value = validate_busid(busid)
    stop_usbipd_auto_attach(value, log)
    with _USBIPD_OPERATION_LOCK:
        executable = find_usbipd()
        log(f"Detaching USB device {value} from WSL so Windows can use it...")
        completed = subprocess.run(
            [str(executable), "detach", "--busid", value],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            check=False,
        )
        output = (completed.stdout or "").strip()
        if completed.returncode != 0:
            harmless = (
                "not attached",
                "not found",
                "no device",
            )
            if not any(token in output.casefold() for token in harmless):
                raise USBIPDError(
                    f"Could not detach USB device {value}:\n\n"
                    f"{output or '(no output)'}"
                )
        if output:
            log(output)

        if stop_keepalive and distro.strip():
            stop_wsl_keepalive(distro, log, wsl)



def select_apple_candidate(
    devices: list[USBIPDDevice],
    preferred_busid: str = "",
    current_busid: str = "",
) -> tuple[USBIPDDevice | None, str]:
    """
    Follow one iPhone across normal/recovery/DFU enumeration changes.

    A newly appeared non-attached Apple device is preferred over the stale
    attached entry from the previous mode. When multiple unrelated Apple
    devices are present, the watcher refuses to guess unless the selected or
    current BUSID identifies one.
    """
    apple = [device for device in devices if device.is_apple]
    if not apple:
        return None, "no Apple USB device detected"

    pending = [device for device in apple if not device.is_attached]

    for wanted, label in (
        (current_busid.strip(), "current BUSID"),
        (preferred_busid.strip(), "selected BUSID"),
    ):
        if wanted:
            exact_pending = [
                device for device in pending if device.busid == wanted
            ]
            if len(exact_pending) == 1:
                return exact_pending[0], label

    if len(pending) == 1:
        return pending[0], "only pending Apple device"

    if len(pending) > 1:
        return None, "multiple pending Apple devices detected"

    for wanted, label in (
        (current_busid.strip(), "current BUSID"),
        (preferred_busid.strip(), "selected BUSID"),
    ):
        if wanted:
            exact = [device for device in apple if device.busid == wanted]
            if len(exact) == 1:
                return exact[0], label

    if len(apple) == 1:
        return apple[0], "only Apple device"

    attached = [device for device in apple if device.is_attached]
    if len(attached) == 1:
        return attached[0], "only attached Apple device"

    return None, "multiple Apple devices detected"


class AppleUSBWatcher:
    def __init__(
        self,
        distro: str,
        preferred_busid: str,
        log: LogFn,
        wsl: Path | None = None,
        poll_interval: float = 0.40,
        device_supplier: Callable[[], list[USBIPDDevice]] = list_devices,
        attach_function: Callable[..., None] = attach_to_wsl,
        ensure_wsl_function: Callable[..., Path] = ensure_wsl_running,
    ):
        self.distro = validate_distro(distro)
        self.preferred_busid = preferred_busid.strip()
        self.current_busid = self.preferred_busid
        self.log = log
        self.wsl = wsl
        self.poll_interval = max(0.10, float(poll_interval))
        self.device_supplier = device_supplier
        self.attach_function = attach_function
        self.ensure_wsl_function = ensure_wsl_function

        self._stop_event = threading.Event()
        self._attached_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_snapshot: tuple[tuple[str, str, str, str], ...] = ()
        self._last_selection_reason = ""
        self._retry_after: dict[tuple[str, str], float] = {}
        self._last_error: dict[tuple[str, str], str] = {}
        self._native_auto_attach_busid = ""
        self._native_auto_attach_hardware_id = ""
        self._native_auto_attach_supported: bool | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self.ensure_wsl_function(self.distro, self.log, self.wsl)
        self._stop_event.clear()
        self._attached_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="AppleUSBWatcher",
            daemon=True,
        )
        self._thread.start()
        self.log(
            "Apple USB watcher started. It will follow normal, recovery, "
            "DFU, and pwnDFU mode changes automatically."
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        if self._native_auto_attach_busid:
            stop_usbipd_auto_attach(
                self._native_auto_attach_busid,
                self.log,
            )
            self._native_auto_attach_busid = ""
            self._native_auto_attach_hardware_id = ""
        self.log("Apple USB watcher stopped.")

    def wait_until_attached(self, timeout: float) -> bool:
        return self._attached_event.wait(timeout=max(0.0, timeout))

    def _snapshot(
        self,
        devices: list[USBIPDDevice],
    ) -> tuple[tuple[str, str, str, str], ...]:
        return tuple(
            sorted(
                (
                    device.busid,
                    device.hardware_id,
                    device.mode_name,
                    device.state,
                )
                for device in devices
                if device.is_apple
            )
        )

    def _log_snapshot_change(
        self,
        devices: list[USBIPDDevice],
        reason: str,
    ) -> None:
        snapshot = self._snapshot(devices)
        if snapshot == self._last_snapshot and reason == self._last_selection_reason:
            return

        self._last_snapshot = snapshot
        self._last_selection_reason = reason

        if not snapshot:
            self.log(
                "USB watcher: iPhone temporarily disconnected or changing modes..."
            )
            return

        description = "; ".join(
            f"{mode} {busid} {hardware_id or '(no VID:PID)'} [{state}]"
            for busid, hardware_id, mode, state in snapshot
        )
        self.log(f"USB watcher detected: {description}")

        if "multiple" in reason:
            self.log(
                "USB watcher paused because more than one Apple device matches. "
                "Leave only the target iPhone connected or use the selected BUSID."
            )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                devices = self.device_supplier()
            except Exception as exc:
                message = str(exc)
                if self._last_error.get(("", "")) != message:
                    self.log(f"USB watcher scan warning: {message}")
                    self._last_error[("", "")] = message
                self._stop_event.wait(self.poll_interval)
                continue

            candidate, reason = select_apple_candidate(
                devices,
                self.preferred_busid,
                self.current_busid,
            )
            self._log_snapshot_change(devices, reason)

            if candidate is None:
                self._attached_event.clear()
                self._stop_event.wait(self.poll_interval)
                continue

            if candidate.is_attached:
                self.current_busid = candidate.busid
                self._attached_event.set()
                self._stop_event.wait(self.poll_interval)
                continue

            self._attached_event.clear()
            key = (candidate.busid, candidate.hardware_id)
            now = time.monotonic()
            if now < self._retry_after.get(key, 0.0):
                self._stop_event.wait(self.poll_interval)
                continue

            try:
                if self._native_auto_attach_supported is not False:
                    candidate_hardware_id = candidate.hardware_id.casefold()
                    identity_changed = (
                        self._native_auto_attach_busid
                        and (
                            self._native_auto_attach_busid != candidate.busid
                            or self._native_auto_attach_hardware_id
                            != candidate_hardware_id
                        )
                    )
                    sharing_lost = (
                        self._native_auto_attach_busid == candidate.busid
                        and not candidate.is_shared
                    )

                    if identity_changed or sharing_lost:
                        old_identity = (
                            f"{self._native_auto_attach_busid} "
                            f"{self._native_auto_attach_hardware_id or 'unknown'}"
                        )
                        new_identity = (
                            f"{candidate.busid} "
                            f"{candidate_hardware_id or 'unknown'}"
                        )
                        self.log(
                            "USB watcher detected a new Apple USB "
                            f"enumeration ({old_identity} -> {new_identity}). "
                            "Stopping the old supervisor so the new mode can "
                            "be shared and attached."
                        )
                        stop_usbipd_auto_attach(
                            self._native_auto_attach_busid,
                            self.log,
                        )
                        self._native_auto_attach_busid = ""
                        self._native_auto_attach_hardware_id = ""

                    was_supervising = bool(
                        self._native_auto_attach_busid
                        and self._native_auto_attach_hardware_id
                        == candidate_hardware_id
                    )
                    start_usbipd_auto_attach(
                        candidate.busid,
                        self.distro,
                        self.log,
                        self.wsl,
                    )
                    self._native_auto_attach_supported = True
                    self._native_auto_attach_busid = candidate.busid
                    self._native_auto_attach_hardware_id = (
                        candidate_hardware_id
                    )
                    if not was_supervising:
                        self.log(
                            "usbipd native auto-attach is now supervising "
                            f"{candidate.mode_name} "
                            f"{candidate.hardware_id or 'unknown VID:PID'} "
                            f"at BUSID {candidate.busid}. Waiting for the "
                            "device to become genuinely Attached."
                        )
                else:
                    self.attach_function(
                        candidate.busid,
                        self.distro,
                        self.log,
                        self.wsl,
                        stop_keepalive_on_error=False,
                    )
            except Exception as exc:
                message = str(exc)
                if (
                    self._native_auto_attach_supported is not False
                    and (
                        "--auto-attach" in message
                        or "does not support" in message
                        or "unrecognized" in message.casefold()
                    )
                ):
                    self._native_auto_attach_supported = False
                    self.log(
                        "usbipd native auto-attach is unavailable; "
                        "falling back to the Python reattach loop."
                    )
                    self._retry_after[key] = now
                    self._stop_event.wait(0.05)
                    continue

                if self._last_error.get(key) != message:
                    self.log(
                        f"USB watcher could not attach {candidate.mode_name} "
                        f"at {candidate.busid}: {message}"
                    )
                    self._last_error[key] = message
                self._retry_after[key] = now + 1.5
            else:
                old_busid = self.current_busid
                self.current_busid = candidate.busid
                self._last_error.pop(key, None)
                self._retry_after[key] = now + 0.75

                if old_busid and old_busid != candidate.busid:
                    self.log(
                        f"USB watcher followed the iPhone from BUSID "
                        f"{old_busid} to {candidate.busid}."
                    )

                if self._native_auto_attach_supported:
                    self._attached_event.clear()
                else:
                    self._attached_event.set()
                    self.log(
                        f"USB watcher attached {candidate.mode_name} "
                        f"({candidate.hardware_id or 'unknown VID:PID'}) "
                        f"at BUSID {candidate.busid}."
                    )

            self._stop_event.wait(self.poll_interval)



def detach_attached_apple_devices(
    log: LogFn,
    preferred_busid: str = "",
    distro: str = "",
    wsl: Path | None = None,
    stop_keepalive: bool = True,
) -> None:
    devices = list_devices()
    targets: list[USBIPDDevice] = []
    if preferred_busid:
        targets.extend(
            device for device in devices
            if device.busid == preferred_busid and device.is_attached
        )
    targets.extend(
        device for device in devices
        if device.is_apple and device.is_attached and device not in targets
    )
    for device in targets:
        try:
            detach_from_wsl(
                device.busid,
                log,
                distro=distro,
                wsl=wsl,
                stop_keepalive=False,
            )
        except USBIPDError as exc:
            log(f"USB detach warning: {exc}")

    if stop_keepalive and distro.strip():
        stop_wsl_keepalive(distro, log, wsl)


def wait_for_windows_recovery(
    preferred_busid: str,
    log: LogFn,
    poll_interval: float = 0.15,
    device_supplier: Callable[[], list[USBIPDDevice]] = list_devices,
) -> USBIPDDevice:
    """Wait until Windows exposes the target Apple device in Recovery mode."""
    attempt = 0
    last_snapshot = ""
    preferred = preferred_busid.strip()

    while True:
        attempt += 1
        devices = device_supplier()
        recovery = [
            device for device in devices
            if device.is_apple and device.mode_name == "Recovery"
        ]

        exact = [device for device in recovery if device.busid == preferred]
        if len(exact) == 1:
            return exact[0]
        if len(recovery) == 1:
            return recovery[0]
        if len(recovery) > 1:
            raise USBIPDError(
                "More than one Apple Recovery device appeared. "
                "Disconnect the unrelated Apple device and retry."
            )

        snapshot = "; ".join(
            f"{device.mode_name} {device.busid} "
            f"{device.hardware_id or '(no VID:PID)'} [{device.state}]"
            for device in devices
            if device.is_apple
        ) or "no Apple USB device visible to Windows"

        if snapshot != last_snapshot or attempt == 1 or attempt % 40 == 0:
            log(
                "Host handoff is waiting for Windows Recovery mode: "
                + snapshot
            )
            last_snapshot = snapshot

        time.sleep(max(0.05, float(poll_interval)))


def wsl_lsusb(wsl: Path, distro: str) -> tuple[bool | None, str]:
    completed = subprocess.run(
        [str(wsl), "-d", distro, "--", "bash", "-lc", "command -v lsusb >/dev/null && lsusb"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        check=False,
    )
    output = completed.stdout or ""
    if completed.returncode != 0:
        return None, output
    lower = output.casefold()
    return ("05ac:" in lower or "apple" in lower), output

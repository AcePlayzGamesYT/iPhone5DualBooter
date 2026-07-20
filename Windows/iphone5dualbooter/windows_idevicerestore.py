from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile


LogFn = Callable[[str], None]

DEVJAM_REPOSITORY_URL = "https://github.com/Devjam81/libimobile2019"
DEVJAM_COMMIT = "3ad2a118abdc51d529136fc1f7d524b32cdc428a"
DEVJAM_ARCHIVE_URL = (
    "https://github.com/Devjam81/libimobile2019/archive/"
    f"{DEVJAM_COMMIT}.zip"
)

REQUIRED_WINDOWS_FILES = (
    "idevicerestore.exe",
    "libimobiledevice.dll",
    "libirecovery.dll",
    "libplist.dll",
    "libusbmuxd.dll",
    "libzip.dll",
    "zlib1.dll",
)


class WindowsIDeviceRestoreError(RuntimeError):
    pass


def _safe_extract(
    archive: zipfile.ZipFile,
    destination: Path,
) -> None:
    destination = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if target != destination and destination not in target.parents:
            raise WindowsIDeviceRestoreError(
                "Unsafe path found in the Windows idevicerestore ZIP."
            )
    archive.extractall(destination)


def validate_windows_idevicerestore_suite(directory: Path) -> Path:
    root = Path(directory).resolve()
    if not root.is_dir():
        raise WindowsIDeviceRestoreError(
            f"Windows idevicerestore folder does not exist: {root}"
        )

    names = {
        path.name.casefold(): path
        for path in root.iterdir()
        if path.is_file()
    }
    missing = [
        name
        for name in REQUIRED_WINDOWS_FILES
        if name.casefold() not in names
    ]
    if missing:
        raise WindowsIDeviceRestoreError(
            "The selected Windows idevicerestore folder is missing: "
            + ", ".join(missing)
        )

    executable = names["idevicerestore.exe"]
    try:
        header = executable.read_bytes()[:2]
    except OSError as exc:
        raise WindowsIDeviceRestoreError(
            f"Could not read {executable}: {exc}"
        ) from exc

    if header != b"MZ":
        raise WindowsIDeviceRestoreError(
            f"{executable} is not a Windows PE executable."
        )
    return root


def find_windows_idevicerestore_suite(
    directory: Path,
) -> Path | None:
    directory = Path(directory)
    candidates = [directory]
    candidates.extend(
        path.parent
        for path in directory.rglob("idevicerestore.exe")
        if path.is_file()
    )

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            return validate_windows_idevicerestore_suite(resolved)
        except WindowsIDeviceRestoreError:
            continue
    return None



def diagnose_windows_idevicerestore_tree(directory: Path) -> str:
    root = Path(directory).resolve()
    executable_paths = sorted(
        path
        for path in root.rglob("idevicerestore.exe")
        if path.is_file()
    )

    if not executable_paths:
        visible_files = sorted(
            path.name
            for path in root.rglob("*")
            if path.is_file()
        )
        preview = ", ".join(visible_files[:20]) or "(no files)"
        return (
            "No idevicerestore.exe was found in the downloaded archive. "
            f"First extracted files: {preview}"
        )

    errors: list[str] = []
    for executable in executable_paths:
        candidate = executable.parent
        try:
            validate_windows_idevicerestore_suite(candidate)
            return f"Complete suite found at {candidate}"
        except WindowsIDeviceRestoreError as exc:
            errors.append(f"{candidate}: {exc}")

    return " | ".join(errors)


def _download_archive(url: str, timeout: int = 240) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "iPhone5DualBooter/0.4.41",
            "Accept": "application/zip, application/octet-stream",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(128 * 1024 * 1024 + 1)

    if len(data) > 128 * 1024 * 1024:
        raise WindowsIDeviceRestoreError(
            "The Windows idevicerestore download exceeded 128 MiB."
        )
    return data


def ensure_windows_idevicerestore_suite(
    app_root: Path,
    configured_directory: Path | None,
    auto_download: bool,
    log: LogFn,
) -> Path:
    if configured_directory is not None:
        root = validate_windows_idevicerestore_suite(
            configured_directory
        )
        log(f"Using user-selected Windows idevicerestore suite: {root}")
        return root

    tools_root = Path(app_root) / "tools" / "windows-idevicerestore"
    obsolete_target = tools_root / "Devjam81-libimobile2019-e789c25"
    if obsolete_target.is_dir():
        log(
            "Removing the obsolete v0.4.40 Windows suite cache. That "
            "commit predates zlib1.dll and can never pass validation."
        )
        shutil.rmtree(obsolete_target, ignore_errors=True)

    target = tools_root / f"Devjam81-libimobile2019-{DEVJAM_COMMIT[:7]}"

    if target.is_dir():
        try:
            root = validate_windows_idevicerestore_suite(target)
            log(f"Using cached Windows idevicerestore suite: {root}")
            return root
        except WindowsIDeviceRestoreError as exc:
            log(
                "Discarding an invalid cached Windows idevicerestore "
                f"suite: {exc}"
            )
            shutil.rmtree(target, ignore_errors=True)

    if not auto_download:
        raise WindowsIDeviceRestoreError(
            "Windows-host idevicerestore is enabled, but no valid "
            "suite folder was selected and automatic download is off."
        )

    tools_root.mkdir(parents=True, exist_ok=True)
    log(
        "Downloading the pinned Devjam81/libimobile2019 Windows "
        f"suite at commit {DEVJAM_COMMIT[:7]}..."
    )

    try:
        data = _download_archive(DEVJAM_ARCHIVE_URL)
    except Exception as exc:
        raise WindowsIDeviceRestoreError(
            "Could not download the Windows idevicerestore suite: "
            f"{exc}"
        ) from exc

    with tempfile.TemporaryDirectory(
        prefix="iphone5dualbooter-windows-idr-",
        dir=str(tools_root),
    ) as temp_text:
        temp_root = Path(temp_text)
        archive_path = temp_root / "suite.zip"
        extract_root = temp_root / "extracted"
        archive_path.write_bytes(data)
        extract_root.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                _safe_extract(archive, extract_root)
        except zipfile.BadZipFile as exc:
            raise WindowsIDeviceRestoreError(
                "The Windows idevicerestore download was not a valid ZIP."
            ) from exc

        source = find_windows_idevicerestore_suite(extract_root)
        if source is None:
            details = diagnose_windows_idevicerestore_tree(extract_root)
            raise WindowsIDeviceRestoreError(
                "The downloaded repository did not contain a complete "
                "Windows idevicerestore suite. Details: "
                + details
            )

        staging = tools_root / f".{target.name}.staging"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)

        for file in source.iterdir():
            if file.is_file():
                shutil.copy2(file, staging / file.name)

        (staging / "IPHONE5DUALBOOTER_SOURCE.txt").write_text(
            "Third-party Windows binary suite\n"
            f"Repository: {DEVJAM_REPOSITORY_URL}\n"
            f"Pinned commit: {DEVJAM_COMMIT}\n"
            "Downloaded by iPhone5DualBooter; not redistributed "
            "inside the application ZIP.\n",
            encoding="utf-8",
        )

        validate_windows_idevicerestore_suite(staging)
        shutil.rmtree(target, ignore_errors=True)
        staging.replace(target)

    root = validate_windows_idevicerestore_suite(target)
    log(
        "Windows idevicerestore suite downloaded and validated at: "
        f"{root}"
    )
    return root

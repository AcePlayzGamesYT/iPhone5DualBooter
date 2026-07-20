from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from .models import WorkflowSettings


LogFn = Callable[[str], None]
LegacyPromptFn = Callable[[int, int], str]
LEGACY_RELEASE_API = "https://api.github.com/repos/LukeZGD/Legacy-iOS-Kit/releases/latest"
LEGACY_CODE_ZIP = "https://github.com/LukeZGD/Legacy-iOS-Kit/archive/refs/heads/main.zip"


class LegacyMacOSError(RuntimeError):
    pass


def _request_bytes(url: str, timeout: int = 180) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "iPhone5DualBooter-macOS/1.0.0",
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
            raise LegacyMacOSError("Unsafe path found in the Legacy iOS Kit ZIP.")
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
        raise LegacyMacOSError("The latest Legacy iOS Kit release has no ZIP asset.")
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
        raise LegacyMacOSError(
            f"restore.sh and resources were not found in {explicit_directory}."
        )
    tools = app_root / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    existing = find_legacy_root(tools)
    if existing:
        log(f"Using Legacy iOS Kit: {existing}")
        return existing
    if not auto_download:
        raise LegacyMacOSError(
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
        raise LegacyMacOSError(f"Could not download Legacy iOS Kit: {exc}") from exc
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp:
        temp.write(data)
        temp_path = Path(temp.name)
    try:
        with zipfile.ZipFile(temp_path, "r") as archive:
            _safe_extract(archive, download_root)
    except zipfile.BadZipFile as exc:
        raise LegacyMacOSError("The Legacy iOS Kit download was not a valid ZIP.") from exc
    finally:
        temp_path.unlink(missing_ok=True)
    root = find_legacy_root(download_root)
    if not root:
        raise LegacyMacOSError("The downloaded Legacy iOS Kit ZIP did not contain restore.sh.")
    log(f"Legacy iOS Kit is ready: {root}")
    return root


def run_legacy_native_restore(
    settings: WorkflowSettings,
    app_root: Path,
    log: LogFn,
    legacy_prompt: LegacyPromptFn | None = None,
) -> None:
    if sys.platform != "darwin":
        raise LegacyMacOSError("This application requires macOS.")
    if not settings.stock_host_ipsw or not settings.stock_host_ipsw.is_file():
        raise LegacyMacOSError("Select the stock iOS 8.4.1 Restore IPSW.")
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
    attempt = 0
    while True:
        attempt += 1
        log(f"Launching Legacy iOS Kit natively, attempt {attempt}.")
        completed = subprocess.run(command, cwd=str(legacy_root), check=False)
        log(f"Legacy iOS Kit exited with code {completed.returncode}.")
        if legacy_prompt is None:
            if completed.returncode != 0:
                raise LegacyMacOSError(
                    f"Legacy iOS Kit exited with code {completed.returncode}."
                )
            return
        action = legacy_prompt(completed.returncode, attempt)
        if action == "continue":
            return
        if action == "rerun":
            log("Relaunching Legacy iOS Kit.")
            continue
        raise LegacyMacOSError("Legacy iOS Kit restore was cancelled.")

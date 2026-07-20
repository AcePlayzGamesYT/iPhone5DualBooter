from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class WorkflowSettings:
    secondary_ipsw: Path
    stock_host_ipsw: Optional[Path]
    secondary_version: str
    datasize_gb: Optional[int]

    skip_restore: bool
    restore_mode: str

    legacy_kit_dir: Optional[Path]
    auto_download_legacy_kit: bool
    legacy_wsl_distro: str

    usbipd_busid: str
    auto_attach_usb_to_wsl: bool
    auto_detach_usb_after_restore: bool
    require_external_pwndfu: bool
    patch_idevicerestore: bool
    ibec_retry_seconds: int
    full_rebuild_fallback: bool

    phone_wifi_ip: str
    ssh_port: int
    root_password: str
    install_untether: bool = True

    use_windows_idevicerestore: bool = False
    windows_idevicerestore_dir: Optional[Path] = None
    auto_download_windows_idevicerestore: bool = True

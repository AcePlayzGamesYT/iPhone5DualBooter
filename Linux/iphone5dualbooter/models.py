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
    legacy_kit_dir: Optional[Path]
    auto_download_legacy_kit: bool
    phone_wifi_ip: str
    ssh_port: int
    root_password: str
    install_untether: bool = True

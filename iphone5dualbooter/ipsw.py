from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import plistlib
import re
import zipfile


IPHONE_5_TYPES = {"iPhone5,1", "iPhone5,2"}
VERSION_PATTERN = re.compile(r"^\d+\.\d+(?:\.\d+)?(?:b[1-9]\d*)?$", re.IGNORECASE)


class IPSWError(ValueError):
    pass


@dataclass(frozen=True)
class IPSWInfo:
    path: Path
    product_version: str
    build_version: str
    product_types: frozenset[str]

    @property
    def is_iphone5(self) -> bool:
        return bool(self.product_types & IPHONE_5_TYPES)


def validate_secondary_version(value: str) -> str:
    value = value.strip()
    if not VERSION_PATTERN.fullmatch(value):
        raise ValueError(
            "Use a version like 8.0, 7.0.6, or 7.0b1. "
            "Beta numbers must use b followed by an integer."
        )
    return value


def _read_plist(zf: zipfile.ZipFile, name: str) -> dict:
    try:
        with zf.open(name) as fp:
            return plistlib.load(fp)
    except KeyError as exc:
        raise IPSWError(f"{name} is missing from the IPSW.") from exc
    except Exception as exc:
        raise IPSWError(f"Could not parse {name}: {exc}") from exc


def inspect_ipsw(path: Path) -> IPSWInfo:
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != ".ipsw":
        raise IPSWError("Select a real .ipsw file.")

    try:
        with zipfile.ZipFile(path, "r") as zf:
            restore = _read_plist(zf, "Restore.plist")
            try:
                manifest = _read_plist(zf, "BuildManifest.plist")
            except IPSWError:
                manifest = {}
    except zipfile.BadZipFile as exc:
        raise IPSWError("The selected file is not a valid IPSW/ZIP archive.") from exc

    product_version = str(
        restore.get("ProductVersion")
        or manifest.get("ProductVersion")
        or ""
    )
    build_version = str(
        restore.get("ProductBuildVersion")
        or manifest.get("ProductBuildVersion")
        or ""
    )

    product_types: set[str] = set()
    for source in (restore, manifest):
        values = source.get("SupportedProductTypes") or []
        if isinstance(values, (list, tuple)):
            product_types.update(str(v) for v in values)

    # Older manifests can omit SupportedProductTypes.
    if not product_types:
        for identity in manifest.get("BuildIdentities", []) or []:
            info = identity.get("Info", {}) if isinstance(identity, dict) else {}
            product_type = info.get("ProductType")
            if product_type:
                product_types.add(str(product_type))

    if not product_version:
        raise IPSWError("Could not determine the iOS version from the IPSW.")
    if not product_types:
        raise IPSWError("Could not determine supported device models from the IPSW.")

    return IPSWInfo(
        path=path,
        product_version=product_version,
        build_version=build_version,
        product_types=frozenset(product_types),
    )


def base_version(version: str) -> str:
    return re.sub(r"b\d+$", "", version, flags=re.IGNORECASE)



def validate_secondary_info(secondary: IPSWInfo, requested_version: str) -> None:
    if not secondary.is_iphone5:
        raise IPSWError("The secondary IPSW does not support iPhone 5.")
    entered = base_version(requested_version)
    manifest_version = base_version(secondary.product_version)
    if entered != manifest_version:
        raise IPSWError(
            f"You entered {requested_version}, but the IPSW reports "
            f"iOS {secondary.product_version}. For betas, enter the beta suffix "
            "manually, such as 7.0b1."
        )



def validate_host_841_info(host: IPSWInfo, device_model: str | None = None) -> None:
    if host.product_version != "8.4.1":
        raise IPSWError(
            f"The stock restore IPSW must be iOS 8.4.1, but it reports {host.product_version}."
        )
    if not host.is_iphone5:
        raise IPSWError("The stock iOS 8.4.1 IPSW does not support iPhone 5.")
    if device_model and device_model not in host.product_types:
        raise IPSWError(
            f"The selected stock IPSW does not support {device_model}."
        )


def validate_host_secondary_models(host: IPSWInfo, secondary: IPSWInfo) -> None:
    overlap = (host.product_types & secondary.product_types) & IPHONE_5_TYPES
    if not overlap:
        raise IPSWError(
            "The stock 8.4.1 IPSW and secondary IPSW target different iPhone 5 models "
            "(iPhone5,1 GSM versus iPhone5,2 Global)."
        )

def validate_pair(host: IPSWInfo, secondary: IPSWInfo, requested_version: str) -> None:
    if host.product_version != "8.4.1":
        raise IPSWError(
            f"The host IPSW must be iOS 8.4.1, but this one is {host.product_version}."
        )
    if not host.is_iphone5:
        raise IPSWError("The iOS 8.4.1 IPSW does not support iPhone 5.")

    if not secondary.is_iphone5:
        raise IPSWError("The secondary IPSW does not support iPhone 5.")

    overlap = (host.product_types & secondary.product_types) & IPHONE_5_TYPES
    if not overlap:
        raise IPSWError(
            "The two IPSWs target different iPhone 5 models "
            "(iPhone5,1 GSM vs iPhone5,2 Global)."
        )

    entered = base_version(requested_version)
    manifest_version = base_version(secondary.product_version)
    if entered != manifest_version:
        raise IPSWError(
            f"You entered {requested_version}, but the IPSW reports "
            f"iOS {secondary.product_version}. For betas, enter the beta suffix "
            "manually, such as 7.0b1."
        )

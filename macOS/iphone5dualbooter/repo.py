from __future__ import annotations

import bz2
from dataclasses import dataclass
import gzip
from html import unescape
from pathlib import Path
import re
import tempfile
import urllib.parse
import urllib.request


class RepoError(RuntimeError):
    pass


COOLBOOTER_BASE_URL = "https://coolbooter.com/"
COOLBOOTER_INDEX_URLS = (
    urllib.parse.urljoin(COOLBOOTER_BASE_URL, "Packages.bz2"),
    urllib.parse.urljoin(COOLBOOTER_BASE_URL, "Packages.gz"),
    urllib.parse.urljoin(COOLBOOTER_BASE_URL, "Packages"),
)

                          
LEGACY_ARCHIVE_BASE_URL = "https://apt.saurik.com/debs/"
KNOWN_DIRECT_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "com.coolbooter.coolbootercli": (
        "png",
        "xpwn",
        "zip",
        "unzip",
        "diskdev-cmds",
        "gettext",
        "wget",
    ),
    "com.coolbooter.cbuntether": (
        "com.saurik.substrate.safemode",
        "mobilesubstrate",
    ),
}


@dataclass(frozen=True)
class RepositoryPackage:
    package_id: str
    version: str
    filename: str
    depends: str = ""
    pre_depends: str = ""
    base_url: str = COOLBOOTER_BASE_URL


def _download(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "iPhone5DualBooter/0.1"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()


def _read_coolbooter_index() -> str:
    errors: list[str] = []
    for url in COOLBOOTER_INDEX_URLS:
        try:
            payload = _download(url)
            if url.endswith(".bz2"):
                payload = bz2.decompress(payload)
            elif url.endswith(".gz"):
                payload = gzip.decompress(payload)
            return payload.decode("utf-8", errors="replace")
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise RepoError("Could not download the CoolBooter package index.\n" + "\n".join(errors))


def _parse_deb822(text: str) -> list[dict[str, str]]:
    packages: list[dict[str, str]] = []
    current: dict[str, str] = {}
    last_key: str | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip():
            if current:
                packages.append(current)
                current = {}
                last_key = None
            continue
        if raw_line[:1].isspace() and last_key:
            current[last_key] += "\n" + raw_line.strip()
            continue
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        last_key = key.strip()
        current[last_key] = value.strip()
    if current:
        packages.append(current)
    return packages


def _coolbooter_packages() -> dict[str, RepositoryPackage]:
    result: dict[str, RepositoryPackage] = {}
    for raw in _parse_deb822(_read_coolbooter_index()):
        package_id = raw.get("Package", "").strip()
        filename = raw.get("Filename", "").strip()
        if not package_id or not filename:
            continue
        result[package_id] = RepositoryPackage(
            package_id=package_id,
            version=raw.get("Version", "unknown").strip(),
            filename=filename,
            depends=raw.get("Depends", "").strip(),
            pre_depends=raw.get("Pre-Depends", "").strip(),
            base_url=COOLBOOTER_BASE_URL,
        )
    return result


def _dependency_groups(value: str) -> list[tuple[str, ...]]:
    groups: list[tuple[str, ...]] = []
    for raw_group in value.replace("\n", " ").split(","):
        alternatives: list[str] = []
        for raw_alternative in raw_group.split("|"):
            name = re.split(r"\s|\(", raw_alternative.strip(), maxsplit=1)[0]
            name = name.split(":", 1)[0].strip()
            if name:
                alternatives.append(name)
        if alternatives:
            groups.append(tuple(alternatives))
    return groups


def _natural_key(value: str) -> tuple[object, ...]:
    return tuple(int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value))


def _legacy_archive_package(package_id: str) -> RepositoryPackage:
    """Find the newest old iphoneos-arm build in Saurik's public archive."""
    html = _download(LEGACY_ARCHIVE_BASE_URL).decode("utf-8", errors="replace")
    hrefs = [unescape(item) for item in re.findall(r'href=["\']([^"\']+)["\']', html, re.I)]
    prefix = package_id + "_"
    candidates = [
        urllib.parse.unquote(Path(urllib.parse.urlparse(href).path).name)
        for href in hrefs
        if Path(urllib.parse.urlparse(href).path).name.startswith(prefix)
        and Path(urllib.parse.urlparse(href).path).name.endswith("_iphoneos-arm.deb")
    ]
    if not candidates:
        raise RepoError(
            f"Could not find {package_id} in the legacy jailbreak package archive."
        )
    filename = sorted(set(candidates), key=_natural_key)[-1]
    version = filename[len(prefix): -len("_iphoneos-arm.deb")]
    return RepositoryPackage(
        package_id=package_id,
        version=version,
        filename=filename,
        base_url=LEGACY_ARCHIVE_BASE_URL,
    )


def resolve_package_plan(package_id: str) -> list[RepositoryPackage]:
    """Return dependencies first, then the requested CoolBooter package."""
    index = _coolbooter_packages()
    target = index.get(package_id)
    if target is None:
        raise RepoError(f"{package_id} was not found in the CoolBooter repository.")

    ordered: list[RepositoryPackage] = []
    added: set[str] = set()

    dependency_names: list[str] = list(KNOWN_DIRECT_DEPENDENCIES.get(package_id, ()))
    dependency_text = ", ".join(part for part in (target.pre_depends, target.depends) if part)
    for alternatives in _dependency_groups(dependency_text):
        selected = next((name for name in alternatives if name in index), None)
        if selected and selected not in dependency_names:
            dependency_names.append(selected)

    for dependency_id in dependency_names:
        if dependency_id in added:
            continue
        package = index.get(dependency_id)
        if package is None:
            package = _legacy_archive_package(dependency_id)
        ordered.append(package)
        added.add(dependency_id)

    ordered.append(target)
    return ordered


def download_repository_package(package: RepositoryPackage) -> Path:
    url = urllib.parse.urljoin(package.base_url, package.filename.lstrip("/"))
    data = _download(url)
    temp_dir = Path(tempfile.mkdtemp(prefix="iphone5dualbooter-"))
    destination = temp_dir / Path(package.filename).name
    destination.write_bytes(data)
    return destination


def download_package(package_id: str) -> Path:
    plan = resolve_package_plan(package_id)
    target = next((package for package in plan if package.package_id == package_id), None)
    if target is None:
        raise RepoError(f"{package_id} was not found in the CoolBooter repository.")
    return download_repository_package(target)

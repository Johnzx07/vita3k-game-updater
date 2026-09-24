from __future__ import annotations

import hashlib
import hmac
import os
import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests


TITLE_ID = re.compile(r"^[A-Z]{4}[0-9]{5}$")
SHA1 = re.compile(r"^[0-9a-fA-F]{40}$")
VERSION = re.compile(r"^[0-9]{1,2}(?:\.[0-9]{1,3}){1,2}$")
SONY_HOST = "gs2-sec.ww.prod.dl.playstation.net"
HMAC_KEY = bytes.fromhex(
    "e5e278aa1ee34082a088279c83f9bbc806821c52f2ab5d2b4abd995450355114"
)


@dataclass(frozen=True)
class Game:
    title_id: str
    title: str
    installed_version: str
    icon_path: Path | None


@dataclass(frozen=True)
class Update:
    version: str
    size: int
    sha1: str
    url: str
    kind: str
    content_id: str

    @property
    def filename(self) -> str:
        parsed = urlparse(self.url)
        return Path(parsed.path).name


def version_key(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        return (0,)


def read_sfo(path: Path) -> dict[str, str]:
    """Read the small subset of PARAM.SFO needed by the library view."""
    raw = path.read_bytes()
    if len(raw) < 20 or raw[:4] != b"\x00PSF":
        raise ValueError("Invalid PARAM.SFO header")
    _, key_start, data_start, count = struct.unpack_from("<4I", raw, 4)
    if count > 1024 or 20 + count * 16 > len(raw):
        raise ValueError("Invalid PARAM.SFO index")
    result: dict[str, str] = {}
    for index in range(count):
        key_offset, _format, length, _maximum, data_offset = struct.unpack_from(
            "<HHIII", raw, 20 + index * 16
        )
        k0 = key_start + key_offset
        d0 = data_start + data_offset
        if k0 >= len(raw) or d0 + length > len(raw):
            continue
        k1 = raw.find(b"\x00", k0, min(len(raw), k0 + 128))
        if k1 < 0:
            continue
        key = raw[k0:k1].decode("utf-8", errors="replace")
        value = raw[d0:d0 + length].split(b"\x00", 1)[0].decode(
            "utf-8", errors="replace"
        )
        result[key] = value.strip()
    return result


def find_library_root(chosen: Path | None = None) -> Path | None:
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Vita3K" / "Vita3K"
    candidates = [chosen] if chosen else [base, base / "fs"]
    for candidate in candidates:
        if candidate is None:
            continue
        for root in (candidate, candidate / "fs"):
            if (root / "ux0" / "app").is_dir():
                return root
    return None


def scan_library(root: Path) -> list[Game]:
    app_dir = root / "ux0" / "app"
    patch_dir = root / "ux0" / "patch"
    if not app_dir.is_dir():
        raise ValueError("Choose the Vita3K content folder containing ux0/app")
    games: list[Game] = []
    for folder in sorted(app_dir.iterdir()):
        if not folder.is_dir() or not TITLE_ID.fullmatch(folder.name.upper()):
            continue
        base_sfo = folder / "sce_sys" / "param.sfo"
        if not base_sfo.is_file():
            continue
        try:
            metadata = read_sfo(base_sfo)
        except (OSError, ValueError):
            continue
        title_id = folder.name.upper()
        title = metadata.get("TITLE") or metadata.get("STITLE") or title_id
        title = " ".join(title.split())
        installed = metadata.get("APP_VER") or "1.00"
        patch_sfo = patch_dir / title_id / "sce_sys" / "param.sfo"
        if patch_sfo.is_file():
            try:
                patch_version = read_sfo(patch_sfo).get("APP_VER")
                if patch_version and version_key(patch_version) > version_key(installed):
                    installed = patch_version
            except (OSError, ValueError):
                pass
        icon = folder / "sce_sys" / "icon0.png"
        games.append(Game(title_id, title, installed, icon if icon.is_file() else None))
    return games


def update_url(title_id: str) -> str:
    title_id = title_id.upper().strip()
    if not TITLE_ID.fullmatch(title_id):
        raise ValueError("Enter a nine-character Vita title ID, for example PCSA00007")
    digest = hmac.new(HMAC_KEY, ("np_" + title_id).encode("ascii"), hashlib.sha256).hexdigest()
    return f"https://{SONY_HOST}/pl/np/{title_id}/{digest}/{title_id}-ver.xml"


def _safe_package_url(value: str, title_id: str) -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if host not in {
        "gs.ww.np.dl.playstation.net",
        "gs-sec.ww.np.dl.playstation.net",
        "gs2-sec.ww.prod.dl.playstation.net",
        "nsx.np.dl.playstation.net",
    } or not parsed.path.startswith(f"/ppkg/np/{title_id}/"):
        raise ValueError("Update metadata contains an unexpected package URL")
    if not parsed.path.lower().endswith(".pkg") or parsed.query or parsed.fragment:
        raise ValueError("Update metadata contains an invalid package path")
    return f"https://{SONY_HOST}{parsed.path}"


def parse_updates(payload: bytes, title_id: str) -> tuple[str, list[Update]]:
    if len(payload) > 2_000_000 or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise ValueError("Unexpected update metadata")
    root = ET.fromstring(payload)
    if root.tag != "titlepatch" or root.attrib.get("titleid", "").upper() != title_id:
        raise ValueError("Update metadata does not match the requested title")
    title = root.findtext("./tag/package/paramsfo/title") or title_id
    updates: list[Update] = []
    for package in root.findall("./tag/package"):
        for item in [package, *package.findall("hybrid_package")]:
            version = item.attrib.get("version") or package.attrib.get("version", "")
            digest = item.attrib.get("sha1sum", "")
            size_text = item.attrib.get("size", "")
            if not VERSION.fullmatch(version) or not SHA1.fullmatch(digest):
                continue
            try:
                size = int(size_text)
            except ValueError:
                continue
            if not 0 < size <= 20_000_000_000:
                continue
            url = _safe_package_url(item.attrib.get("url", ""), title_id)
            updates.append(Update(
                version=version,
                size=size,
                sha1=digest.lower(),
                url=url,
                kind=item.attrib.get("type", package.attrib.get("type", "cumulative")),
                content_id=item.attrib.get("content_id", ""),
            ))
    return " ".join(title.split()), sorted(updates, key=lambda u: version_key(u.version))


def fetch_updates(title_id: str, session: requests.Session | None = None) -> tuple[str, list[Update]]:
    session = session or requests.Session()
    response = session.get(update_url(title_id), timeout=(10, 30), allow_redirects=False)
    if response.status_code in (403, 404):
        return title_id, []
    response.raise_for_status()
    if len(response.content) > 2_000_000:
        raise ValueError("Update metadata is too large")
    return parse_updates(response.content, title_id)


def pending_updates(installed_version: str, updates: list[Update]) -> list[Update]:
    """Keep all newer packages; incremental chains may require earlier releases."""
    return [u for u in updates if version_key(u.version) > version_key(installed_version)]


def verify_package(path: Path, update: Update) -> bool:
    if not path.is_file() or path.stat().st_size != update.size:
        return False
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return hmac.compare_digest(digest.hexdigest(), update.sha1)


def download_update(
    update: Update,
    title_id: str,
    destination: Path,
    progress,
    session: requests.Session | None = None,
) -> Path:
    if not TITLE_ID.fullmatch(title_id):
        raise ValueError("Invalid title ID")
    destination = destination / title_id
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / update.filename
    if path.exists() and verify_package(path, update):
        progress(update.size, update.size)
        return path
    partial = path.with_suffix(path.suffix + ".part")
    session = session or requests.Session()
    digest = hashlib.sha1()
    received = 0
    try:
        with session.get(update.url, stream=True, timeout=(10, 60), allow_redirects=False) as response:
            response.raise_for_status()
            with partial.open("wb") as stream:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        stream.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        if received > update.size:
                            raise ValueError("Package exceeded the size in Sony metadata")
                        progress(received, update.size)
        if received != update.size or not hmac.compare_digest(digest.hexdigest(), update.sha1):
            raise ValueError("Package size or SHA-1 did not match Sony metadata")
        partial.replace(path)
        return path
    except Exception:
        partial.unlink(missing_ok=True)
        raise

from __future__ import annotations

import hashlib
import hmac
import io
import os
import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests


TITLE_ID = re.compile(r"^[A-Z]{4}[0-9]{5}$")
SHA1 = re.compile(r"^[0-9a-fA-F]{40}$")
VERSION = re.compile(r"^[0-9]{1,2}(?:\.[0-9]{1,3}){1,2}$")
SONY_HOST = "gs2-sec.ww.prod.dl.playstation.net"
HMAC_KEY = bytes.fromhex(
    "e5e278aa1ee34082a088279c83f9bbc806821c52f2ab5d2b4abd995450355114"
)
# Sony's public TMDB key (same one the PS3/RPCS3 updater uses). It serves PS3
# titles; Vita IDs return 404/403 there, so remote art is an enhancement that
# degrades to nothing instead of breaking the core lookup.
TMDB_URL = "https://tmdb.np.dl.playstation.net/tmdb/{pid}_{hash}/{pid}.xml"
TMDB_HMAC_KEY = bytes.fromhex(
    "F5DE66D2680E255B2DF79E74F890EBF349262F618BCAE2A9ACCDEE5156CE8DF2"
    "CDF2D48C71173CDC2594465B87405D197CF1AED3B7E9671EEB56CA6753C2E6B0"
)
MAX_ICON_BYTES = 8 * 1024 * 1024


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


SFO_FORMATS = {2, 4, 6, 516}
SFO_STRING_FORMAT = 516


def _sfo_entry_fields(raw: bytes, offset: int, layout: str) -> tuple[int, int, int, int]:
    """Return (nameoff, format, size, dataoff) for one 16-byte index entry.

    Field order differs in real files: the canonical order is
    (nameoff, format, dataoff, size) as uint32s; the Vita/SFOEditor variant
    packs nameoff/format as two uint16s followed by size, maximum, dataoff.
    """
    a, b, c, d = struct.unpack_from("<4I", raw, offset)
    if layout == "canonical":
        return a, b, d, c
    return a & 0xFFFF, a >> 16, b, d


def read_sfo(path: Path) -> dict[str, str]:
    """Read the small subset of PARAM.SFO needed by the library view."""
    raw = path.read_bytes()
    if len(raw) < 20 or raw[:4] != b"\x00PSF":
        raise ValueError("Invalid PARAM.SFO header")
    _, key_start, data_start, count = struct.unpack_from("<4I", raw, 4)
    if count == 0 or count > 1024:
        raise ValueError("Invalid PARAM.SFO index")
    entry_size = key_start - 20
    if entry_size != 16 * count or 20 + entry_size > len(raw):
        raise ValueError("Invalid PARAM.SFO index")
    key_size = data_start - key_start
    data_size = len(raw) - data_start

    def valid(fields: tuple[int, int, int, int]) -> bool:
        nameoff, fmt, size, dataoff = fields
        return nameoff < key_size and fmt in SFO_FORMATS and dataoff + size <= data_size

    first = _sfo_entry_fields(raw, 20, "canonical")
    layout = "canonical" if valid(first) else "vita"
    if not valid(_sfo_entry_fields(raw, 20, layout)):
        raise ValueError("Invalid PARAM.SFO index")

    result: dict[str, str] = {}
    for index in range(count):
        nameoff, fmt, size, dataoff = _sfo_entry_fields(raw, 20 + index * 16, layout)
        if not valid((nameoff, fmt, size, dataoff)):
            continue
        k0 = key_start + nameoff
        k1 = raw.find(b"\x00", k0, min(len(raw), k0 + 128))
        if k1 < 0:
            continue
        key = raw[k0:k1].decode("utf-8", errors="replace")
        d0 = data_start + dataoff
        if fmt != SFO_STRING_FORMAT:
            continue
        value = raw[d0:d0 + size].split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        result[key] = value.strip()
    return result


def valid_icon_bytes(data: bytes | None) -> bool:
    """True when the bytes decode as a real image within the sanity cap."""
    if not data or len(data) > MAX_ICON_BYTES:
        return False
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(data))
        image.load()
        return True
    except Exception:
        return False


def art_cache_dir() -> Path:
    return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Vita Pulse" / "art_cache"


def _tmdb_hash(pid: str) -> str:
    return hmac.new(TMDB_HMAC_KEY, pid.encode(), hashlib.sha1).hexdigest().upper()


def fetch_meta(title_id: str, session: requests.Session | None = None) -> tuple[str, str]:
    """Display name + box-art URL from Sony's TMDB; never raises.

    TMDB serves PS3 product ids with this key. Vita ids usually 404 there, so
    metadata is an enhancement that degrades to empty values instead of
    breaking the update lookup.
    """
    title_id = title_id.upper().strip()
    if not TITLE_ID.fullmatch(title_id):
        return "", ""
    pid = f"{title_id}_00"
    url = TMDB_URL.format(pid=pid, hash=_tmdb_hash(pid))
    try:
        response = (session or requests.Session()).get(url, timeout=(10, 20))
        if response.status_code != 200 or not response.content:
            return "", ""
        root = ET.fromstring(response.content)
    except Exception:
        return "", ""
    name = (root.findtext("name") or "").strip()
    icon_url = (root.findtext("icon") or "").strip()
    return " ".join(name.split()), icon_url


ICON_HOSTS = {"tmdb.np.dl.playstation.net", "store.playstation.com", "store.np.playstation.net"}


def _safe_icon_url(value: str) -> str:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in ICON_HOSTS or not host.endswith("playstation.net"):
        return ""
    return urlunparse(parsed)


def download_icon(url: str, session: requests.Session | None = None) -> bytes | None:
    """Fetch box-art bytes for a Sony icon URL; never raises."""
    url = _safe_icon_url(url)
    if not url:
        return None
    try:
        response = (session or requests.Session()).get(url, timeout=(10, 20))
        if response.status_code != 200:
            return None
    except Exception:
        return None
    return response.content if valid_icon_bytes(response.content) else None


def find_game_art(
    root: Path,
    title_id: str,
    session: requests.Session | None = None,
    cache_dir: Path | None = None,
    allow_remote: bool = True,
) -> Path | None:
    """Path to a title's art, in priority order (same design as the RPCS3 updater).

    1. locally installed art (ux0/app/<id>/sce_sys/icon0.png, patch copy)
    2. already-cached art in the Vita Pulse cache folder
    3. Sony TMDB box art (network) — only when nothing local or cached exists,
       then stored in the cache for the next run.

    Never raises: art is an enhancement, not a requirement for the update check.
    """
    title_id = title_id.upper().strip()
    if not TITLE_ID.fullmatch(title_id):
        return None
    if root is not None:
        for folder in (root / "ux0" / "app" / title_id / "sce_sys", root / "ux0" / "patch" / title_id / "sce_sys"):
            icon = folder / "icon0.png"
            if icon.is_file() and valid_icon_bytes(icon.read_bytes()):
                return icon
    cache = cache_dir or art_cache_dir()
    cached = cache / f"{title_id}.png"
    if cached.is_file() and valid_icon_bytes(cached.read_bytes()):
        return cached
    if not allow_remote:
        return None
    _, icon_url = fetch_meta(title_id, session)
    data = download_icon(icon_url, session)
    if not data:
        return None
    try:
        cache.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    except OSError:
        return None
    return cached


def find_library_root(chosen: Path | None = None) -> Path | None:
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Vita3K" / "Vita3K"
    candidates = [chosen] if chosen else [base, base / "fs"]
    for candidate in candidates:
        if candidate is None:
            continue
        seen = set()
        # Walk the chosen folder and one level up so users can pick either the
        # content folder (Vita3K/Vita3K), its fs subfolder, or the parent Vita3K.
        for root in (candidate, candidate / "fs", candidate / "Vita3K", candidate.parent):
            if root in seen:
                continue
            seen.add(root)
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

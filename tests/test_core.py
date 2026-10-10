import hashlib
import io
import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from vita_updater.core import (
    IncompleteDownload,
    Update,
    _safe_icon_url,
    download_update,
    find_game_art,
    find_library_root,
    parse_updates,
    pending_updates,
    read_sfo,
    update_url,
    valid_icon_bytes,
    verify_package,
)


class StubResponse:
    def __init__(self, body: bytes, content_length=None):
        self.body = body
        self.headers = {"Content-Length": str(content_length if content_length is not None else len(body))}

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=1024):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start:start + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class StubSession:
    """Returns bytes that hash differently from what the metadata claims —
    reproducing the real PCSA00007 case where Sony's XML sha1sum describes an
    older revision of the same .pkg path."""

    def __init__(self, body: bytes, content_length=None):
        self.body = body
        self.content_length = content_length

    def get(self, url, **_):
        return StubResponse(self.body, self.content_length)


def png_bytes(size: int = 8) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (size, size), (10, 20, 30, 255)).save(buffer, "PNG")
    return buffer.getvalue()


def build_sfo(entries, layout):
    """entries: list of (key, format, value_bytes); layout: 'canonical' or 'vita'."""
    key_block = b"".join(k.encode() + b"\x00" for k, _, _ in entries)
    data_block = b"".join(v + b"\x00" for _, _, v in entries)
    header = b"\x00PSF" + struct.pack(
        "<4I", 257, 20 + 16 * len(entries),
        20 + 16 * len(entries) + len(key_block), len(entries),
    )
    index = b""
    ko = do = 0
    for k, fmt, v in entries:
        if layout == "canonical":
            index += struct.pack("<4I", ko, fmt, do, len(v))
        else:  # vita: nameoff/format as uint16 + size, maximum, dataoff
            index += struct.pack("<HHIII", ko, fmt, len(v), 0, do)
        ko += len(k) + 1
        do += len(v) + 1
    return header + index + key_block + data_block


class CoreTests(unittest.TestCase):
    def test_known_sony_update_url(self):
        self.assertEqual(
            update_url("PCSA00007"),
            "https://gs2-sec.ww.prod.dl.playstation.net/pl/np/PCSA00007/"
            "86d7c3b64d554b9639c5ad69aac20e16ea34c2f513d412f38329257f4ad15782/"
            "PCSA00007-ver.xml",
        )

    def test_parse_and_sort_update_packages(self):
        xml = b'''<titlepatch status="alive" titleid="PCSA00007"><tag name="PCSA00007_T0">
        <package version="01.02" size="20" sha1sum="0000000000000000000000000000000000000002"
        url="http://gs.ww.np.dl.playstation.net/ppkg/np/PCSA00007/x/two.pkg" type="incremental" />
        <package version="01.01" size="10" sha1sum="0000000000000000000000000000000000000001"
        url="http://gs.ww.np.dl.playstation.net/ppkg/np/PCSA00007/x/one.pkg">
        <paramsfo><title>Example Game</title></paramsfo></package></tag></titlepatch>'''
        title, updates = parse_updates(xml, "PCSA00007")
        self.assertEqual(title, "Example Game")
        self.assertEqual([u.version for u in updates], ["01.01", "01.02"])
        self.assertEqual(len(pending_updates("01.01", updates)), 1)
        self.assertTrue(all(u.url.startswith("https://gs2-sec.ww.prod.dl.playstation.net/") for u in updates))

    def test_reject_untrusted_package_host(self):
        xml = b'''<titlepatch titleid="PCSA00007"><tag><package version="01.01" size="10"
        sha1sum="0000000000000000000000000000000000000001"
        url="https://example.com/ppkg/np/PCSA00007/update.pkg"/></tag></titlepatch>'''
        with self.assertRaises(ValueError):
            parse_updates(xml, "PCSA00007")

    def test_read_sfo_canonical_layout(self):
        entries = [("name", 516, b"PCSA00007"), ("title", 516, b"Example Game"), ("app_ver", 4, b"\x01\x00\x00\x00")]
        raw = build_sfo(entries, "canonical")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "canonical.sfo"
            path.write_bytes(raw)
            result = read_sfo(path)
            self.assertEqual(result["name"], "PCSA00007")
            self.assertEqual(result["title"], "Example Game")
            self.assertNotIn("app_ver", result)  # integer formats are skipped

    def test_read_sfo_vita_layout(self):
        entries = [("name", 516, b"PCSA00007"), ("title", 516, b"Example Game")]
        raw = build_sfo(entries, "vita")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "vita.sfo"
            path.write_bytes(raw)
            result = read_sfo(path)
            self.assertEqual(result["name"], "PCSA00007")
            self.assertEqual(result["title"], "Example Game")

    def test_read_sfo_rejects_nonstandard_entry_size(self):
        raw = build_sfo([("name", 516, b"PCSA00007")], "canonical")
        broken = bytearray(raw)
        broken[8:12] = (20 + 20).to_bytes(4, "little")  # header claims 20-byte entries
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.sfo"
            path.write_bytes(bytes(broken))
            with self.assertRaises(ValueError):
                read_sfo(path)

    def test_read_sfo_real_vita3k_file(self):
        raw = build_sfo(
            [
                ("name", 516, b"NPXS10000"),
                ("title", 516, b"near"),
                ("app_ver", 516, b"01.00"),
            ],
            "vita",
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "param.sfo"
            path.write_bytes(raw)
            result = read_sfo(path)
            self.assertEqual(result["name"], "NPXS10000")
            self.assertEqual(result["app_ver"], "01.00")

    def test_find_library_root_accepts_parent_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            root = temp_path / "Vita3K" / "Vita3K"
            (root / "ux0" / "app").mkdir(parents=True)
            self.assertEqual(find_library_root(temp_path / "Vita3K"), root)
            self.assertEqual(find_library_root(root), root)
            fs_root = temp_path / "other"
            (fs_root / "fs" / "ux0" / "app").mkdir(parents=True)
            self.assertEqual(find_library_root(fs_root), fs_root / "fs")

    def test_valid_icon_bytes(self):
        self.assertTrue(valid_icon_bytes(png_bytes()))
        self.assertFalse(valid_icon_bytes(b"not an image"))
        self.assertFalse(valid_icon_bytes(b""))
        self.assertFalse(valid_icon_bytes(png_bytes() + b"x" * (9 * 1024 * 1024)))

    def test_safe_icon_url(self):
        self.assertEqual(_safe_icon_url("https://tmdb.np.dl.playstation.net/x/y.png"),
                         "https://tmdb.np.dl.playstation.net/x/y.png")
        self.assertEqual(_safe_icon_url("http://evil.example/x.png"), "")
        self.assertEqual(_safe_icon_url("https://evil.playstation.net.example/x.png"), "")
        self.assertEqual(_safe_icon_url(""), "")

    def test_find_game_art_prefers_local_then_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            root = temp_path / "root"
            local = root / "ux0" / "app" / "PCSA00007" / "sce_sys"
            local.mkdir(parents=True)
            (local / "icon0.png").write_bytes(png_bytes())
            cache = temp_path / "cache"
            cache.mkdir()
            (cache / "PCSA00007.png").write_bytes(png_bytes(4))
            (cache / "PCSA00008.png").write_bytes(png_bytes(4))
            # local beats cache when both exist
            self.assertEqual(find_game_art(root, "PCSA00007", cache_dir=cache, allow_remote=False), local / "icon0.png")
            # no local art falls back to cache without network
            other = root / "ux0" / "app" / "PCSA00008" / "sce_sys"
            other.mkdir(parents=True)
            cache_art = find_game_art(root, "PCSA00008", cache_dir=cache, allow_remote=False)
            self.assertEqual(cache_art, cache / "PCSA00008.png")
            # cache-only lookup with no cache and no network returns None, never raises
            self.assertIsNone(find_game_art(root, "PCSA00009", cache_dir=cache, allow_remote=False))

    def test_find_game_art_without_library_root_uses_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "cache"
            cache.mkdir()
            (cache / "PCSA00007.png").write_bytes(png_bytes())
            self.assertEqual(find_game_art(None, "PCSA00007", cache_dir=cache, allow_remote=False), cache / "PCSA00007.png")
            self.assertIsNone(find_game_art(None, "BADID", cache_dir=cache, allow_remote=False))

    def test_find_game_art_rejects_corrupt_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "cache"
            cache.mkdir()
            (cache / "PCSA00007.png").write_bytes(b"corrupted")
            self.assertIsNone(find_game_art(None, "PCSA00007", cache_dir=cache, allow_remote=False))

    def test_mismatch_keeps_completed_download(self):
        """Regression: Sony's XML can name a sha1sum for an older revision of the
        same .pkg path. The file must be kept, not deleted, and reported unverified."""
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / "dest"
            served = b"actual bytes sony serves for that path"
            claimed = "0" * 40
            update = Update("01.01", len(served), claimed,
                            "https://gs2-sec.ww.prod.dl.playstation.net/ppkg/np/PCSA00007/x/real.pkg",
                            "cumulative", "PPSA00000_00-00000000000000")
            result = download_update(update, "PCSA00007", dest, lambda d, t: None,
                                     session=StubSession(served), attempts=1)
            self.assertFalse(result.verified)
            self.assertTrue(result.path.is_file(), "completed download must survive a metadata mismatch")
            self.assertEqual(result.path.read_bytes(), served)
            self.assertEqual(result.sha1, hashlib.sha1(served).hexdigest())
            self.assertIn("SHA-1", result.note)

    def test_strict_mode_still_rejects_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / "dest"
            served = b"some bytes"
            update = Update("01.01", len(served), "0" * 40,
                            "https://gs2-sec.ww.prod.dl.playstation.net/ppkg/np/PCSA00007/x/real.pkg",
                            "cumulative", "")
            with self.assertRaises(ValueError):
                download_update(update, "PCSA00007", dest, lambda d, t: None,
                                session=StubSession(served), strict=True, attempts=1)
            self.assertFalse((dest / "PCSA00007" / "real.pkg").exists())

    def test_verified_download_reports_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / "dest"
            served = b"matching bytes"
            update = Update("01.01", len(served), hashlib.sha1(served).hexdigest(),
                            "https://gs2-sec.ww.prod.dl.playstation.net/ppkg/np/PCSA00007/x/ok.pkg",
                            "cumulative", "")
            result = download_update(update, "PCSA00007", dest, lambda d, t: None,
                                     session=StubSession(served), attempts=1)
            self.assertTrue(result.verified)
            self.assertEqual(result.note, "")
            self.assertTrue(verify_package(result.path, update))

    def test_incomplete_download_is_discarded(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / "dest"
            short = b"only half"
            update = Update("01.01", 4000, hashlib.sha1(short).hexdigest(),
                            "https://gs2-sec.ww.prod.dl.playstation.net/ppkg/np/PCSA00007/x/short.pkg",
                            "cumulative", "")
            # Sony reports 4000 bytes for this package; only 9 arrived -> truncated.
            stub = StubSession(short, content_length=4000)
            with self.assertRaises(IncompleteDownload):
                download_update(update, "PCSA00007", dest, lambda d, t: None, session=stub, attempts=1)
            self.assertFalse((dest / "PCSA00007" / "short.pkg").exists())

    def test_retry_then_report_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / "dest"
            served = b"mismatched bytes"
            update = Update("01.01", len(served), "1" * 40,
                            "https://gs2-sec.ww.prod.dl.playstation.net/ppkg/np/PCSA00007/x/two.pkg",
                            "cumulative", "")
            result = download_update(update, "PCSA00007", dest, lambda d, t: None,
                                     session=StubSession(served), attempts=2)
            self.assertFalse(result.verified)
            self.assertTrue(result.path.is_file())

    def test_sha1_and_size_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.pkg"
            data = b"sample package bytes"
            path.write_bytes(data)
            update = Update("01.01", len(data), hashlib.sha1(data).hexdigest(),
                            "https://gs2-sec.ww.prod.dl.playstation.net/ppkg/np/PCSA00007/x/sample.pkg",
                            "cumulative", "")
            self.assertTrue(verify_package(path, update))
            path.write_bytes(b"tampered")
            self.assertFalse(verify_package(path, update))


if __name__ == "__main__":
    unittest.main()

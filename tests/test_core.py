import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from vita_updater.core import (
    Update,
    find_library_root,
    parse_updates,
    pending_updates,
    read_sfo,
    update_url,
    verify_package,
)


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

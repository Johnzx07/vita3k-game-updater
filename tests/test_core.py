import hashlib
import tempfile
import unittest
from pathlib import Path

from vita_updater.core import Update, parse_updates, pending_updates, update_url, verify_package


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

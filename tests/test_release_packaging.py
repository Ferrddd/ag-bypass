import os
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.build_release import (
    COMMON_CORE_FILES,
    LINUX_MACOS_FILES,
    WINDOWS_FILES,
    compute_sha256,
    package_linux_macos_tar,
    package_windows_zip,
)


class TestReleasePackaging(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_package_windows_zip(self):
        out_zip = self.temp_path / "ag-bypass-windows.zip"
        package_windows_zip(out_zip)
        self.assertTrue(out_zip.is_file())
        self.assertGreater(out_zip.stat().st_size, 1000)

        with zipfile.ZipFile(out_zip, "r") as zf:
            names = set(zf.namelist())
            for expected in WINDOWS_FILES:
                self.assertIn(expected, names)
            # Ensure no systemd or bash scripts leaked into windows archive
            self.assertNotIn("install.sh", names)
            self.assertNotIn("systemd/ag-check.service", names)

    def test_package_linux_macos_tar(self):
        out_tar = self.temp_path / "ag-bypass-linux-macos.tar.gz"
        package_linux_macos_tar(out_tar)
        self.assertTrue(out_tar.is_file())
        self.assertGreater(out_tar.stat().st_size, 1000)

        with tarfile.open(out_tar, "r:gz") as tf:
            members = {m.name: m for m in tf.getmembers()}
            for expected in LINUX_MACOS_FILES:
                self.assertIn(expected, members)
                if expected in ("ag", "install.sh", "uninstall.sh"):
                    self.assertEqual(members[expected].mode & 0o777, 0o755)

            # Ensure no Windows scripts leaked into linux archive
            self.assertNotIn("ag.bat", members)
            self.assertNotIn("install.ps1", members)

    def test_compute_sha256(self):
        dummy = self.temp_path / "test.txt"
        dummy.write_text("Antigravity Bypass", encoding="utf-8")
        h = compute_sha256(dummy)
        self.assertEqual(len(h), 64)


if __name__ == "__main__":
    unittest.main()

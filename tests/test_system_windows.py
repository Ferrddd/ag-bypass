import ctypes
import importlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

# Preload modules to avoid dynamic import issues when sys.platform is temporarily patched
import core.system_linux
import core.system_windows
from core import system, system_linux, system_windows

# Load CLI module safely
from importlib.machinery import SourceFileLoader
_ag_path = Path(__file__).resolve().parent.parent / "ag"
ag_cli = SourceFileLoader("ag", str(_ag_path)).load_module()


# =====================================================================
# FakeWinreg: In-Memory Registry Simulation
# =====================================================================

class FakeKey:
    """Mock context manager for Windows registry key handles."""
    def __init__(self, store: dict, key_path: tuple):
        self.store = store
        self.key_path = key_path

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class FakeWinreg:
    """In-memory replacement for Python's winreg module on non-Windows platforms."""
    HKEY_CURRENT_USER = 1
    KEY_SET_VALUE = 2
    KEY_QUERY_VALUE = 4
    REG_SZ = 1

    def __init__(self):
        # (root, subkey) -> { value_name: (value_data, type) }
        self.data: dict[tuple, dict[str, tuple]] = {}

    def OpenKey(self, root, subkey, reserved=0, access=0):
        key = (root, subkey)
        if key not in self.data:
            raise FileNotFoundError(f"[WinError 2] The system cannot find the file specified: {subkey}")
        return FakeKey(self.data[key], key)

    def CreateKey(self, root, subkey):
        key = (root, subkey)
        if key not in self.data:
            self.data[key] = {}
        return FakeKey(self.data[key], key)

    def SetValueEx(self, key_obj: FakeKey, value_name: str, reserved: int, type_: int, value: str):
        key_obj.store[value_name] = (value, type_)

    def QueryValueEx(self, key_obj: FakeKey, value_name: str) -> tuple:
        if value_name not in key_obj.store:
            raise FileNotFoundError(f"[WinError 2] The system cannot find the value specified: {value_name}")
        return key_obj.store[value_name]

    def DeleteValue(self, key_obj: FakeKey, value_name: str):
        if value_name not in key_obj.store:
            raise FileNotFoundError(f"[WinError 2] The system cannot find the value specified: {value_name}")
        del key_obj.store[value_name]


class TestFakeWinreg(unittest.TestCase):
    """Unit tests for the FakeWinreg simulator itself."""

    def setUp(self):
        self.reg = FakeWinreg()

    def test_create_set_and_query_value(self):
        key = self.reg.CreateKey(FakeWinreg.HKEY_CURRENT_USER, r"Environment")
        with key:
            self.reg.SetValueEx(key, "TEST_VAR", 0, FakeWinreg.REG_SZ, "test_val")

        opened = self.reg.OpenKey(FakeWinreg.HKEY_CURRENT_USER, r"Environment", 0, FakeWinreg.KEY_QUERY_VALUE)
        with opened:
            val, typ = self.reg.QueryValueEx(opened, "TEST_VAR")
            self.assertEqual(val, "test_val")
            self.assertEqual(typ, FakeWinreg.REG_SZ)

    def test_delete_value(self):
        key = self.reg.CreateKey(FakeWinreg.HKEY_CURRENT_USER, r"Environment")
        self.reg.SetValueEx(key, "DELETE_ME", 0, FakeWinreg.REG_SZ, "val")
        self.reg.DeleteValue(key, "DELETE_ME")

        with self.assertRaises(FileNotFoundError):
            self.reg.QueryValueEx(key, "DELETE_ME")

    def test_open_missing_key_raises(self):
        with self.assertRaises(FileNotFoundError):
            self.reg.OpenKey(FakeWinreg.HKEY_CURRENT_USER, r"NonExistentKey")

    def test_delete_missing_value_raises(self):
        key = self.reg.CreateKey(FakeWinreg.HKEY_CURRENT_USER, r"Environment")
        with self.assertRaises(FileNotFoundError):
            self.reg.DeleteValue(key, "NonExistentVal")


# =====================================================================
# Windows Admin Check Tests
# =====================================================================

class TestWindowsAdminCheck(unittest.TestCase):
    """Tests for system_windows.is_admin()."""

    def test_is_admin_elevated(self):
        mock_windll = MagicMock()
        mock_windll.shell32.IsUserAnAdmin.return_value = 1
        with patch.object(ctypes, "windll", mock_windll, create=True):
            self.assertTrue(system_windows.is_admin())

    def test_is_admin_not_elevated(self):
        mock_windll = MagicMock()
        mock_windll.shell32.IsUserAnAdmin.return_value = 0
        with patch.object(ctypes, "windll", mock_windll, create=True):
            self.assertFalse(system_windows.is_admin())

    def test_is_admin_exception(self):
        mock_windll = MagicMock()
        mock_windll.shell32.IsUserAnAdmin.side_effect = OSError("Access denied")
        with patch.object(ctypes, "windll", mock_windll, create=True):
            self.assertFalse(system_windows.is_admin())

    def test_is_admin_no_windll(self):
        # On Linux ctypes has no windll attribute
        if hasattr(ctypes, "windll"):
            delattr(ctypes, "windll")
        self.assertFalse(system_windows.is_admin())


# =====================================================================
# Windows DNS Flush Tests
# =====================================================================

class TestWindowsDNSFlush(unittest.TestCase):
    """Tests for system_windows.flush_dns_cache()."""

    @patch("subprocess.run")
    def test_flush_dns_cache_success(self, mock_run):
        mock_run.return_value.returncode = 0
        self.assertTrue(system_windows.flush_dns_cache())
        mock_run.assert_called_once_with(["ipconfig", "/flushdns"], capture_output=True, timeout=5)

    @patch("subprocess.run")
    def test_flush_dns_cache_failure(self, mock_run):
        mock_run.return_value.returncode = 1
        self.assertFalse(system_windows.flush_dns_cache())

    @patch("subprocess.run")
    def test_flush_dns_cache_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ipconfig", timeout=5)
        self.assertFalse(system_windows.flush_dns_cache())

    @patch("subprocess.run")
    def test_flush_dns_cache_oserror(self, mock_run):
        mock_run.side_effect = FileNotFoundError("ipconfig not found")
        self.assertFalse(system_windows.flush_dns_cache())


# =====================================================================
# Windows Hosts File Manager Tests
# =====================================================================

class TestWindowsHostsManager(unittest.TestCase):
    """Tests for system_windows hosts file reading, writing, clearing, and backup."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.hosts_path = Path(self.temp_dir.name).resolve() / "hosts"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_active_entries_missing_file(self):
        self.assertEqual(system_windows.get_active_entries(self.hosts_path), [])

    def test_get_active_entries_empty_file(self):
        self.hosts_path.write_text("", encoding="utf-8")
        self.assertEqual(system_windows.get_active_entries(self.hosts_path), [])

    @patch("core.system_windows.flush_dns_cache")
    def test_set_and_get_active_entries(self, mock_flush):
        self.hosts_path.write_text("127.0.0.1 localhost\n::1 localhost\n", encoding="utf-8")
        entries = [("1.2.3.4", "example.com"), ("1.2.3.5", "api.example.com")]
        ok, msg = system_windows.set_entries(entries, hosts_path=self.hosts_path)
        self.assertTrue(ok)
        self.assertIn("успешно", msg)
        mock_flush.assert_called_once()

        active = system_windows.get_active_entries(self.hosts_path)
        self.assertEqual(active, entries)

        content = self.hosts_path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("127.0.0.1 localhost\n::1 localhost\n"))
        self.assertIn(system_windows.TAG_BEGIN, content)
        self.assertIn("1.2.3.4 example.com", content)
        self.assertIn("1.2.3.5 api.example.com", content)
        self.assertIn(system_windows.TAG_END, content)

    @patch("core.system_windows.flush_dns_cache")
    def test_set_entries_creates_ag_bak(self, mock_flush):
        original_text = "127.0.0.1 localhost\n# Custom Windows hosts\n"
        self.hosts_path.write_text(original_text, encoding="utf-8")

        ok, msg = system_windows.set_entries([("10.0.0.1", "win.test")], hosts_path=self.hosts_path)
        self.assertTrue(ok)

        bak_path = self.hosts_path.with_name(self.hosts_path.name + ".ag_bak")
        self.assertTrue(bak_path.is_file(), "hosts.ag_bak must be created on set_entries")
        self.assertEqual(bak_path.read_text(encoding="utf-8"), original_text)

    @patch("core.system_windows.flush_dns_cache")
    def test_clear_entries(self, mock_flush):
        self.hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
        system_windows.set_entries([("1.2.3.4", "example.com")], hosts_path=self.hosts_path)
        mock_flush.reset_mock()

        ok, msg = system_windows.clear_entries(hosts_path=self.hosts_path)
        self.assertTrue(ok)
        self.assertIn("успешно", msg)
        mock_flush.assert_called_once()
        self.assertEqual(system_windows.get_active_entries(self.hosts_path), [])

        content = self.hosts_path.read_text(encoding="utf-8")
        self.assertNotIn(system_windows.TAG_BEGIN, content)
        self.assertIn("127.0.0.1 localhost", content)

    def test_clear_entries_when_already_clean(self):
        self.hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
        ok, msg = system_windows.clear_entries(hosts_path=self.hosts_path)
        self.assertTrue(ok)
        self.assertIn("отсутствует", msg)

    def test_clear_entries_missing_file(self):
        ok, msg = system_windows.clear_entries(hosts_path=self.hosts_path)
        self.assertFalse(ok)
        self.assertIn("Файл не найден", msg)

    def test_set_entries_missing_file(self):
        ok, msg = system_windows.set_entries([("1.1.1.1", "test.com")], hosts_path=self.hosts_path)
        self.assertFalse(ok)
        self.assertIn("Файл не найден", msg)

    def test_hosts_multi_domain_and_comments(self):
        content = (
            f"127.0.0.1 localhost\n"
            f"{system_windows.TAG_BEGIN}\n"
            f"# Leading comment\n"
            f"10.0.0.1 alpha.com beta.com # inline comment\n"
            f"  # Indented comment\n"
            f"10.0.0.2 gamma.com\n"
            f"{system_windows.TAG_END}\n"
        )
        self.hosts_path.write_text(content, encoding="utf-8")
        entries = system_windows.get_active_entries(self.hosts_path)
        self.assertEqual(entries, [
            ("10.0.0.1", "alpha.com"),
            ("10.0.0.1", "beta.com"),
            ("10.0.0.2", "gamma.com"),
        ])

    @patch("core.system_windows.flush_dns_cache")
    def test_hosts_unclosed_tag_cleanup(self, mock_flush):
        corrupt = f"127.0.0.1 localhost\n{system_windows.TAG_BEGIN}\n10.0.0.1 alpha.com\n"
        self.hosts_path.write_text(corrupt, encoding="utf-8")
        ok, msg = system_windows.set_entries([("10.0.0.2", "beta.com")], hosts_path=self.hosts_path)
        self.assertTrue(ok)

        text = self.hosts_path.read_text(encoding="utf-8")
        self.assertEqual(text.count(system_windows.TAG_BEGIN), 1)
        self.assertEqual(text.count(system_windows.TAG_END), 1)
        self.assertEqual(system_windows.get_active_entries(self.hosts_path), [("10.0.0.2", "beta.com")])

    @patch("core.system_windows.flush_dns_cache")
    def test_hosts_orphaned_end_tag_cleanup(self, mock_flush):
        corrupt = f"127.0.0.1 localhost\n10.0.0.1 alpha.com\n{system_windows.TAG_END}\n"
        self.hosts_path.write_text(corrupt, encoding="utf-8")
        ok, msg = system_windows.set_entries([("10.0.0.2", "beta.com")], hosts_path=self.hosts_path)
        self.assertTrue(ok)

        text = self.hosts_path.read_text(encoding="utf-8")
        self.assertEqual(text.count(system_windows.TAG_END), 1)

        # Also test clear_entries removes orphan end tag
        self.hosts_path.write_text(corrupt, encoding="utf-8")
        ok, msg = system_windows.clear_entries(hosts_path=self.hosts_path)
        self.assertTrue(ok)
        self.assertNotIn(system_windows.TAG_END, self.hosts_path.read_text(encoding="utf-8"))

    @patch("core.system_windows.flush_dns_cache")
    def test_hosts_set_entries_empty_clears(self, mock_flush):
        self.hosts_path.write_text(
            f"127.0.0.1 localhost\n{system_windows.TAG_BEGIN}\n1.1.1.1 test.com\n{system_windows.TAG_END}\n",
            encoding="utf-8"
        )
        ok, msg = system_windows.set_entries([], hosts_path=self.hosts_path)
        self.assertTrue(ok)
        self.assertEqual(system_windows.get_active_entries(self.hosts_path), [])
        text = self.hosts_path.read_text(encoding="utf-8")
        self.assertNotIn(system_windows.TAG_BEGIN, text)

    @patch("core.system_windows.flush_dns_cache")
    def test_hosts_injection_sanitization(self, mock_flush):
        self.hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
        entries = [("10.0.0.1\n127.0.0.1 evil.com", "good.com\nfake.com")]
        ok, msg = system_windows.set_entries(entries, hosts_path=self.hosts_path)
        self.assertTrue(ok)
        active = system_windows.get_active_entries(self.hosts_path)
        self.assertEqual(active, [("10.0.0.1", "good.com")])
        text = self.hosts_path.read_text(encoding="utf-8")
        self.assertNotIn("evil.com", text)
        self.assertNotIn("fake.com", text)

    @patch("core.system_windows.flush_dns_cache")
    def test_hosts_deduplicate_domains(self, mock_flush):
        self.hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
        entries = [("10.0.0.1", "dup.com"), ("10.0.0.2", "dup.com")]
        ok, msg = system_windows.set_entries(entries, hosts_path=self.hosts_path)
        self.assertTrue(ok)
        active = system_windows.get_active_entries(self.hosts_path)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0], ("10.0.0.1", "dup.com"))


# =====================================================================
# Windows Endpoint Manager Tests (Registry & Fallbacks)
# =====================================================================

class TestWindowsEndpointManager(unittest.TestCase):
    """Tests for system_windows CLOUD_CODE_URL management in HKCU\\Environment and os.environ."""

    def setUp(self):
        self.orig_env = os.environ.get("CLOUD_CODE_URL")
        os.environ.pop("CLOUD_CODE_URL", None)
        self.fake_winreg = FakeWinreg()

    def tearDown(self):
        if self.orig_env is not None:
            os.environ["CLOUD_CODE_URL"] = self.orig_env
        else:
            os.environ.pop("CLOUD_CODE_URL", None)

    def test_apply_and_remove_endpoint_with_winreg(self):
        with patch("core.system_windows.winreg", self.fake_winreg):
            # Apply
            applied = system_windows.apply_endpoint("https://custom.endpoint.com")
            self.assertEqual(os.environ.get("CLOUD_CODE_URL"), "https://custom.endpoint.com")
            self.assertIn(r"HKCU\Environment:CLOUD_CODE_URL", applied)

            # Check status
            has_ep, desc = system_windows.get_endpoint_status()
            self.assertTrue(has_ep)
            self.assertIn("custom.endpoint.com", desc)

            # Check registry directly
            key = self.fake_winreg.OpenKey(FakeWinreg.HKEY_CURRENT_USER, r"Environment")
            val, typ = self.fake_winreg.QueryValueEx(key, "CLOUD_CODE_URL")
            self.assertEqual(val, "https://custom.endpoint.com")

            # Remove
            removed = system_windows.remove_endpoint()
            self.assertNotIn("CLOUD_CODE_URL", os.environ)
            self.assertIn(r"HKCU\Environment:CLOUD_CODE_URL", removed)
            self.assertIn("os.environ:CLOUD_CODE_URL", removed)

            # Verify registry value is deleted
            with self.assertRaises(FileNotFoundError):
                self.fake_winreg.QueryValueEx(key, "CLOUD_CODE_URL")

            # Check status after removal
            has_ep, desc = system_windows.get_endpoint_status()
            self.assertFalse(has_ep)

    def test_apply_endpoint_creates_environment_key_if_missing(self):
        with patch("core.system_windows.winreg", self.fake_winreg):
            # Key does not exist initially in fake_winreg
            applied = system_windows.apply_endpoint("https://created.endpoint.com")
            self.assertIn(r"HKCU\Environment:CLOUD_CODE_URL", applied)
            key = self.fake_winreg.OpenKey(FakeWinreg.HKEY_CURRENT_USER, r"Environment")
            val, _ = self.fake_winreg.QueryValueEx(key, "CLOUD_CODE_URL")
            self.assertEqual(val, "https://created.endpoint.com")

    def test_apply_endpoint_url_sanitization(self):
        with patch("core.system_windows.winreg", self.fake_winreg):
            system_windows.apply_endpoint('https://evil.com"; `rm -rf`; $VAR; \\path\n')
            val = os.environ.get("CLOUD_CODE_URL")
            self.assertNotIn('"', val)
            self.assertNotIn('`', val)
            self.assertNotIn(';', val)
            self.assertNotIn('$', val)
            self.assertNotIn('\\', val)
            self.assertNotIn('\n', val)

    def test_remove_endpoint_handles_missing_key_or_val(self):
        with patch("core.system_windows.winreg", self.fake_winreg):
            # Nothing in environ, nothing in registry
            removed = system_windows.remove_endpoint()
            self.assertEqual(removed, [])

    def test_get_endpoint_status_session(self):
        os.environ["CLOUD_CODE_URL"] = "https://session.endpoint.com"
        with patch("core.system_windows.winreg", self.fake_winreg):
            has_ep, desc = system_windows.get_endpoint_status()
            self.assertTrue(has_ep)
            self.assertIn("В текущей сессии", desc)
            self.assertIn("https://session.endpoint.com", desc)

    def test_get_endpoint_status_winreg(self):
        # Unset in session, set in registry
        os.environ.pop("CLOUD_CODE_URL", None)
        key = self.fake_winreg.CreateKey(FakeWinreg.HKEY_CURRENT_USER, r"Environment")
        self.fake_winreg.SetValueEx(key, "CLOUD_CODE_URL", 0, FakeWinreg.REG_SZ, "https://reg.endpoint.com")

        with patch("core.system_windows.winreg", self.fake_winreg):
            has_ep, desc = system_windows.get_endpoint_status()
            self.assertTrue(has_ep)
            self.assertIn("В реестре (HKCU\\Environment)", desc)
            self.assertIn("https://reg.endpoint.com", desc)

    def test_get_endpoint_status_not_configured(self):
        os.environ.pop("CLOUD_CODE_URL", None)
        with patch("core.system_windows.winreg", self.fake_winreg), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            has_ep, desc = system_windows.get_endpoint_status()
            self.assertFalse(has_ep)
            self.assertIn("Не настроен", desc)

    @patch("subprocess.run")
    def test_apply_endpoint_fallback_setx(self, mock_run):
        mock_run.return_value.returncode = 0
        with patch("core.system_windows.winreg", None):
            applied = system_windows.apply_endpoint("https://setx.endpoint.com")
            self.assertEqual(os.environ.get("CLOUD_CODE_URL"), "https://setx.endpoint.com")
            mock_run.assert_called_once_with(["setx", "CLOUD_CODE_URL", "https://setx.endpoint.com"], capture_output=True, timeout=5)
            self.assertIn(r"HKCU\Environment:CLOUD_CODE_URL (setx)", applied)

    @patch("subprocess.run")
    def test_remove_endpoint_fallback_reg_delete(self, mock_run):
        os.environ["CLOUD_CODE_URL"] = "https://to-delete.com"
        mock_run.return_value.returncode = 0
        with patch("core.system_windows.winreg", None):
            removed = system_windows.remove_endpoint()
            self.assertNotIn("CLOUD_CODE_URL", os.environ)
            mock_run.assert_called_once_with(
                ["reg", "delete", r"HKCU\Environment", "/v", "CLOUD_CODE_URL", "/f"],
                capture_output=True,
                timeout=5,
            )
            self.assertIn(r"HKCU\Environment:CLOUD_CODE_URL (reg delete)", removed)

    @patch("subprocess.run")
    def test_get_endpoint_status_fallback_reg_query(self, mock_run):
        os.environ.pop("CLOUD_CODE_URL", None)
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "HKEY_CURRENT_USER\\Environment\n    CLOUD_CODE_URL    REG_SZ    https://query.endpoint.com\n"
        with patch("core.system_windows.winreg", None):
            has_ep, desc = system_windows.get_endpoint_status()
            self.assertTrue(has_ep)
            self.assertIn("В реестре (HKCU\\Environment)", desc)
            self.assertIn("https://query.endpoint.com", desc)

    @patch("subprocess.run")
    def test_fallback_handles_subprocess_failure(self, mock_run):
        # setx failure
        mock_run.return_value.returncode = 1
        with patch("core.system_windows.winreg", None):
            applied = system_windows.apply_endpoint("https://fail.com")
            self.assertEqual(applied, [])

        # reg delete exception when CLOUD_CODE_URL was set in os.environ by apply_endpoint above
        mock_run.side_effect = OSError("command failed")
        with patch("core.system_windows.winreg", None):
            removed = system_windows.remove_endpoint()
            # Successfully removed from os.environ, but reg delete failed
            self.assertEqual(removed, ["os.environ:CLOUD_CODE_URL"])
            self.assertNotIn("CLOUD_CODE_URL", os.environ)

            # When not in os.environ either, returns empty list
            removed2 = system_windows.remove_endpoint()
            self.assertEqual(removed2, [])


# =====================================================================
# Windows Binary Finder and Patcher Tests
# =====================================================================

class TestWindowsBinaryFinderAndPatcher(unittest.TestCase):
    """Tests for system_windows binary discovery, inspection, patching, and restoration."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_find_binary_configured_path(self):
        bin_file = self.temp_path / "custom_agy.exe"
        bin_file.write_bytes(b"dummy")
        self.assertEqual(system_windows.find_binary(str(bin_file)), bin_file.resolve())

    def test_find_binary_configured_nonexistent(self):
        self.assertIsNone(system_windows.find_binary(str(self.temp_path / "missing.exe")))

    def test_find_binary_configured_env_vars(self):
        import ntpath
        bin_file = self.temp_path / "agy.exe"
        bin_file.write_bytes(b"dummy")
        with patch.dict(os.environ, {"TEST_WIN_BIN": str(self.temp_path)}):
            # Test POSIX expansion $VAR
            found = system_windows.find_binary("$TEST_WIN_BIN/agy.exe")
            self.assertEqual(found, bin_file.resolve())
            # Test Windows expansion %VAR% with ntpath.expandvars
            with patch("os.path.expandvars", side_effect=ntpath.expandvars):
                found_nt = system_windows.find_binary("%TEST_WIN_BIN%/agy.exe")
                self.assertEqual(found_nt, bin_file.resolve())

    def test_find_binary_auto_localappdata_programs(self):
        agy_path = self.temp_path / "Programs" / "antigravity" / "agy.exe"
        agy_path.parent.mkdir(parents=True, exist_ok=True)
        agy_path.write_bytes(b"dummy")

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.temp_path)}):
            found = system_windows.find_binary("auto")
            self.assertEqual(found, agy_path.resolve())

    def test_find_binary_auto_localappdata_direct(self):
        agy_path = self.temp_path / "antigravity" / "agy.exe"
        agy_path.parent.mkdir(parents=True, exist_ok=True)
        agy_path.write_bytes(b"dummy")

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.temp_path)}):
            found = system_windows.find_binary("auto")
            self.assertEqual(found, agy_path.resolve())

    def test_find_binary_auto_appdata(self):
        agy_path = self.temp_path / "antigravity" / "agy.exe"
        agy_path.parent.mkdir(parents=True, exist_ok=True)
        agy_path.write_bytes(b"dummy")

        clean_env = {"LOCALAPPDATA": "", "APPDATA": str(self.temp_path)}
        with patch.dict(os.environ, clean_env):
            found = system_windows.find_binary("auto")
            self.assertEqual(found, agy_path.resolve())

    def test_find_binary_auto_programfiles(self):
        agy_path = self.temp_path / "Antigravity" / "agy.exe"
        agy_path.parent.mkdir(parents=True, exist_ok=True)
        agy_path.write_bytes(b"dummy")

        clean_env = {"LOCALAPPDATA": "", "APPDATA": "", "ProgramFiles": str(self.temp_path)}
        with patch.dict(os.environ, clean_env):
            found = system_windows.find_binary("auto")
            self.assertEqual(found, agy_path.resolve())

    def test_find_binary_auto_programfiles_x86(self):
        agy_path = self.temp_path / "Antigravity" / "agy.exe"
        agy_path.parent.mkdir(parents=True, exist_ok=True)
        agy_path.write_bytes(b"dummy")

        clean_env = {
            "LOCALAPPDATA": "",
            "APPDATA": "",
            "ProgramFiles": "",
            "ProgramFiles(x86)": str(self.temp_path),
        }
        with patch.dict(os.environ, clean_env):
            found = system_windows.find_binary("auto")
            self.assertEqual(found, agy_path.resolve())

    def test_find_binary_auto_path(self):
        bin_file = self.temp_path / "agy.exe"
        bin_file.write_bytes(b"dummy")

        clean_env = {
            "LOCALAPPDATA": "",
            "APPDATA": "",
            "ProgramFiles": "",
            "ProgramFiles(x86)": "",
        }
        with patch.dict(os.environ, clean_env), \
             patch("shutil.which", return_value=str(bin_file)):
            found = system_windows.find_binary("auto")
            self.assertEqual(found, bin_file.resolve())

    def test_find_binary_auto_none(self):
        clean_env = {
            "LOCALAPPDATA": "",
            "APPDATA": "",
            "ProgramFiles": "",
            "ProgramFiles(x86)": "",
        }
        with patch.dict(os.environ, clean_env), \
             patch("shutil.which", return_value=None):
            self.assertIsNone(system_windows.find_binary("auto"))

    def test_get_binary_status_variants(self):
        missing = self.temp_path / "missing.exe"
        self.assertEqual(system_windows.get_binary_status(missing), "FILE_NOT_FOUND")

        ready = self.temp_path / "ready.exe"
        ready.write_bytes(b"pre_ineligible_post")
        self.assertEqual(system_windows.get_binary_status(ready), "READY_TO_PATCH")

        patched = self.temp_path / "patched.exe"
        patched.write_bytes(b"pre_inexigible_post")
        self.assertEqual(system_windows.get_binary_status(patched), "ALREADY_PATCHED")

        not_found = self.temp_path / "unknown.exe"
        not_found.write_bytes(b"pre_normal_binary_post")
        self.assertEqual(system_windows.get_binary_status(not_found), "SIGNATURE_NOT_FOUND")

    def test_apply_patch_and_restore_cycle(self):
        bin_file = self.temp_path / "agy.exe"
        original_data = b"WINDOWS_PE_DATA_ineligible_END"
        bin_file.write_bytes(original_data)

        # 1. Check initial status
        self.assertEqual(system_windows.get_binary_status(bin_file), "READY_TO_PATCH")

        # 2. Apply patch
        ok, msg = system_windows.apply_patch(bin_file)
        self.assertTrue(ok)
        self.assertIn("успешно пропатчен", msg)
        self.assertEqual(bin_file.read_bytes(), b"WINDOWS_PE_DATA_inexigible_END")
        self.assertEqual(system_windows.get_binary_status(bin_file), "ALREADY_PATCHED")

        bak_file = bin_file.with_name("agy.exe.bak")
        self.assertTrue(bak_file.is_file(), ".bak backup file must be created")
        self.assertEqual(bak_file.read_bytes(), original_data)

        # 3. Patching again is idempotent
        ok, msg = system_windows.apply_patch(bin_file)
        self.assertTrue(ok)
        self.assertIn("уже пропатчен", msg)

        # 4. Restore backup
        ok, msg = system_windows.restore_backup(bin_file)
        self.assertTrue(ok)
        self.assertIn("восстановлен", msg)
        self.assertEqual(bin_file.read_bytes(), original_data)
        self.assertFalse(bak_file.exists(), ".bak backup file must be deleted after restore")
        self.assertEqual(system_windows.get_binary_status(bin_file), "READY_TO_PATCH")

    def test_apply_patch_missing_file(self):
        ok, msg = system_windows.apply_patch(self.temp_path / "nonexistent.exe")
        self.assertFalse(ok)
        self.assertIn("Файл не найден", msg)

    def test_apply_patch_missing_signature(self):
        bin_file = self.temp_path / "unsig.exe"
        bin_file.write_bytes(b"clean binary without signature")
        ok, msg = system_windows.apply_patch(bin_file)
        self.assertFalse(ok)
        self.assertIn("Сигнатура 'ineligible' не найдена", msg)

    def test_restore_backup_missing_bak(self):
        bin_file = self.temp_path / "nobak.exe"
        bin_file.write_bytes(b"dummy")
        ok, msg = system_windows.restore_backup(bin_file)
        self.assertFalse(ok)
        self.assertIn("Бэкап не найден", msg)


# =====================================================================
# Windows Task Scheduler Tests
# =====================================================================

class TestWindowsTaskScheduler(unittest.TestCase):
    """Tests for system_windows.is_timer_active() with schtasks."""

    @patch("subprocess.run")
    def test_is_timer_active_true(self, mock_run):
        mock_run.return_value.returncode = 0
        self.assertTrue(system_windows.is_timer_active())
        mock_run.assert_called_once_with(
            ["schtasks", "/query", "/tn", "AntigravityBypassCheck"],
            capture_output=True,
            timeout=5,
        )

    @patch("subprocess.run")
    def test_is_timer_active_false(self, mock_run):
        mock_run.return_value.returncode = 1
        self.assertFalse(system_windows.is_timer_active())

    @patch("subprocess.run")
    def test_is_timer_active_exception(self, mock_run):
        mock_run.side_effect = FileNotFoundError("schtasks not found")
        self.assertFalse(system_windows.is_timer_active())


# =====================================================================
# Platform Dispatcher Tests (core.system)
# =====================================================================

class TestSystemDispatcher(unittest.TestCase):
    """Tests for core.system dynamic platform dispatching based on sys.platform."""

    def tearDown(self):
        # Guarantee core.system is restored to native Linux after tests
        with patch.object(sys, "platform", "linux"):
            importlib.reload(system)

    def test_dispatcher_win32(self):
        with patch.object(sys, "platform", "win32"):
            importlib.reload(system)
            self.assertEqual(system.DEFAULT_HOSTS_PATH, system_windows.DEFAULT_HOSTS_PATH)
            self.assertEqual(system.is_admin, system_windows.is_admin)
            self.assertEqual(system.flush_dns_cache, system_windows.flush_dns_cache)
            self.assertEqual(system.apply_endpoint, system_windows.apply_endpoint)
            self.assertEqual(system.remove_endpoint, system_windows.remove_endpoint)
            self.assertEqual(system.get_endpoint_status, system_windows.get_endpoint_status)
            self.assertEqual(system.find_binary, system_windows.find_binary)
            self.assertEqual(system.get_binary_status, system_windows.get_binary_status)
            self.assertEqual(system.apply_patch, system_windows.apply_patch)
            self.assertEqual(system.restore_backup, system_windows.restore_backup)
            self.assertEqual(system.is_timer_active, system_windows.is_timer_active)

    def test_dispatcher_linux(self):
        with patch.object(sys, "platform", "linux"):
            importlib.reload(system)
            self.assertEqual(system.DEFAULT_HOSTS_PATH, system_linux.DEFAULT_HOSTS_PATH)
            self.assertEqual(system.is_admin, system_linux.is_admin)
            self.assertEqual(system.flush_dns_cache, system_linux.flush_dns_cache)
            self.assertEqual(system.apply_endpoint, system_linux.apply_endpoint)
            self.assertEqual(system.remove_endpoint, system_linux.remove_endpoint)
            self.assertEqual(system.get_endpoint_status, system_linux.get_endpoint_status)
            self.assertEqual(system.find_binary, system_linux.find_binary)
            self.assertEqual(system.get_binary_status, system_linux.get_binary_status)
            self.assertEqual(system.apply_patch, system_linux.apply_patch)
            self.assertEqual(system.restore_backup, system_linux.restore_backup)
            self.assertEqual(system.is_timer_active, system_linux.is_timer_active)

    def test_dispatcher_exports_all_contracts(self):
        expected_contracts = [
            "TAG_BEGIN",
            "TAG_END",
            "DEFAULT_HOSTS_PATH",
            "is_admin",
            "flush_dns_cache",
            "get_active_entries",
            "set_entries",
            "clear_entries",
            "apply_endpoint",
            "remove_endpoint",
            "get_endpoint_status",
            "find_binary",
            "get_binary_status",
            "apply_patch",
            "restore_backup",
            "is_timer_active",
        ]
        for contract in expected_contracts:
            self.assertIn(contract, system.__all__, f"Missing contract symbol in __all__: {contract}")
            self.assertTrue(hasattr(system, contract), f"Missing attribute in system module: {contract}")


# =====================================================================
# CLI Admin Requirement Tests (_req_admin)
# =====================================================================

class TestCLIAdminRequirement(unittest.TestCase):
    """Tests for ag._req_admin message and behavior differences between Windows and Linux."""

    def test_req_admin_linux_non_admin(self):
        out = io.StringIO()
        with patch("sys.stdout", out), \
             patch("core.system.is_admin", return_value=False), \
             patch.object(sys, "platform", "linux"):
            with self.assertRaises(SystemExit) as ctx:
                ag_cli._req_admin("on")
            self.assertEqual(ctx.exception.code, 1)

        captured = out.getvalue()
        self.assertIn("права root", captured)
        self.assertIn("sudo", captured)

    def test_req_admin_linux_admin(self):
        with patch("core.system.is_admin", return_value=True), \
             patch.object(sys, "platform", "linux"):
            # Must return without raising SystemExit
            ag_cli._req_admin("on")

    def test_req_admin_windows_non_admin(self):
        out = io.StringIO()
        with patch("sys.stdout", out), \
             patch("core.system.is_admin", return_value=False), \
             patch.object(sys, "platform", "win32"):
            with self.assertRaises(SystemExit) as ctx:
                ag_cli._req_admin("on")
            self.assertEqual(ctx.exception.code, 1)

        captured = out.getvalue()
        self.assertIn("права Администратора", captured)
        self.assertIn("PowerShell от имени Администратора", captured)

    def test_req_admin_windows_admin(self):
        with patch("core.system.is_admin", return_value=True), \
             patch.object(sys, "platform", "win32"):
            # Must return without raising SystemExit
            ag_cli._req_admin("on")


if __name__ == "__main__":
    unittest.main()

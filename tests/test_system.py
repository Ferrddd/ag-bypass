import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import system


@unittest.skipIf(sys.platform == "win32", "Linux/Unix-specific system tests skipped on Windows")
class TestHostsManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.hosts_path = Path(self.temp_dir.name).resolve() / "hosts"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_active_entries_missing_file(self):
        self.assertEqual(system.get_active_entries(self.hosts_path), [])

    def test_set_and_get_active_entries(self):
        self.hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
        entries = [("1.2.3.4", "example.com"), ("1.2.3.5", "api.example.com")]
        ok, msg = system.set_entries(entries, hosts_path=self.hosts_path)
        self.assertTrue(ok)
        self.assertIn("успешно", msg)

        active = system.get_active_entries(self.hosts_path)
        self.assertEqual(active, entries)
        content = self.hosts_path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("127.0.0.1 localhost\n"))
        self.assertIn(system.TAG_BEGIN, content)
        self.assertIn(system.TAG_END, content)

    def test_set_entries_multi_node_same_domain(self):
        self.hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
        entries = [("1.2.3.4", "example.com"), ("1.2.3.5", "example.com")]
        ok, msg = system.set_entries(entries, hosts_path=self.hosts_path)
        self.assertTrue(ok)
        active = system.get_active_entries(self.hosts_path)
        self.assertEqual(active, entries)
        content = self.hosts_path.read_text(encoding="utf-8")
        self.assertIn("1.2.3.4 example.com", content)
        self.assertIn("1.2.3.5 example.com", content)

    def test_clear_entries(self):
        self.hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
        system.set_entries([("1.2.3.4", "example.com")], hosts_path=self.hosts_path)
        self.assertEqual(len(system.get_active_entries(self.hosts_path)), 1)

        ok, msg = system.clear_entries(hosts_path=self.hosts_path)
        self.assertTrue(ok)
        self.assertEqual(system.get_active_entries(self.hosts_path), [])
        content = self.hosts_path.read_text(encoding="utf-8")
        self.assertNotIn(system.TAG_BEGIN, content)
        self.assertIn("127.0.0.1 localhost", content)

    def test_hosts_multi_domain_and_comments(self):
        content = f"127.0.0.1 localhost\n{system.TAG_BEGIN}\n# Comment line\n10.0.0.1 alpha.com beta.com # inline comment\n{system.TAG_END}\n"
        self.hosts_path.write_text(content, encoding="utf-8")
        entries = system.get_active_entries(self.hosts_path)
        self.assertEqual(entries, [("10.0.0.1", "alpha.com"), ("10.0.0.1", "beta.com")])

    def test_hosts_unclosed_tag_cleanup(self):
        corrupt = f"127.0.0.1 localhost\n{system.TAG_BEGIN}\n10.0.0.1 alpha.com\n"
        self.hosts_path.write_text(corrupt, encoding="utf-8")
        ok, msg = system.set_entries([("10.0.0.2", "beta.com")], hosts_path=self.hosts_path)
        self.assertTrue(ok)
        entries = system.get_active_entries(self.hosts_path)
        self.assertEqual(entries, [("10.0.0.2", "beta.com")])
        text = self.hosts_path.read_text(encoding="utf-8")
        self.assertEqual(text.count(system.TAG_BEGIN), 1)

    def test_hosts_symlink_atomic_update(self):
        real_file = self.hosts_path.with_name("real_hosts")
        real_file.write_text("127.0.0.1 localhost\n", encoding="utf-8")
        self.hosts_path.symlink_to(real_file)
        self.assertTrue(self.hosts_path.is_symlink())

        ok, msg = system.set_entries([("10.0.0.1", "example.com")], hosts_path=self.hosts_path)
        self.assertTrue(ok)
        self.assertTrue(self.hosts_path.is_symlink())
        self.assertEqual(system.get_active_entries(self.hosts_path), [("10.0.0.1", "example.com")])

    def test_hosts_orphaned_end_tag_cleanup(self):
        corrupt = f"127.0.0.1 localhost\n10.0.0.1 alpha.com\n{system.TAG_END}\n"
        self.hosts_path.write_text(corrupt, encoding="utf-8")
        ok, msg = system.set_entries([("10.0.0.2", "beta.com")], hosts_path=self.hosts_path)
        self.assertTrue(ok)
        text = self.hosts_path.read_text(encoding="utf-8")
        self.assertEqual(text.count(system.TAG_END), 1)

        self.hosts_path.write_text(corrupt, encoding="utf-8")
        ok, msg = system.clear_entries(hosts_path=self.hosts_path)
        self.assertTrue(ok)
        text = self.hosts_path.read_text(encoding="utf-8")
        self.assertNotIn(system.TAG_END, text)

    def test_hosts_set_entries_empty_clears(self):
        self.hosts_path.write_text(f"127.0.0.1 localhost\n{system.TAG_BEGIN}\n1.1.1.1 test.com\n{system.TAG_END}\n")
        ok, msg = system.set_entries([], hosts_path=self.hosts_path)
        self.assertTrue(ok)
        self.assertEqual(system.get_active_entries(self.hosts_path), [])
        text = self.hosts_path.read_text(encoding="utf-8")
        self.assertNotIn(system.TAG_BEGIN, text)

    def test_hosts_injection_sanitization(self):
        self.hosts_path.write_text("127.0.0.1 localhost\n", encoding="utf-8")
        entries = [("10.0.0.1\n127.0.0.1 evil.com", "good.com\nfake.com")]
        ok, msg = system.set_entries(entries, hosts_path=self.hosts_path)
        self.assertTrue(ok)
        active = system.get_active_entries(self.hosts_path)
        self.assertEqual(active, [("10.0.0.1", "good.com")])
        text = self.hosts_path.read_text(encoding="utf-8")
        self.assertNotIn("evil.com", text)
        self.assertNotIn("fake.com", text)


@unittest.skipIf(sys.platform == "win32", "Linux/Unix-specific system tests skipped on Windows")
class TestPatcher(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_binary_status_not_found(self):
        self.assertEqual(system.get_binary_status(self.temp_path / "missing"), "FILE_NOT_FOUND")

    def test_binary_patch_and_restore_cycle(self):
        bin_file = self.temp_path / "agy"
        original_data = b"PREFIX_ineligible_SUFFIX"
        bin_file.write_bytes(original_data)
        bin_file.chmod(0o750)

        # 1. Status ready
        self.assertEqual(system.get_binary_status(bin_file), "READY_TO_PATCH")

        # 2. Patch & check permissions preserved
        ok, msg = system.apply_patch(bin_file)
        self.assertTrue(ok)
        self.assertEqual(system.get_binary_status(bin_file), "ALREADY_PATCHED")
        self.assertEqual(bin_file.read_bytes(), b"PREFIX_inexigible_SUFFIX")
        self.assertEqual(bin_file.stat().st_mode & 0o777, 0o750)

        bak_file = bin_file.with_name("agy.bak")
        self.assertFalse(bak_file.exists(), ".bak backup must not be created")

        # 3. Patching again is idempotent
        ok, msg = system.apply_patch(bin_file)
        self.assertTrue(ok)
        self.assertIn("уже пропатчен", msg)

        # 4. Restore & check permissions preserved (reverts patch in-place)
        ok, msg = system.restore_backup(bin_file)
        self.assertTrue(ok)
        self.assertEqual(bin_file.read_bytes(), original_data)
        self.assertEqual(bin_file.stat().st_mode & 0o777, 0o750)
        self.assertFalse(bak_file.exists())
        self.assertEqual(system.get_binary_status(bin_file), "READY_TO_PATCH")

    def test_binary_patch_and_restore_symlink(self):
        real_bin = self.temp_path / "real_agy"
        sym_bin = self.temp_path / "sym_agy"
        original_data = b"PREFIX_ineligible_SUFFIX"
        real_bin.write_bytes(original_data)
        sym_bin.symlink_to(real_bin)

        self.assertTrue(sym_bin.is_symlink())
        self.assertEqual(system.get_binary_status(sym_bin), "READY_TO_PATCH")

        # Patch via symlink must patch real target without breaking symlink
        ok, msg = system.apply_patch(sym_bin)
        self.assertTrue(ok)
        self.assertTrue(sym_bin.is_symlink(), "Symlink must NOT be replaced with regular file")
        self.assertEqual(real_bin.read_bytes(), b"PREFIX_inexigible_SUFFIX")
        self.assertEqual(system.get_binary_status(sym_bin), "ALREADY_PATCHED")

        real_bak = real_bin.with_name("real_agy.bak")
        self.assertFalse(real_bak.exists(), ".bak backup must not be created")

        # Restore via symlink must revert real target without breaking symlink
        ok, msg = system.restore_backup(sym_bin)
        self.assertTrue(ok)
        self.assertTrue(sym_bin.is_symlink(), "Symlink must be preserved after restore")
        self.assertEqual(real_bin.read_bytes(), original_data)
        self.assertFalse(real_bak.exists())

    def test_find_binary(self):
        bin_file = self.temp_path / "custom_agy"
        bin_file.write_bytes(b"dummy")
        self.assertEqual(system.find_binary(str(bin_file)), bin_file.resolve())
        self.assertEqual(system.find_binary(f'"{bin_file}"'), bin_file.resolve())
        self.assertIsNone(system.find_binary(str(self.temp_path / "nonexistent")))

        # Test directory resolution
        cand_dir = self.temp_path / "app_dir"
        cand_dir.mkdir(parents=True, exist_ok=True)
        app_bin = cand_dir / "Antigravity"
        app_bin.write_bytes(b"dummy")
        self.assertEqual(system.find_binary(str(cand_dir)), app_bin.resolve())

    def test_find_binary_macos_app_bundle_configured_and_auto(self):
        import plistlib
        from unittest.mock import patch

        apps_dir = self.temp_path / "Applications"
        app_bundle = apps_dir / "Antigravity.app"
        macos_dir = app_bundle / "Contents" / "MacOS"
        macos_dir.mkdir(parents=True, exist_ok=True)
        electron_bin = macos_dir / "Electron"
        electron_bin.write_bytes(b"dummy")

        # Without Info.plist: configured .app bundle dir resolves Electron
        self.assertEqual(system.find_binary(str(app_bundle)), electron_bin.resolve())

        # With Info.plist CFBundleExecutable pointing to custom binary name
        custom_bin = macos_dir / "CustomEntry"
        custom_bin.write_bytes(b"dummy")
        plist_path = app_bundle / "Contents" / "Info.plist"
        with plist_path.open("wb") as f:
            plistlib.dump({"CFBundleExecutable": "CustomEntry"}, f)
        self.assertEqual(system.find_binary(str(app_bundle)), custom_bin.resolve())

        # Auto-discovery finds /Applications/Antigravity.app via _SYS_APPS
        custom_bin.unlink()
        with plist_path.open("wb") as f:
            plistlib.dump({"CFBundleExecutable": "Electron"}, f)
        with patch("core.system.user_home", return_value=self.temp_path / "empty_home"), \
             patch("pathlib.Path.home", return_value=self.temp_path / "empty_home"), \
             patch("core.system_linux._SYS_APPS", apps_dir), \
             patch("shutil.which", return_value=None):
            self.assertEqual(system.find_binary("auto"), electron_bin.resolve())

    def test_find_binary_macos_user_applications_and_language_server_fallback(self):
        from unittest.mock import patch

        user_home_dir = self.temp_path / "user_home"
        ide_app = user_home_dir / "Applications" / "Antigravity IDE.app"
        ls_dir = ide_app / "Contents" / "Resources" / "app" / "extensions" / "antigravity" / "bin"
        ls_dir.mkdir(parents=True, exist_ok=True)
        ls_arm = ls_dir / "language_server_macos_arm"
        ls_arm.write_bytes(b"dummy")

        with patch("core.system.user_home", return_value=user_home_dir), \
             patch("pathlib.Path.home", return_value=user_home_dir), \
             patch("core.system_linux._SYS_APPS", self.temp_path / "no_sys_apps"), \
             patch("platform.machine", return_value="arm64"), \
             patch("shutil.which", return_value=None):
            self.assertEqual(system.find_binary("auto"), ls_arm.resolve())

    def test_find_binary_macos_prefers_resources_bin_language_server(self):
        from unittest.mock import patch

        apps_dir = self.temp_path / "Applications"
        app_bundle = apps_dir / "Antigravity.app"
        macos_dir = app_bundle / "Contents" / "MacOS"
        ls_dir = app_bundle / "Contents" / "Resources" / "bin"
        macos_dir.mkdir(parents=True, exist_ok=True)
        ls_dir.mkdir(parents=True, exist_ok=True)
        (macos_dir / "Antigravity").write_bytes(b"stub")
        ls_bin = ls_dir / "language_server"
        ls_bin.write_bytes(b"real_ls")

        self.assertEqual(system.find_binary(str(app_bundle)), ls_bin.resolve())
        with patch("core.system.user_home", return_value=self.temp_path / "empty_home"), \
             patch("pathlib.Path.home", return_value=self.temp_path / "empty_home"), \
             patch("core.system_linux._SYS_APPS", apps_dir), \
             patch("shutil.which", return_value=None):
            self.assertEqual(system.find_binary("auto"), ls_bin.resolve())

    def test_apply_patch_resigns_macho_on_darwin(self):
        from unittest.mock import MagicMock, patch

        bin_file = self.temp_path / "language_server"
        bin_file.write_bytes(b"\xcf\xfa\xed\xfe_PAYLOAD_ineligible_END")

        mock_res = MagicMock(returncode=0, stderr="", stdout="")
        with patch.object(sys, "platform", "darwin"), \
             patch("shutil.which", return_value="/usr/bin/codesign"), \
             patch("subprocess.run", return_value=mock_res) as mock_run:
            ok, msg = system.apply_patch(bin_file)
            self.assertTrue(ok)
            self.assertIn("переподписан", msg)
            self.assertEqual(bin_file.read_bytes(), b"\xcf\xfa\xed\xfe_PAYLOAD_inexigible_END")
            mock_run.assert_called_once_with(
                ["codesign", "--force", "--sign", "-", str(bin_file.resolve())],
                capture_output=True,
                text=True,
                timeout=30,
            )

    def test_apply_patch_macos_app_permission_hint(self):
        from unittest.mock import patch

        app_bin = self.temp_path / "Antigravity.app" / "Contents" / "Resources" / "bin" / "language_server"
        app_bin.parent.mkdir(parents=True, exist_ok=True)
        app_bin.write_bytes(b"PAYLOAD_ineligible_END")

        with patch.object(sys, "platform", "darwin"), \
             patch("os.replace", side_effect=PermissionError(1, "Operation not permitted")):
            ok, msg = system.apply_patch(app_bin)
            self.assertFalse(ok)
            self.assertIn("App Management", msg)

    def test_stream_replace_boundary_across_chunks(self):
        from core import system_linux

        src = self.temp_path / "src_bin"
        dst = self.temp_path / "dst_bin"
        # Chunk size 12 forces "ineligible" (10 bytes) to straddle chunk boundaries
        payload = b"AAAAineligibleBBBBineligibleCCCC"
        src.write_bytes(payload)
        system_linux._stream_replace_to_tmp(src, dst, b"ineligible", b"inexigible", chunk_size=12)
        self.assertEqual(dst.read_bytes(), b"AAAAinexigibleBBBBinexigibleCCCC")

    def test_find_binaries_discovers_both_cli_and_gui(self):
        from unittest.mock import patch

        user_home_dir = self.temp_path / "home"
        cli_bin = user_home_dir / ".local" / "bin" / "agy"
        cli_bin.parent.mkdir(parents=True, exist_ok=True)
        cli_bin.write_bytes(b"CLI_ineligible")

        apps_dir = self.temp_path / "Applications"
        app_bundle = apps_dir / "Antigravity.app"
        (app_bundle / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
        (app_bundle / "Contents" / "MacOS" / "Antigravity").write_bytes(b"stub")
        ls_dir = app_bundle / "Contents" / "Resources" / "bin"
        ls_dir.mkdir(parents=True, exist_ok=True)
        gui_bin = ls_dir / "language_server"
        gui_bin.write_bytes(b"GUI_ineligible")

        with patch("core.system.user_home", return_value=user_home_dir), \
             patch("pathlib.Path.home", return_value=user_home_dir), \
             patch("core.system_linux._SYS_APPS", apps_dir), \
             patch("shutil.which", return_value=None):
            found = system.find_binaries("auto")
            self.assertEqual(found, [cli_bin.resolve(), gui_bin.resolve()])

            # Patch both via loop just like cmd_on
            for bp in found:
                ok, _ = system.apply_patch(bp)
                self.assertTrue(ok)
            self.assertEqual(system.get_binary_status(cli_bin), "ALREADY_PATCHED")
            self.assertEqual(system.get_binary_status(gui_bin), "ALREADY_PATCHED")
            self.assertFalse(cli_bin.with_name("agy.bak").exists())
            self.assertFalse(gui_bin.with_name("language_server.bak").exists())


@unittest.skipIf(sys.platform == "win32", "Linux/Unix-specific system tests skipped on Windows")
class TestEndpointManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name).resolve()
        self.sys_env = self.temp_path / "sys_env.d/10-antigravity.conf"
        self.sys_prof = self.temp_path / "sys_prof.d/antigravity.sh"
        self.sys_env.parent.mkdir(parents=True, exist_ok=True)
        self.sys_prof.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_apply_and_remove_endpoint(self):
        from unittest.mock import patch
        with patch("core.system.is_admin", return_value=True), \
             patch("core.system.user_home", return_value=self.temp_path), \
             patch("core.system._SYS_ENV", self.sys_env), \
             patch("core.system._SYS_PROF", self.sys_prof):
            applied = system.apply_endpoint("https://custom.api.endpoint")
            self.assertTrue(any(str(self.temp_path) in p for p in applied))

            has_ep, desc = system.get_endpoint_status()
            self.assertTrue(has_ep)
            self.assertIn("custom.api.endpoint", desc)

            removed = system.remove_endpoint()
            self.assertTrue(any(str(self.temp_path) in p for p in removed))

            has_ep, desc = system.get_endpoint_status()
            self.assertFalse(has_ep)

    def test_apply_endpoint_sanitization(self):
        from unittest.mock import patch
        with patch("core.system.is_admin", return_value=True), \
             patch("core.system.user_home", return_value=self.temp_path), \
             patch("core.system._SYS_ENV", self.sys_env), \
             patch("core.system._SYS_PROF", self.sys_prof):
            system.apply_endpoint('https://example.com"; rm -rf /; echo "')
            env_file = self.temp_path / ".config/environment.d/10-antigravity.conf"
            content = env_file.read_text(encoding="utf-8")
            self.assertNotIn('"', content)
            self.assertNotIn(';', content)
            self.assertIn("CLOUD_CODE_URL=https://example.com rm -rf / echo \n", content)
            prof_content = self.sys_prof.read_text(encoding="utf-8")
            self.assertNotIn(';', prof_content)
            self.assertIn('export CLOUD_CODE_URL="https://example.com rm -rf / echo "\n', prof_content)

    def test_get_endpoint_status_profile_d(self):
        from unittest.mock import patch
        self.sys_prof.write_text('export CLOUD_CODE_URL="https://prof.example.com"\n')
        with patch("core.system._SYS_PROF", self.sys_prof), patch("core.system.user_home", return_value=self.temp_path / "empty"):
            has_ep, desc = system.get_endpoint_status()
            self.assertTrue(has_ep)
            self.assertIn("https://prof.example.com", desc)

        # Empty profile.d should return False
        self.sys_prof.write_text('# empty\n')
        with patch("core.system._SYS_PROF", self.sys_prof), patch("core.system.user_home", return_value=self.temp_path / "empty"):
            has_ep, desc = system.get_endpoint_status()
            self.assertFalse(has_ep)

        # Empty CLOUD_CODE_URL value should return False
        self.sys_prof.write_text('export CLOUD_CODE_URL=""\n')
        with patch("core.system._SYS_PROF", self.sys_prof), patch("core.system.user_home", return_value=self.temp_path / "empty"):
            has_ep, desc = system.get_endpoint_status()
            self.assertFalse(has_ep)


@unittest.skipIf(sys.platform == "win32", "Linux/Unix-specific system tests skipped on Windows")
class TestCLINodesAndStatus(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name).resolve()
        self.cfg_file = self.temp_path / "test_cfg.json"
        import json
        self.cfg_file.write_text(json.dumps({
            "smart_dns": {"servers": []},
            "nodes": ["127.0.0.1", "127.0.0.2"],
            "domains": ["example.com"],
            "healthcheck": {"connect_timeout_seconds": 0.05}
        }), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cli_nodes_runs_cleanly(self):
        ag_bin = Path(__file__).resolve().parent.parent / "ag"
        res = subprocess.run([sys.executable, str(ag_bin), "-c", str(self.cfg_file), "nodes"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("TLS SNI-проверка", res.stdout)
        self.assertIn("SNI СТАТУС", res.stdout)

    def test_cmd_nodes_table_alignment(self):
        ag_bin = Path(__file__).resolve().parent.parent / "ag"
        res = subprocess.run([sys.executable, str(ag_bin), "-c", str(self.cfg_file), "nodes"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        import re
        lines = res.stdout.splitlines()
        header_idx = -1
        for idx, line in enumerate(lines):
            clean_line = re.sub(r"\033\[[0-9;]*m", "", line)
            if "IP" in clean_line and "SNI СТАТУС" in clean_line and "ДЕТАЛИ" in clean_line:
                header_idx = idx
                break
        self.assertNotEqual(header_idx, -1)
        clean_header = re.sub(r"\033\[[0-9;]*m", "", lines[header_idx])
        details_col = clean_header.index("ДЕТАЛИ")
        for line in lines[header_idx + 2:]:
            clean = re.sub(r"\033\[[0-9;]*m", "", line)
            if clean.strip() and not clean.startswith("-"):
                det_part = clean[details_col:].strip()
                if det_part:
                    self.assertEqual(clean.find(det_part, details_col), details_col)


if __name__ == "__main__":
    unittest.main()

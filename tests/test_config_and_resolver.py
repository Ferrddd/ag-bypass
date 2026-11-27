import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.config import Config, find_config
from core import network, system
import core


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_config_no_hardcoded_dns_or_ip(self):
        cfg_file = self.temp_path / "config.json"
        cfg_file.write_text("{}", encoding="utf-8")
        cfg = Config(cfg_file)
        self.assertEqual(cfg.smart_dns_servers, [])
        self.assertEqual(cfg.nodes, [])
        self.assertEqual(cfg.check_interval_minutes, 2)

    def test_validation_fails_on_empty(self):
        cfg_file = self.temp_path / "config.json"
        cfg_file.write_text("{}", encoding="utf-8")
        cfg = Config(cfg_file)
        errors = cfg.validate()
        self.assertTrue(any("smart_dns.servers" in e or "nodes" in e for e in errors))

    def test_validation_succeeds_with_nodes(self):
        cfg_file = self.temp_path / "config.json"
        cfg_file.write_text(json.dumps({"nodes": ["1.1.1.1"]}), encoding="utf-8")
        cfg = Config(cfg_file)
        self.assertEqual(cfg.validate(), [])
        self.assertEqual(cfg.nodes, ["1.1.1.1"])

    def test_validation_succeeds_with_dns_servers_list(self):
        cfg_file = self.temp_path / "config.json"
        cfg_file.write_text(
            json.dumps({"smart_dns": {"servers": ["dns1.example.com", "1.1.1.1", "8.8.8.8"]}}),
            encoding="utf-8",
        )
        cfg = Config(cfg_file)
        self.assertEqual(cfg.validate(), [])
        self.assertEqual(cfg.smart_dns_servers, ["dns1.example.com", "1.1.1.1", "8.8.8.8"])

    def test_backward_compat_with_host_and_fallback(self):
        cfg_file = self.temp_path / "config.json"
        cfg_file.write_text(
            json.dumps({"smart_dns": {"host": "dns.example.com", "fallback_ip": "1.2.3.4"}}),
            encoding="utf-8",
        )
        cfg = Config(cfg_file)
        self.assertEqual(cfg.smart_dns_servers, ["dns.example.com", "1.2.3.4"])

    def test_invalid_json_raises_value_error(self):
        cfg_file = self.temp_path / "bad.json"
        cfg_file.write_text("{ invalid json", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            Config(cfg_file)
        self.assertIn("Ошибка синтаксиса JSON", str(ctx.exception))

    def test_nonexistent_file_raises_filenotfound(self):
        with self.assertRaises(FileNotFoundError):
            Config(self.temp_path / "does_not_exist.json")

    def test_env_var_config(self):
        cfg_file = self.temp_path / "custom.json"
        cfg_file.write_text(json.dumps({"nodes": ["9.9.9.9"]}), encoding="utf-8")
        old_env = os.environ.get("AG_CONFIG")
        try:
            os.environ["AG_CONFIG"] = str(cfg_file)
            found = find_config()
            self.assertEqual(found.resolve(), cfg_file.resolve())
            cfg = Config()
            self.assertEqual(cfg.nodes, ["9.9.9.9"])
        finally:
            if old_env is None:
                os.environ.pop("AG_CONFIG", None)
            else:
                os.environ["AG_CONFIG"] = old_env

    def test_config_empty_domains_list_no_crash(self):
        cfg_file = self.temp_path / "empty_dom.json"
        cfg_file.write_text(json.dumps({"domains": ["  "]}), encoding="utf-8")
        cfg = Config(cfg_file)
        self.assertEqual(cfg.domains, [])
        self.assertEqual(cfg.endpoint_url, "")
        self.assertIn("Список доменов (domains) пуст", cfg.validate())

    def test_config_non_numeric_fields_fallback(self):
        cfg_file = self.temp_path / "bad_types.json"
        cfg_file.write_text(
            json.dumps({
                "smart_dns": {"port": "invalid", "timeout_seconds": "invalid"},
                "healthcheck": {"probe_port": None, "connect_timeout_seconds": "bad", "retries": "xxx", "interval_minutes": "invalid"}
            }),
            encoding="utf-8"
        )
        cfg = Config(cfg_file)
        self.assertEqual(cfg.smart_dns_port, 53)
        self.assertEqual(cfg.smart_dns_timeout, 3.0)
        self.assertEqual(cfg.check_port, 443)
        self.assertEqual(cfg.check_timeout, 2.5)
        self.assertEqual(cfg.check_retries, 3)
        self.assertEqual(cfg.check_interval_minutes, 2)

    def test_config_bounds_clamping(self):
        cfg_file = self.temp_path / "out_of_bounds.json"
        cfg_file.write_text(
            json.dumps({
                "smart_dns": {"port": 70000, "timeout_seconds": -5},
                "healthcheck": {"probe_port": -1, "connect_timeout_seconds": 0, "retries": -2, "retry_delay_seconds": -1, "interval_minutes": 0}
            }),
            encoding="utf-8"
        )
        cfg = Config(cfg_file)
        self.assertEqual(cfg.smart_dns_port, 53)
        self.assertEqual(cfg.smart_dns_timeout, 3.0)
        self.assertEqual(cfg.check_port, 443)
        self.assertEqual(cfg.check_timeout, 2.5)
        self.assertEqual(cfg.check_retries, 3)
        self.assertEqual(cfg.check_retry_delay, 1.0)
        self.assertEqual(cfg.check_interval_minutes, 2)

    def test_config_custom_interval_minutes(self):
        cfg_file = self.temp_path / "custom_interval.json"
        cfg_file.write_text(
            json.dumps({"healthcheck": {"interval_minutes": 1}}),
            encoding="utf-8"
        )
        cfg = Config(cfg_file)
        self.assertEqual(cfg.check_interval_minutes, 1)

    def test_config_auth_domains_loading(self):
        cfg_file = self.temp_path / "auth_doms.json"
        domains = [
            "daily-cloudcode-pa.googleapis.com",
            "cloudcode-pa.googleapis.com",
            "oauth2.googleapis.com",
            "accounts.google.com",
            "www.googleapis.com"
        ]
        cfg_file.write_text(json.dumps({"domains": domains}), encoding="utf-8")
        cfg = Config(cfg_file)
        self.assertEqual(cfg.domains, domains)
        self.assertEqual(cfg.domains[0], "daily-cloudcode-pa.googleapis.com")
        self.assertEqual(cfg.endpoint_url, "https://daily-cloudcode-pa.googleapis.com")

    def test_config_precedence(self):
        fake_user_dir = self.temp_path / "user_home"
        user_cfg_file = fake_user_dir / ".config/ag/config.json"
        user_cfg_file.parent.mkdir(parents=True)
        user_cfg_file.write_text(json.dumps({"nodes": ["10.99.99.99"]}), encoding="utf-8")

        with patch("pathlib.Path.home", return_value=fake_user_dir), \
             patch.dict("os.environ", {}, clear=False):
            os.environ.pop("AG_CONFIG", None)
            os.environ.pop("SUDO_USER", None)
            # When cwd does not have config.json, user config is picked
            with patch("pathlib.Path.cwd", return_value=self.temp_path / "somewhere"):
                found = find_config()
                self.assertEqual(found, user_cfg_file)


class TestNetwork(unittest.TestCase):
    def test_resolve_dns_target_direct_ip(self):
        self.assertEqual(network.resolve_target("8.8.8.8"), "8.8.8.8")
        self.assertIsNone(network.resolve_target(""))
        self.assertIsNone(network.resolve_target(None))

    def test_resolve_dns_server_removed(self):
        self.assertFalse(hasattr(network, "resolve_dns_server"))

    def test_find_config_safe_without_pwd(self):
        import core.config as cfg_mod
        old_pwd = cfg_mod.pwd
        try:
            cfg_mod.pwd = None
            with patch.dict("os.environ", {"SUDO_USER": "fake_user"}, clear=False):
                found = find_config()
                self.assertTrue(found.is_file())
        finally:
            cfg_mod.pwd = old_pwd

    def test_query_a_records_empty_inputs(self):
        self.assertEqual(network.query_a_records(None, "google.com"), [])
        self.assertEqual(network.query_a_records("", "google.com"), [])
        self.assertEqual(network.query_a_records("8.8.8.8", ""), [])

    def test_query_all_dns_servers_empty(self):
        self.assertEqual(network.query_all_dns_servers([], "example.com"), [])

    @patch("core.network.query_a_records")
    @patch("core.network.resolve_target")
    def test_query_all_dns_servers_multiple(self, mock_resolve, mock_query):
        mock_resolve.side_effect = lambda t: t if t in ("1.1.1.1", "8.8.8.8") else None
        mock_query.side_effect = lambda ip, dom, **kw: ["10.0.0.1"] if ip == "1.1.1.1" else (["10.0.0.2"] if ip == "8.8.8.8" else [])

        results = network.query_all_dns_servers(["1.1.1.1", "bad.dns", "8.8.8.8"], "example.com")
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["server"], "1.1.1.1")
        self.assertEqual(results[0]["ips"], ["10.0.0.1"])
        self.assertEqual(results[1]["server"], "bad.dns")
        self.assertIsNotNone(results[1]["error"])
        self.assertEqual(results[2]["server"], "8.8.8.8")
        self.assertEqual(results[2]["ips"], ["10.0.0.2"])

    def test_get_candidate_ips_empty(self):
        class DummyConfig:
            smart_dns_servers = []
            nodes = []

        cands, res = network.get_candidate_ips(DummyConfig(), "example.com")
        self.assertEqual(cands, [])
        self.assertIn("DNS-серверы не настроены и список nodes пуст", str(res))

    @patch("core.network.query_all_dns_servers")
    def test_get_candidate_ips_combines_servers_and_nodes(self, mock_query_all):
        mock_query_all.return_value = [
            {"server": "dns1", "dns_ip": "1.1.1.1", "ips": ["10.1.1.1", "10.1.1.2"], "error": None},
            {"server": "dns2", "dns_ip": "2.2.2.2", "ips": ["10.1.1.2", "10.1.1.3"], "error": None},
        ]
        class DummyConfig:
            smart_dns_servers = ["dns1", "dns2"]
            nodes = ["10.1.1.4"]
            smart_dns_port = 53
            smart_dns_timeout = 3.0

        cands, res = network.get_candidate_ips(DummyConfig(), "example.com")
        self.assertEqual(cands, ["10.1.1.1", "10.1.1.2", "10.1.1.3", "10.1.1.4"])
        self.assertIn("2/2 ответили", str(res))

    @patch("core.network.test_ip")
    def test_rank_ips_filters_and_sorts_by_latency(self, mock_test_ip):
        def fake_test(ip, domain, port=443, timeout=2.5):
            if ip == "10.0.0.1":
                return False, 0.0, "Connection refused"
            if ip == "10.0.0.2":
                return True, 50.0, "HTTP/1.1 200 OK"
            if ip == "10.0.0.3":
                return True, 20.0, "HTTP/1.1 200 OK"
            return False, 0.0, "Unknown"

        mock_test_ip.side_effect = fake_test
        ranked = network.rank_ips(["10.0.0.1", "10.0.0.2", "10.0.0.3"], "example.com")
        self.assertEqual(ranked, [("10.0.0.3", 20.0), ("10.0.0.2", 50.0)])

    @patch("socket.socket")
    def test_query_a_records_idna_and_invalid_labels(self, mock_socket_cls):
        mock_sock = mock_socket_cls.return_value.__enter__.return_value
        # 1. Invalid label > 63 chars -> rejected before socket is touched
        self.assertEqual(network.query_a_records("8.8.8.8", "a" * 70 + ".com"), [])
        self.assertEqual(mock_sock.sendto.call_count, 0)

        # 2. Invalid empty label -> rejected before socket is touched
        self.assertEqual(network.query_a_records("8.8.8.8", "example..com"), [])
        self.assertEqual(mock_sock.sendto.call_count, 0)

        # 3. Invalid port -> rejected before socket is touched
        self.assertEqual(network.query_a_records("8.8.8.8", "example.com", port=70000), [])
        self.assertEqual(network.query_a_records("8.8.8.8", "example.com", port=-1), [])
        self.assertEqual(mock_sock.sendto.call_count, 0)

        # 4. Valid IDNA domain -> converts to punycode and sends packet
        mock_sock.recvfrom.return_value = (b"\x00" * 12, ("8.8.8.8", 53))
        network.query_a_records("8.8.8.8", "пример.рф", retries=1)
        self.assertEqual(mock_sock.sendto.call_count, 1)
        pkt = mock_sock.sendto.call_args[0][0]
        self.assertIn(b"xn--e1afmkfd", pkt)
        self.assertIn(b"xn--p1ai", pkt)

    def test_parse_dns_response_corrupted_data(self):
        self.assertEqual(network._parse_dns_response(b"", 123), [])
        self.assertEqual(network._parse_dns_response(b"short", 123), [])
        self.assertEqual(network._parse_dns_response(b"\x00" * 12, 123), [])

    def test_parse_dns_response_ignores_bogons(self):
        import struct, socket
        qid = 1234
        header = struct.pack("!HHHHHH", qid, 0x8180, 1, 2, 0, 0)
        q = b"\x03foo\x03com\x00\x00\x01\x00\x01"
        ans_bogon = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 300, 4) + socket.inet_aton("0.0.0.0")
        ans_valid = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 300, 4) + socket.inet_aton("1.2.3.4")
        ips = network._parse_dns_response(header + q + ans_bogon + ans_valid, qid)
        self.assertEqual(ips, ["1.2.3.4"])

    def test_test_ip_empty_or_whitespace(self):
        ok, lat, detail = network.test_ip("", "example.com")
        self.assertFalse(ok)
        self.assertEqual(detail, "Некорректный IP")

        ok, lat, detail = network.test_ip("   ", "example.com")
        self.assertFalse(ok)
        self.assertEqual(detail, "Некорректный IP")

    @patch("core.network.test_ip")
    def test_test_ip_with_retries(self, mock_test_ip):
        mock_test_ip.side_effect = [(False, 0.0, "Timeout"), (True, 15.0, "HTTP/1.1 200 OK")]
        ok, lat, detail = network.test_ip_with_retries("1.2.3.4", "example.com", retries=2, delay=0.001)
        self.assertTrue(ok)
        self.assertEqual(lat, 15.0)

        mock_test_ip.side_effect = [(False, 0.0, "ConnRefused"), (False, 0.0, "ConnRefused")]
        ok, lat, detail = network.test_ip_with_retries("1.2.3.4", "example.com", retries=2, delay=0.001)
        self.assertFalse(ok)
        self.assertIn("Не ответил после 2 попыток", detail)

    @patch("core.system.get_active_entries")
    def test_check_and_heal_disabled(self, mock_get_active):
        mock_get_active.return_value = []
        class DummyConfig:
            domains = ["example.com"]
        st, msg = network.check_and_heal(DummyConfig())
        self.assertEqual(st, "DISABLED")

    @patch("core.network.test_ip_with_retries")
    @patch("core.system.get_active_entries")
    def test_check_and_heal_healthy(self, mock_get_active, mock_test):
        mock_get_active.return_value = [("1.2.3.4", "example.com")]
        mock_test.return_value = (True, 12.3, "HTTP/1.1 200 OK")
        class DummyConfig:
            domains = ["example.com"]
            check_port = 443
            check_timeout = 2.5
            check_retries = 2
            check_retry_delay = 0.01

        st, msg = network.check_and_heal(DummyConfig())
        self.assertEqual(st, "HEALTHY")
        self.assertIn("12.3 мс", msg)

    @patch("core.system.set_entries")
    @patch("core.network.rank_ips")
    @patch("core.network.get_candidate_ips")
    @patch("core.network.test_ip_with_retries")
    @patch("core.system.get_active_entries")
    def test_check_and_heal_failover_success(self, mock_get_active, mock_test, mock_cands, mock_rank, mock_set):
        mock_get_active.return_value = [("1.2.3.4", "example.com")]
        mock_test.return_value = (False, 0.0, "Offline")
        mock_cands.return_value = (["5.6.7.8"], "DNS OK")
        mock_rank.return_value = [("5.6.7.8", 14.5)]
        mock_set.return_value = (True, "Hosts updated")

        class DummyConfig:
            domains = ["example.com"]
            check_port = 443
            check_timeout = 2.5
            check_retries = 2
            check_retry_delay = 0.01

        st, msg = network.check_and_heal(DummyConfig(), quiet=True)
        self.assertEqual(st, "HEALED")
        self.assertIn("Переключено с 1.2.3.4 на 5.6.7.8", msg)
        mock_set.assert_called_once_with([("5.6.7.8", "example.com")])

    @patch("core.network.rank_ips")
    @patch("core.network.get_candidate_ips")
    @patch("core.network.test_ip_with_retries")
    @patch("core.system.get_active_entries")
    def test_check_and_heal_all_failed(self, mock_get_active, mock_test, mock_cands, mock_rank):
        mock_get_active.return_value = [("1.2.3.4", "example.com")]
        mock_test.return_value = (False, 0.0, "Offline")
        mock_cands.return_value = (["5.6.7.8"], "DNS OK")
        mock_rank.return_value = []

        class DummyConfig:
            domains = ["example.com"]
            check_port = 443
            check_timeout = 2.5
            check_retries = 2
            check_retry_delay = 0.01

        st, msg = network.check_and_heal(DummyConfig(), quiet=True)
        self.assertEqual(st, "FAILED")
        self.assertIn("Ни один из найденных узлов", msg)

    def test_test_ip_bogon_and_invalid(self):
        ok, lat, detail = network.test_ip("0.0.0.0", "example.com")
        self.assertFalse(ok)
        self.assertEqual(detail, "Некорректный IP")

        ok, lat, detail = network.test_ip("255.255.255.255", "example.com")
        self.assertFalse(ok)
        self.assertEqual(detail, "Некорректный IP")

        ok, lat, detail = network.test_ip("::", "example.com")
        self.assertFalse(ok)
        self.assertEqual(detail, "Некорректный IP")

        ok, lat, detail = network.test_ip("1.2.3.4", "")
        self.assertFalse(ok)
        self.assertEqual(detail, "Некорректный домен")

        ok, lat, detail = network.test_ip("1.2.3.4", "example.com\r\nHost: evil")
        self.assertFalse(ok)
        self.assertEqual(detail, "Некорректный домен")

        ok, lat, detail = network.test_ip("1.2.3.4", "example.com", port=70000)
        self.assertFalse(ok)
        self.assertEqual(detail, "Некорректный порт")

        ok, lat, detail = network.test_ip("1.2.3.4", "example.com", port=0)
        self.assertFalse(ok)
        self.assertEqual(detail, "Некорректный порт")

    def test_resolve_target_bogon(self):
        self.assertIsNone(network.resolve_target("0.0.0.0"))
        self.assertIsNone(network.resolve_target("255.255.255.255"))
        self.assertIsNone(network.resolve_target("0.1.2.3"))

    @patch("core.network.query_all_dot_servers")
    @patch("core.network.query_all_doh_servers")
    @patch("core.network.query_all_dns_servers")
    def test_network_airgap_dns_failure(self, mock_query_all, mock_doh, mock_dot):
        # Simulate airgapped environment where all DNS servers fail
        mock_query_all.return_value = [
            {"server": "1.1.1.1", "dns_ip": "1.1.1.1", "ips": [], "error": "DNS 1.1.1.1 не вернул A-записей"},
            {"server": "8.8.8.8", "dns_ip": "8.8.8.8", "ips": [], "error": "DNS 8.8.8.8 не вернул A-записей"},
        ]
        mock_doh.return_value = [
            {"server": "https://1.1.1.1/dns-query", "ips": [], "error": "timeout", "doh": True}
        ]
        mock_dot.return_value = [
            {"server": "tls://1.1.1.1", "ips": [], "error": "timeout", "dot": True}
        ]
        class DummyConfig:
            smart_dns_servers = ["1.1.1.1", "8.8.8.8"]
            nodes = ["192.168.1.100"]

        cands, res = network.get_candidate_ips(DummyConfig(), "example.com")
        self.assertEqual(cands, ["192.168.1.100"])
        self.assertIn("все DNS/DoT/DoH не ответили", str(res))


class TestCorePackage(unittest.TestCase):
    def test_core_exports_and_no_legacy_aliases(self):
        self.assertTrue(hasattr(core, "Config"))
        self.assertTrue(hasattr(core, "network"))
        self.assertTrue(hasattr(core, "system"))
        self.assertFalse(hasattr(core, "resolver"))
        self.assertFalse(hasattr(core, "checker"))
        self.assertFalse(hasattr(core, "monitor"))
        self.assertFalse(hasattr(core, "hosts"))
        self.assertFalse(hasattr(core, "endpoint"))
        self.assertFalse(hasattr(core, "patcher"))


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cli_status(self):
        import subprocess
        import sys
        ag_bin = Path(__file__).resolve().parent.parent / "ag"
        sub_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        res = subprocess.run([sys.executable, str(ag_bin), "status"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=sub_env)
        self.assertEqual(res.returncode, 0)
        self.assertIn("Antigravity Bypass Status", res.stdout)
        self.assertIn("Конфигурация:", res.stdout)

    def test_cli_config_arg_before_and_after(self):
        import subprocess
        import sys
        ag_bin = Path(__file__).resolve().parent.parent / "ag"
        sub_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        res1 = subprocess.run([sys.executable, str(ag_bin), "-c", "/nonexistent.json", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=sub_env)
        self.assertEqual(res1.returncode, 1)
        self.assertIn("Файл конфигурации не найден", res1.stdout + res1.stderr)

        res2 = subprocess.run([sys.executable, str(ag_bin), "status", "-c", "/nonexistent.json"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=sub_env)
        self.assertEqual(res2.returncode, 1)
        self.assertIn("Файл конфигурации не найден", res2.stdout + res2.stderr)

    def test_cli_on_validation_failure_when_empty(self):
        import subprocess
        import sys
        ag_bin = Path(__file__).resolve().parent.parent / "ag"
        empty_cfg = self.temp_path / "empty.json"
        empty_cfg.write_text("{}", encoding="utf-8")
        sub_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        res = subprocess.run([sys.executable, str(ag_bin), "-c", str(empty_cfg), "on"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=sub_env)
        self.assertEqual(res.returncode, 1)
        self.assertIn("Ошибка в конфигурации", res.stdout)

    @patch("ssl.create_default_context")
    @patch("socket.create_connection")
    def test_test_ip_filters_proxy_errors(self, mock_conn, mock_ssl):
        mock_raw = mock_conn.return_value.__enter__.return_value
        mock_sock = mock_ssl.return_value.wrap_socket.return_value.__enter__.return_value

        # 502 Bad Gateway -> rejected
        mock_sock.recv.return_value = b"HTTP/1.1 502 Bad Gateway\r\nServer: nginx\r\n\r\n"
        ok, lat, detail = network.test_ip("1.2.3.4", "example.com")
        self.assertFalse(ok)
        self.assertIn("Ошибка прокси: HTTP/1.1 502 Bad Gateway", detail)

        # 503 Service Unavailable -> rejected
        mock_sock.recv.return_value = b"HTTP/1.1 503 Service Unavailable\r\n\r\n"
        ok, lat, detail = network.test_ip("1.2.3.4", "example.com")
        self.assertFalse(ok)
        self.assertIn("Ошибка прокси", detail)

        # 403 Forbidden (Cloudflare block) -> rejected
        mock_sock.recv.return_value = b"HTTP/1.1 403 Forbidden\r\nServer: cloudflare\r\n\r\n"
        ok, lat, detail = network.test_ip("1.2.3.4", "example.com")
        self.assertFalse(ok)
        self.assertIn("Ошибка прокси", detail)

        # 200 OK -> accepted
        mock_sock.recv.return_value = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
        ok, lat, detail = network.test_ip("1.2.3.4", "example.com")
        self.assertTrue(ok)
        self.assertEqual(detail, "HTTP/1.1 200 OK")

        # 404 Not Found -> accepted (Google service response)
        mock_sock.recv.return_value = b"HTTP/1.1 404 Not Found\r\nServer: gws\r\n\r\n"
        ok, lat, detail = network.test_ip("1.2.3.4", "example.com")
        self.assertTrue(ok)
        self.assertEqual(detail, "HTTP/1.1 404 Not Found")

    @patch("urllib.request.urlopen")
    def test_query_doh_server_parsing(self, mock_urlopen):
        import io
        import json
        payload = {
            "Status": 0,
            "Answer": [
                {"name": "example.com", "type": 1, "data": "10.0.0.1"},
                {"name": "example.com", "type": 5, "data": "cname.com"},
                {"name": "example.com", "type": 1, "data": "10.0.0.2"},
                {"name": "example.com", "type": 1, "data": "0.0.0.0"},  # Bogon ignored
            ]
        }
        mock_resp = io.BytesIO(json.dumps(payload).encode("utf-8"))
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        ips = network.query_doh_server("https://1.1.1.1/dns-query", "example.com")
        self.assertEqual(ips, ["10.0.0.1", "10.0.0.2"])

    def test_query_doh_empty_args(self):
        self.assertEqual(network.query_doh_server("", "example.com"), [])
        self.assertEqual(network.query_doh_server("https://1.1.1.1/dns-query", ""), [])

    @patch("urllib.request.urlopen", side_effect=Exception("Network error"))
    def test_query_doh_network_error(self, mock_urlopen):
        self.assertEqual(network.query_doh_server("https://1.1.1.1/dns-query", "example.com"), [])

    @patch("core.network.query_all_dot_servers")
    @patch("core.network.query_all_doh_servers")
    @patch("core.network.query_all_dns_servers")
    def test_get_candidate_ips_doh_fallback_success(self, mock_dns, mock_doh, mock_dot):
        mock_dns.return_value = [{"server": "1.2.3.4", "dns_ip": "1.2.3.4", "ips": [], "error": "timeout"}]
        mock_dot.return_value = []
        mock_doh.return_value = [{"server": "https://1.1.1.1/dns-query", "dns_ip": "https://1.1.1.1/dns-query", "ips": ["159.194.200.1"], "error": None, "doh": True}]

        class DummyConfig:
            smart_dns_servers = ["1.2.3.4"]
            nodes = []

        cands, res = network.get_candidate_ips(DummyConfig(), "example.com")
        self.assertEqual(cands, ["159.194.200.1"])
        self.assertIn("SmartDNS (DoH)", str(res))

    @patch("core.system.set_entries")
    @patch("core.network.rank_ips")
    @patch("core.network.get_candidate_ips")
    @patch("core.network.test_ip_with_retries")
    @patch("core.system.get_active_entries")
    def test_check_and_heal_multi_node(self, mock_get_active, mock_test, mock_cands, mock_rank, mock_set):
        mock_get_active.return_value = [("1.2.3.4", "example.com")]
        mock_test.return_value = (False, 0.0, "Offline")
        mock_cands.return_value = (["5.6.7.8", "5.6.7.9"], "DNS OK")
        mock_rank.return_value = [("5.6.7.8", 14.5), ("5.6.7.9", 20.1)]
        mock_set.return_value = (True, "Hosts updated")

        class DummyConfig:
            domains = ["example.com"]
            check_port = 443
            check_timeout = 2.5
            check_retries = 2
            check_retry_delay = 0.01
            max_hosts_ips = 2

        st, msg = network.check_and_heal(DummyConfig(), quiet=True)
        self.assertEqual(st, "HEALED")
        self.assertIn("Переключено с 1.2.3.4 на 5.6.7.8, 5.6.7.9", msg)
        mock_set.assert_called_once_with([("5.6.7.8", "example.com"), ("5.6.7.9", "example.com")])

    def test_config_max_hosts_ips_and_doh(self):
        cfg_file = self.temp_path / "extra_cfg.json"
        cfg_file.write_text(json.dumps({"max_hosts_ips": 3, "doh_servers": ["https://custom.doh/query"]}), encoding="utf-8")
        cfg = Config(cfg_file)
        self.assertEqual(cfg.max_hosts_ips, 3)
        self.assertEqual(cfg.doh_servers, ["https://custom.doh/query"])

    def test_config_dot_servers_and_validation(self):
        cfg_file = self.temp_path / "dot_cfg.json"
        cfg_file.write_text(
            json.dumps({
                "dot_servers": ["tls://dns.geohide.ru", "1.1.1.1#cloudflare-dns.com"],
                "smart_dns": {"dot_port": 8853},
            }),
            encoding="utf-8",
        )
        cfg = Config(cfg_file)
        self.assertEqual(cfg.dot_servers, ["tls://dns.geohide.ru", "1.1.1.1#cloudflare-dns.com"])
        self.assertEqual(cfg.smart_dns_dot_port, 8853)
        self.assertEqual(cfg.validate(), [])

    def test_parse_dot_target_variants(self):
        self.assertEqual(network._parse_dot_target("tls://dns.google"), ("dns.google", 853, "dns.google"))
        self.assertEqual(
            network._parse_dot_target("tls://1.1.1.1:8853#cloudflare-dns.com"),
            ("1.1.1.1", 8853, "cloudflare-dns.com"),
        )
        self.assertEqual(network._parse_dot_target("9.9.9.9"), ("9.9.9.9", 853, "9.9.9.9"))
        self.assertIsNone(network._parse_dot_target("https://1.1.1.1/dns-query"))
        self.assertIsNone(network._parse_dot_target("tls://bad:99999"))

    @patch("urllib.request.urlopen")
    def test_query_doh_server_rfc8484_wire_fallback(self, mock_urlopen):
        import io, socket, struct, urllib.error

        dns_wire = (
            struct.pack("!HHHHHH", 0, 0x8180, 1, 1, 0, 0)
            + b"\x07example\x03com\x00\x00\x01\x00\x01"
            + b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton("203.0.113.55")
        )
        mock_resp = io.BytesIO(dns_wire)
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_resp
        mock_urlopen.side_effect = [
            urllib.error.HTTPError("https://doh.example/dns-query", 415, "Unsupported Media Type", {}, None),
            mock_cm,
        ]

        ips = network.query_doh_server("https://doh.example/dns-query", "example.com")
        self.assertEqual(ips, ["203.0.113.55"])
        self.assertEqual(mock_urlopen.call_count, 2)
        second_req = mock_urlopen.call_args_list[1].args[0]
        self.assertEqual(second_req.get_method(), "POST")
        self.assertEqual(second_req.get_header("Content-type"), "application/dns-message")

    def test_query_dot_server_tls_framing(self):
        import socket, struct

        sent_frames = []

        class FakeTLSSock:
            def __init__(self):
                self._buf = b""
                self._off = 0

            def settimeout(self, t):
                pass

            def sendall(self, data):
                sent_frames.append(data)
                msg_len = struct.unpack("!H", data[:2])[0]
                pkt = data[2 : 2 + msg_len]
                qid = struct.unpack("!H", pkt[:2])[0]
                resp = (
                    struct.pack("!HHHHHH", qid, 0x8180, 1, 1, 0, 0)
                    + b"\x07example\x03com\x00\x00\x01\x00\x01"
                    + b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton("198.51.100.77")
                )
                self._buf = struct.pack("!H", len(resp)) + resp

            def recv(self, n):
                chunk = self._buf[self._off : self._off + min(n, 3)]
                self._off += len(chunk)
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        fake_ctx = MagicMock()
        fake_ctx.wrap_socket.return_value = FakeTLSSock()
        fake_raw = MagicMock()
        fake_raw.__enter__.return_value = fake_raw

        with patch("ssl.create_default_context", return_value=fake_ctx), \
             patch("socket.create_connection", return_value=fake_raw) as mock_conn:
            ips = network.query_dot_server("tls://1.1.1.1:853#cloudflare-dns.com", "example.com")

        self.assertEqual(ips, ["198.51.100.77"])
        mock_conn.assert_called_once_with(("1.1.1.1", 853), timeout=3.0)
        fake_ctx.wrap_socket.assert_called_once_with(fake_raw, server_hostname="cloudflare-dns.com")
        self.assertEqual(len(sent_frames[0]) - 2, struct.unpack("!H", sent_frames[0][:2])[0])

    @patch("core.network.query_all_dot_servers")
    @patch("core.network.query_all_doh_servers")
    @patch("core.network.query_all_dns_servers")
    def test_get_candidate_ips_routes_udp_dot_doh_and_dot_fallback(self, mock_dns, mock_doh, mock_dot):
        mock_dns.return_value = [{"server": "8.8.8.8", "dns_ip": "8.8.8.8", "ips": [], "error": "timeout"}]
        mock_doh.return_value = [{"server": "https://dns.google/resolve", "dns_ip": "https://dns.google/resolve", "ips": ["1.2.3.4"], "error": None, "doh": True}]
        mock_dot.return_value = [{"server": "tls://1.1.1.1", "dns_ip": "1.1.1.1", "ips": ["5.6.7.8"], "error": None, "dot": True}]

        class MixedConfig:
            smart_dns_servers = ["8.8.8.8", "tls://1.1.1.1", "https://dns.google/resolve"]
            dot_servers = []
            doh_servers = []
            nodes = []

        cands, res = network.get_candidate_ips(MixedConfig(), "example.com")
        mock_dns.assert_called_once()
        self.assertEqual(mock_dns.call_args.args[0], ["8.8.8.8"])
        mock_dot.assert_called_once()
        self.assertEqual(mock_dot.call_args.args[0], ["tls://1.1.1.1"])
        mock_doh.assert_called_once()
        self.assertEqual(mock_doh.call_args.args[0], ["https://dns.google/resolve"])
        self.assertEqual(cands, ["5.6.7.8", "1.2.3.4"])

        # Now verify bare domain auto-expands to DoT (tls://host) + DoH (https://host/dns-query),
        # while bare IP does not, and explicit custom DoH path suppresses /dns-query duplicate
        mock_dns.reset_mock()
        mock_doh.reset_mock()
        mock_dot.reset_mock()
        mock_dns.return_value = [{"server": "dns.geohide.ru", "dns_ip": "1.2.3.4", "ips": [], "error": "timeout"}]
        mock_dot.return_value = [{"server": "tls://dns.geohide.ru", "dns_ip": "1.2.3.4", "ips": ["7.7.7.7"], "error": None, "dot": True}]
        mock_doh.return_value = [{"server": "https://dns.geohide.ru/dns-query", "dns_ip": "https://dns.geohide.ru/dns-query", "ips": ["8.8.4.4"], "error": None, "doh": True}]

        class DomainAutoConfig:
            smart_dns_servers = ["dns.geohide.ru", "45.155.204.190", "dns.google", "https://dns.google/resolve"]
            dot_servers = []
            doh_servers = []
            nodes = []

        cands_dom, _ = network.get_candidate_ips(DomainAutoConfig(), "example.com")
        self.assertEqual(mock_dns.call_args.args[0], ["dns.geohide.ru", "45.155.204.190", "dns.google"])
        self.assertEqual(mock_dot.call_args.args[0], ["tls://dns.geohide.ru", "tls://dns.google"])
        self.assertEqual(
            mock_doh.call_args.args[0],
            ["https://dns.google/resolve", "https://dns.geohide.ru/dns-query"],
        )
        self.assertEqual(cands_dom, ["7.7.7.7", "8.8.4.4"])

        # Now verify DoT fallback when UDP and DoH fail
        mock_dns.reset_mock()
        mock_doh.reset_mock()
        mock_dot.reset_mock()
        mock_dns.return_value = [{"server": "1.2.3.4", "dns_ip": "1.2.3.4", "ips": [], "error": "timeout"}]
        mock_doh.return_value = [{"server": "https://1.1.1.1/dns-query", "dns_ip": "https://1.1.1.1/dns-query", "ips": [], "error": "fail", "doh": True}]
        mock_dot.return_value = [{"server": "tls://1.1.1.1", "dns_ip": "1.1.1.1", "ips": ["9.9.9.9"], "error": None, "dot": True}]

        class FallbackConfig:
            smart_dns_servers = ["1.2.3.4"]
            dot_servers = []
            doh_servers = []
            nodes = []

        cands2, res2 = network.get_candidate_ips(FallbackConfig(), "example.com")
        self.assertEqual(cands2, ["9.9.9.9"])
        self.assertIn("SmartDNS (DoT)", str(res2))

    def test_cli_print_dns_results_dot_tag(self):
        import ag as ag_cli
        import sys
        from io import StringIO
        buf = StringIO()
        res_info = network.ResolutionResult(
            "ok",
            [{"server": "tls://1.1.1.1", "dns_ip": "1.1.1.1", "ips": ["1.2.3.4"], "error": None, "dot": True}],
            ["1.2.3.4"],
            [],
        )
        with patch.object(sys, "stdout", buf):
            ag_cli._print_dns_results(res_info)
        self.assertIn("DoT tls://1.1.1.1 (1.1.1.1)", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

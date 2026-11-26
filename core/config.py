import json
import os
try:
    import pwd
except ImportError:
    pwd = None
from pathlib import Path


def find_config(path: str | Path | None = None) -> Path:
    if path:
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Файл конфигурации не найден: {p}")
        return p

    project_cfg = Path(__file__).resolve().parent.parent / "config.json"
    candidates = []
    if env := os.environ.get("AG_CONFIG"):
        candidates.append(Path(env).expanduser().resolve())
    candidates.append(Path.cwd() / "config.json")

    home = Path.home()
    if sudo_user := os.environ.get("SUDO_USER"):
        if pwd:
            try:
                home = Path(pwd.getpwnam(sudo_user).pw_dir)
            except Exception:
                pass
    candidates.extend([home / ".config/ag/config.json", Path("/etc/ag/config.json"), project_cfg])

    for c in candidates:
        if c.is_file():
            return c
    return project_cfg


def _parse_int(val, default: int, min_val: int = 1, max_val: int | None = None) -> int:
    try:
        n = int(val or default)
    except (ValueError, TypeError):
        return default
    if n < min_val or (max_val is not None and n > max_val):
        return default
    return n


def _parse_float(val, default: float, allow_zero: bool = False) -> float:
    try:
        n = float(val if val not in (None, "") else default)
    except (ValueError, TypeError):
        return default
    if (n >= 0) if allow_zero else (n > 0):
        return n
    return default


class Config:
    def __init__(self, config_path: str | Path | None = None):
        self.path = find_config(config_path)
        self.exists = self.path.is_file()
        cfg = {}
        if self.exists:
            try:
                cfg = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(cfg, dict):
                    raise ValueError(f"Конфигурация в '{self.path}' должна быть JSON-объектом (dict)")
            except json.JSONDecodeError as e:
                raise ValueError(f"Ошибка синтаксиса JSON в '{self.path}': {e}") from e

        dns = cfg.get("smart_dns")
        dns_dict = dns if isinstance(dns, dict) else {}
        hc = cfg.get("healthcheck") if isinstance(cfg.get("healthcheck"), dict) else {}

        # DNS-серверы
        servers = []
        if isinstance(dns, list):
            servers.extend(dns)
        elif isinstance(dns, dict):
            for k in ("servers", "hosts", "host", "fallback_ip"):
                val = dns.get(k)
                servers.extend(val if isinstance(val, list) else [val] if val else [])
        elif isinstance(dns, str):
            servers.append(dns)
        self.smart_dns_servers = list(dict.fromkeys(str(s).strip() for s in servers if str(s).strip()))
        doh = cfg.get("doh_servers") or dns_dict.get("doh_servers")
        if isinstance(doh, list):
            self.doh_servers = [str(x).strip() for x in doh if str(x).strip()]
        elif isinstance(doh, str) and doh.strip():
            self.doh_servers = [doh.strip()]
        else:
            self.doh_servers = []
        self.smart_dns_port = _parse_int(dns_dict.get("port"), 53, 1, 65535)
        self.smart_dns_timeout = _parse_float(dns_dict.get("timeout_seconds"), 3.0)

        # Статические узлы
        nodes = cfg.get("nodes")
        if isinstance(nodes, str):
            nodes = [nodes]
        elif not isinstance(nodes, list):
            nodes = []
        self.nodes = [str(x).strip() for x in nodes if str(x).strip()]

        # Домены и эндпоинт
        doms = cfg.get("domains")
        if doms is None:
            doms = ["daily-cloudcode-pa.googleapis.com"]
        elif isinstance(doms, str):
            doms = [doms]
        elif not isinstance(doms, list):
            doms = []
        self.domains = [str(d).strip() for d in doms if str(d).strip()]
        default_ep = f"https://{self.domains[0]}" if self.domains else ""
        self.endpoint_url = str(cfg.get("endpoint_url") or default_ep).strip()
        self.binary_path = str(cfg.get("binary_path") or "auto").strip()

        # Healthcheck
        self.max_hosts_ips = _parse_int(cfg.get("max_hosts_ips") or hc.get("max_hosts_ips"), 2)
        self.check_port = _parse_int(hc.get("probe_port"), 443, 1, 65535)
        self.check_timeout = _parse_float(hc.get("connect_timeout_seconds"), 2.5)
        self.check_interval_minutes = _parse_int(hc.get("interval_minutes"), 2)
        self.check_retries = _parse_int(hc.get("retries"), 3)
        self.check_retry_delay = _parse_float(hc.get("retry_delay_seconds"), 1.0, allow_zero=True)

    def validate(self) -> list[str]:
        errs = []
        if not self.exists:
            errs.append(f"Файл конфигурации не найден: {self.path}")
        if not self.smart_dns_servers and not self.nodes:
            errs.append("Не настроены узлы или DNS: укажите 'smart_dns.servers' или 'nodes' в config.json")
        if not self.domains:
            errs.append("Список доменов (domains) пуст")
        return errs

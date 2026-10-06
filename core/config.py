import json
from pathlib import Path


class Config:
    def __init__(self, config_path: Path | str | None = None):
        self.path = Path(config_path) if config_path else Path(__file__).resolve().parent.parent / "config.json"
        cfg = {}
        if self.path.is_file():
            try:
                cfg = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        self.data = cfg
        dns, hc = cfg.get("smart_dns") or {}, cfg.get("healthcheck") or {}
        self.smart_dns_host = str(dns.get("host") or "dns.geohide.ru")
        self.smart_dns_fallback_ip = str(dns.get("fallback_ip") or "45.155.204.190")
        self.smart_dns_port = int(dns.get("port") or 53)
        self.smart_dns_timeout = float(dns.get("timeout_seconds") or 3.0)
        raw = cfg.get("domains")
        self.domains = list(raw) if isinstance(raw, list) and raw else ["daily-cloudcode-pa.googleapis.com"]
        self.binary_path = str(cfg.get("binary_path") or "auto")
        self.check_port = int(hc.get("probe_port") or 443)
        self.check_timeout = float(hc.get("connect_timeout_seconds") or 2.5)
        self.check_interval_minutes = int(hc.get("interval_minutes") or 15)
        self.check_retries = int(hc.get("retries") or 3)
        self.check_retry_delay = float(hc.get("retry_delay_seconds") or 1.0)

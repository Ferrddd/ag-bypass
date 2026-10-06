import json
import os
from pathlib import Path

DEFAULT_CONFIG = {
    "smart_dns": {
        "host": "dns.geohide.ru",
        "fallback_ip": "45.155.204.190",
        "port": 53,
        "timeout_seconds": 3.0,
    },
    "domains": [
        "daily-cloudcode-pa.googleapis.com"
    ],
    "binary_path": "auto",
    "healthcheck": {
        "interval_minutes": 15,
        "connect_timeout_seconds": 2.5,
        "probe_port": 443,
        "retries": 3,
        "retry_delay_seconds": 1.0,
    }
}


class Config:
    def __init__(self, config_path: Path | None = None):
        if config_path is None:
            # Look in project root directory (parent of core/)
            root_dir = Path(__file__).resolve().parent.parent
            config_path = root_dir / "config.json"
        
        self.path = config_path
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.is_file():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    user_data = json.load(f)
                # Merge with defaults
                merged = DEFAULT_CONFIG.copy()
                for k, v in user_data.items():
                    if isinstance(v, dict) and k in merged and isinstance(merged[k], dict):
                        merged[k] = {**merged[k], **v}
                    else:
                        merged[k] = v
                return merged
            except Exception as e:
                print(f"[WARN] Ошибка чтения config.json ({e}), используются настройки по умолчанию.")
        return DEFAULT_CONFIG.copy()

    @property
    def smart_dns_host(self) -> str:
        return self.data.get("smart_dns", {}).get("host", "dns.geohide.ru")

    @property
    def smart_dns_fallback_ip(self) -> str:
        return self.data.get("smart_dns", {}).get("fallback_ip", "45.155.204.190")

    @property
    def smart_dns_port(self) -> int:
        return int(self.data.get("smart_dns", {}).get("port", 53))

    @property
    def smart_dns_timeout(self) -> float:
        return float(self.data.get("smart_dns", {}).get("timeout_seconds", 3.0))

    @property
    def domains(self) -> list[str]:
        return self.data.get("domains", ["daily-cloudcode-pa.googleapis.com"])

    @property
    def binary_path(self) -> str:
        return self.data.get("binary_path", "auto")

    @property
    def check_timeout(self) -> float:
        return float(self.data.get("healthcheck", {}).get("connect_timeout_seconds", 2.5))

    @property
    def check_port(self) -> int:
        return int(self.data.get("healthcheck", {}).get("probe_port", 443))

    @property
    def check_interval_minutes(self) -> int:
        return int(self.data.get("healthcheck", {}).get("interval_minutes", 15))

    @property
    def check_retries(self) -> int:
        return int(self.data.get("healthcheck", {}).get("retries", 3))

    @property
    def check_retry_delay(self) -> float:
        return float(self.data.get("healthcheck", {}).get("retry_delay_seconds", 1.0))

    @property
    def state_file(self) -> Path:
        system_path = Path("/var/lib/ag-bypass/state.json")
        if os.geteuid() == 0:
            try:
                system_path.parent.mkdir(parents=True, exist_ok=True)
                return system_path
            except Exception:
                pass
        elif system_path.is_file():
            return system_path

        user_home = Path.home()
        if "SUDO_USER" in os.environ:
            try:
                import pwd
                user_home = Path(pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir)
            except Exception:
                pass
        state_dir = user_home / ".config" / "ag-bypass"
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return state_dir / "state.json"

import os
import json
import time
from pathlib import Path
from core.config import Config
from core import resolver
from core import checker
from core import hosts


def load_state(config: Config) -> dict:
    try:
        p = config.state_file
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_state(config: Config, data: dict):
    try:
        p = config.state_file
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(p, 0o644)
        except Exception:
            pass
    except Exception:
        pass


def check_and_heal(
    config: Config, interactive: bool = True, quiet: bool = False
) -> tuple[str, str]:
    active_entries = hosts.get_active_entries()
    if not active_entries:
        return "DISABLED", "Обход не включен в /etc/hosts"

    primary_domain = config.domains[0]
    # Find active IP for primary domain
    primary_ip = None
    for ip, domain in active_entries:
        if domain == primary_domain:
            primary_ip = ip
            break

    if not primary_ip:
        primary_ip = active_entries[0][0]

    # Test current primary IP with retries
    alive, latency, detail = checker.test_ip_with_retries(
        primary_ip,
        primary_domain,
        port=config.check_port,
        timeout=config.check_timeout,
        retries=config.check_retries,
        delay=config.check_retry_delay,
    )
    if alive:
        state = load_state(config)
        state["last_checked"] = time.time()
        state["last_status"] = "HEALTHY"
        state["last_ip"] = primary_ip
        state["latency_ms"] = latency
        save_state(config, state)
        return "HEALTHY", f"Текущий узел {primary_ip} доступен (задержка {latency:.1f} мс)"

    if not quiet:
        print(f"[!] Текущий узел {primary_ip} перестал отвечать ({detail}).")
        print("[*] Поиск нового рабочего сервера через SmartDNS...")

    dns_ip = resolver.resolve_dns_server(
        config.smart_dns_host, fallback_ip=config.smart_dns_fallback_ip
    )
    candidates = resolver.query_a_records(
        dns_ip, primary_domain, port=config.smart_dns_port, timeout=config.smart_dns_timeout
    )

    if not candidates:
        # Fallback to configured fallback IPs if DNS server returned empty
        candidates = ["193.233.112.68", "193.233.112.88", "159.194.200.33"]

    ranked = checker.rank_ips(
        candidates, primary_domain, port=config.check_port, timeout=config.check_timeout
    )
    if not ranked:
        return "FAILED", "Ни один из найденных серверов не прошёл TLS-проверку"

    new_best_ip, new_latency = ranked[0]

    if new_best_ip == primary_ip:
        # The primary IP suddenly answered or flapped
        return "HEALTHY", f"Узел {primary_ip} снова отвечает (задержка {new_latency:.1f} мс)"

    if interactive and not quiet:
        ans = input(
            f"[?] IP сервера изменился с {primary_ip} на {new_best_ip}. Применить обновление? [Y/n]: "
        ).strip().lower()
        if ans in ("n", "no"):
            return "SKIPPED", f"Обновление отклонено пользователем (оставлен {primary_ip})"

    # Prepare new hosts entries: single best IP per domain
    new_entries: list[tuple[str, str]] = [(new_best_ip, domain) for domain in config.domains]

    ok, msg = hosts.set_entries(new_entries)
    if not ok:
        return "ERROR", f"Не удалось обновить /etc/hosts: {msg}"

    state = load_state(config)
    state["last_checked"] = time.time()
    state["last_status"] = "HEALED"
    state["last_ip"] = new_best_ip
    state["previous_ip"] = primary_ip
    state["latency_ms"] = new_latency
    save_state(config, state)

    return "HEALED", f"Выполнено переключение с {primary_ip} на {new_best_ip} ({new_latency:.1f} мс)"

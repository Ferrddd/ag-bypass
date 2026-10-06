from core.config import Config
from core import resolver, checker, hosts


def check_and_heal(config: Config, interactive: bool = True, quiet: bool = False) -> tuple[str, str]:
    entries = hosts.get_active_entries()
    if not entries:
        return "DISABLED", "Обход не включен в /etc/hosts"

    domain = config.domains[0]
    cur_ip = next((ip for ip, d in entries if d == domain), entries[0][0])
    alive, lat, detail = checker.test_ip_with_retries(
        cur_ip, domain, port=config.check_port, timeout=config.check_timeout,
        retries=config.check_retries, delay=config.check_retry_delay
    )
    if alive:
        return "HEALTHY", f"Текущий узел {cur_ip} доступен (задержка {lat:.1f} мс)"

    if not quiet:
        print(f"[!] Узел {cur_ip} не отвечает ({detail}). Поиск нового узла...")

    dns_ip = resolver.resolve_dns_server(config.smart_dns_host, fallback_ip=config.smart_dns_fallback_ip)
    candidates = resolver.query_a_records(dns_ip, domain, port=config.smart_dns_port, timeout=config.smart_dns_timeout) or [
        "193.233.112.68", "193.233.112.88", "159.194.200.33"
    ]
    ranked = checker.rank_ips(candidates, domain, port=config.check_port, timeout=config.check_timeout)
    if not ranked:
        return "FAILED", "Ни один из серверов не прошёл TLS-проверку"

    new_ip, new_lat = ranked[0]
    if new_ip == cur_ip:
        return "HEALTHY", f"Узел {cur_ip} снова отвечает (задержка {new_lat:.1f} мс)"

    if interactive and not quiet and input(f"[?] Сменить сервер с {cur_ip} на {new_ip}? [Y/n]: ").strip().lower() in ("n", "no"):
        return "SKIPPED", f"Обновление отклонено (оставлен {cur_ip})"

    ok, msg = hosts.set_entries([(new_ip, d) for d in config.domains])
    return ("HEALED", f"Переключено с {cur_ip} на {new_ip} ({new_lat:.1f} мс)") if ok else ("ERROR", f"Ошибка записи в /etc/hosts: {msg}")

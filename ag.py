#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import Config
from core import checker, endpoint, hosts, monitor, patcher, resolver, scheduler

# ANSI colors
C_CYAN, C_GREEN, C_YELLOW, C_RED, C_BOLD, C_RESET = (
    "\033[36m", "\033[32m", "\033[33m", "\033[31m", "\033[1m", "\033[0m"
)


def _init_windows_console():
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            mode = ctypes.c_ulong()
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass


def is_admin() -> bool:
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _req_admin(action: str):
    if not is_admin():
        print(f"{C_RED}[!] Ошибка: Для команды '{action}' требуются права Администратора.{C_RESET}")
        print(f"    Запустите Терминал / PowerShell / CMD {C_BOLD}от имени Администратора{C_RESET} и повторите команду.")
        sys.exit(1)


def cmd_on(args, cfg: Config):
    _req_admin("on")
    print(f"\n{C_CYAN}{C_BOLD}=== Активация Antigravity Windows Bypass ==={C_RESET}\n")

    # 1. Binary check & patch
    print(f"{C_BOLD}[1/5] Клиент Antigravity...{C_RESET}")
    if bp := patcher.find_binary(cfg.binary_path):
        st = patcher.get_status(bp)
        if st == "READY_TO_PATCH":
            ok, msg = patcher.apply_patch(bp)
            print(f"      {C_GREEN if ok else C_RED}{msg}{C_RESET}")
        else:
            col = C_GREEN if st == "ALREADY_PATCHED" else C_YELLOW
            print(f"      Файл: {bp} (статус: {col}{st}{C_RESET})")
        if patcher.is_binary_running(bp):
            print(f"      {C_YELLOW}[WARN] Процесс {bp.name} запущен. Рекомендуется перезапустить клиент.{C_RESET}")
    else:
        print(f"      {C_YELLOW}[WARN] Бинарник agy.exe не найден в стандартных путях. Вы можете указать путь в config.json.{C_RESET}")

    # 2. Endpoint
    print(f"{C_BOLD}[2/5] Настройка CLOUD_CODE_URL...{C_RESET}")
    applied = endpoint.apply_endpoint()
    if applied:
        print(f"      {C_GREEN}[✓] Применено в: {', '.join(applied)}{C_RESET}")
    else:
        print(f"      {C_YELLOW}[WARN] Не удалось записать переменную окружения.{C_RESET}")

    # 3. DNS resolve & query
    print(f"{C_BOLD}[3/5] Запрос IP через SmartDNS...{C_RESET}")
    dom = cfg.domains[0]
    dns_ip = resolver.resolve_dns_server(cfg.smart_dns_host, cfg.smart_dns_fallback_ip)
    cands = resolver.query_a_records(dns_ip, dom, port=cfg.smart_dns_port, timeout=cfg.smart_dns_timeout) or [
        "159.194.200.33", "193.233.112.67", "193.233.112.68", "193.233.112.88"
    ]
    print(f"      DNS {dns_ip} вернул: {', '.join(cands)}")

    # 4. Check & rank
    print(f"{C_BOLD}[4/5] TLS-проверка узлов (порт {cfg.check_port})...{C_RESET}")
    ranked = checker.rank_ips(cands, dom, port=cfg.check_port, timeout=cfg.check_timeout)
    if not ranked:
        print(f"      {C_RED}[ERR] Ни один из узлов не ответил на TLS-хэндшейк!{C_RESET}")
        sys.exit(1)
    best_ip, lat = ranked[0]
    print(f"      {C_GREEN}[✓] Выбран лучший узел: {best_ip} (пинг: {lat:.1f} мс){C_RESET}")

    # 5. Hosts
    print(f"{C_BOLD}[5/5] Маршрутизация (hosts)...{C_RESET}")
    ok, msg = hosts.set_entries([(best_ip, d) for d in cfg.domains])
    if not ok:
        print(f"      {C_RED}[ERR] {msg}{C_RESET}")
        sys.exit(1)
    print(f"      {C_GREEN}[✓] {best_ip} -> {dom} прописан в {hosts.DEFAULT_HOSTS_PATH}{C_RESET}")

    # 6. Auto-scheduler check
    if not scheduler.is_task_active():
        ok, s_msg = scheduler.install_task(cfg.check_interval_minutes)
        if ok:
            print(f"      {C_GREEN}[✓] {s_msg}{C_RESET}")

    print(f"\n{C_GREEN}{C_BOLD}[✓] Обход активирован! Antigravity готов к работе без VPN.{C_RESET}\n")


def cmd_off(args, cfg: Config):
    _req_admin("off")
    ok, msg = hosts.clear_entries()
    print(f"{C_GREEN}[✓] {msg}{C_RESET}" if ok else f"{C_RED}[ERR] {msg}{C_RESET}")
    rm = endpoint.remove_endpoint()
    if rm:
        print(f"{C_GREEN}[✓] Удалены переменные окружения ({', '.join(rm)}){C_RESET}")
    else:
        print("    Переменные окружения не были установлены.")


def cmd_status(args, cfg: Config):
    bp = patcher.find_binary(cfg.binary_path)
    bst = patcher.get_status(bp) if bp else "NOT_FOUND"
    running = patcher.is_binary_running(bp) if bp else False
    pids = patcher.get_running_pids(bp) if bp else []
    has_ep, ep_desc = endpoint.get_status()
    entries = hosts.get_active_entries()

    node_info = None
    if entries:
        ip, dom = entries[0]
        alive, lat, detail = checker.test_ip_with_retries(ip, dom, port=cfg.check_port, retries=1)
        node_info = {"ip": ip, "domain": dom, "alive": alive, "latency_ms": lat if alive else 0.0, "detail": detail}

    task_active = scheduler.is_task_active()

    print(f"\n{C_CYAN}{C_BOLD}=== Antigravity Windows Bypass Status ==={C_RESET}")
    bcol = C_GREEN if bst == "ALREADY_PATCHED" else C_YELLOW
    proc_info = f" (запущен, PID: {', '.join(map(str, pids))})" if running else " (не запущен)"
    print(f"1. Клиент:      {bcol}{bst}{C_RESET}{proc_info}")
    print(f"2. Эндпоинт:    {C_GREEN if has_ep else C_YELLOW}{ep_desc}{C_RESET}")
    hdesc = f"{C_GREEN}АКТИВЕН ({', '.join(f'{i} -> {d}' for i, d in entries)}){C_RESET}" if entries else f"{C_YELLOW}ОТКЛЮЧЕН{C_RESET}"
    print(f"3. Маршруты:    {hdesc}")
    if node_info:
        ncol = C_GREEN if node_info["alive"] else C_RED
        status_word = "ДОСТУПЕН" if node_info["alive"] else "НЕ ОТВЕЧАЕТ"
        print(f"4. Активный IP: {ncol}{node_info['ip']} - {status_word} ({node_info['latency_ms']:.1f} мс, {node_info['detail']}){C_RESET}")
    else:
        print(f"4. Активный IP: {C_YELLOW}нет активных записей в hosts{C_RESET}")
    tcol = C_GREEN if task_active else C_YELLOW
    print(f"5. Мониторинг:  {tcol}{'АКТИВЕН в планировщике Windows (каждые ' + str(cfg.check_interval_minutes) + ' мин)' if task_active else 'не установлен'}{C_RESET}\n")


def cmd_nodes(args, cfg: Config):
    dom = cfg.domains[0]
    dns = resolver.resolve_dns_server(cfg.smart_dns_host, cfg.smart_dns_fallback_ip)
    cands = resolver.query_a_records(dns, dom, port=cfg.smart_dns_port, timeout=cfg.smart_dns_timeout) or [
        "159.194.200.33", "193.233.112.67", "193.233.112.68", "193.233.112.88"
    ]
    active_ips = {ip for ip, _ in hosts.get_active_entries()}
    all_ips = list(dict.fromkeys(cands + list(active_ips)))

    print(f"\n{C_CYAN}{C_BOLD}Тестирование доступных узлов...{C_RESET}\n")
    res = checker.check_all_ips(all_ips, dom, port=cfg.check_port, timeout=cfg.check_timeout)

    print(f"{C_BOLD}{'IP':<18} {'СТАТУС':<10} {'ОТКЛИК':<12} {'АКТИВЕН':<9} {'ДЕТАЛИ'}{C_RESET}\n" + "-" * 75)
    for r in res:
        st = f"{C_GREEN}ONLINE{C_RESET}" if r["ok"] else f"{C_RED}OFFLINE{C_RESET}"
        lat = f"{r['latency_ms']:.1f} мс" if r["ok"] else "-"
        act = f"{C_CYAN}* ДА{C_RESET}" if r["ip"] in active_ips else "  -"
        det = r["detail"][:30] + "..." if len(r["detail"]) > 33 else r["detail"]
        print(f"{r['ip']:<18} {st:<19} {lat:<12} {act:<18} {det}")
    print()


def cmd_check(args, cfg: Config):
    if not is_admin() and not args.quiet:
        print(f"{C_YELLOW}[!] Предупреждение: для смены узла при падении потребуются права Администратора.{C_RESET}")
    st, msg = monitor.check_and_heal(cfg, quiet=args.quiet)
    if not args.quiet:
        col = C_GREEN if st in ("HEALTHY", "HEALED") else C_YELLOW
        print(f"{col}[{st}] {msg}{C_RESET}")


def cmd_unpatch(args, cfg: Config):
    _req_admin("unpatch")
    bp = patcher.find_binary(cfg.binary_path)
    if not bp:
        sys.exit(f"{C_RED}[ERR] Бинарник не найден.{C_RESET}")
    ok, msg = patcher.restore_backup(bp)
    print(f"{C_GREEN if ok else C_RED}[{'✓' if ok else 'ERR'}] {msg}{C_RESET}")


def main():
    _init_windows_console()
    parser = argparse.ArgumentParser(
        description="Antigravity Windows Bypass — обход геоблокировки без сторонних VPN"
    )
    parser.add_argument("-c", "--config", help="Путь к файлу конфигурации (по умолчанию config.json)")
    sub = parser.add_subparsers(dest="cmd", title="Команды")

    sub.add_parser("on", help="Включить обход: пропатчить клиент, найти лучший IP и прописать в hosts")
    sub.add_parser("off", help="Отключить обход: очистить hosts и удалить переменные окружения")
    sub.add_parser("status", help="Показать статус патча, эндпоинта, текущий IP и пинг")
    sub.add_parser("nodes", help="Протестировать все доступные узлы и вывести задержки")

    p_check = sub.add_parser("check", help="Проверить доступность узла и переключить при падении")
    p_check.add_argument("-q", "--quiet", action="store_true", help="Тихий режим (для фонового планировщика)")

    sub.add_parser("unpatch", help="Восстановить оригинальный agy.exe из бэкапа")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(0)

    cfg = Config(args.config)
    cmds = {
        "on": cmd_on,
        "off": cmd_off,
        "status": cmd_status,
        "nodes": cmd_nodes,
        "check": cmd_check,
        "unpatch": cmd_unpatch,
    }
    cmds[args.cmd](args, cfg)


if __name__ == "__main__":
    main()

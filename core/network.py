from concurrent.futures import ThreadPoolExecutor, as_completed
import random
import socket
import ssl
import struct
import time


# =====================================================================
# DNS Resolution & Queries
# =====================================================================

def resolve_target(target: str | None) -> str | None:
    """Разрешает имя хоста в IPv4 или возвращает IP, если уже передан IP."""
    if not (t := (target or "").strip()) or t in ("0.0.0.0", "255.255.255.255") or t.startswith("0."):
        return None
    try:
        socket.inet_aton(t)
        return t
    except OSError:
        pass
    try:
        ip = socket.getaddrinfo(t, 53, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]
        return None if ip in ("0.0.0.0", "255.255.255.255") or ip.startswith("0.") else ip
    except Exception:
        return None


def _skip_dns_name(data: bytes, off: int, max_steps: int = 128) -> int:
    steps = 0
    while off < len(data) and steps < max_steps:
        steps += 1
        b = data[off]
        if b == 0:
            return off + 1
        if b & 0xC0 == 0xC0:
            return off + 2
        if (b & 0xC0) != 0:
            return len(data)
        off += 1 + b
    return len(data)


def _parse_dns_response(data: bytes, qid: int) -> list[str]:
    if len(data) < 12:
        return []
    try:
        rid, fl, qdc, anc = struct.unpack("!HHHH", data[:8])
        if rid != qid or not (fl & 0x8000) or (fl & 0x000F) != 0 or anc == 0:
            return []
        off = 12
        for _ in range(qdc):
            if off >= len(data):
                break
            off = _skip_dns_name(data, off) + 4
        ips = []
        for _ in range(anc):
            if off >= len(data):
                break
            off = _skip_dns_name(data, off)
            if off + 10 > len(data):
                break
            rtype, _, _, rdlen = struct.unpack("!HHIH", data[off : off + 10])
            off += 10
            if rtype == 1 and rdlen == 4 and off + 4 <= len(data):
                ip = socket.inet_ntoa(data[off : off + 4])
                if ip not in ips and ip not in ("0.0.0.0", "255.255.255.255") and not ip.startswith("0."):
                    ips.append(ip)
            off += rdlen
        return ips
    except Exception:
        return []


def query_a_records(dns_ip: str | None, domain: str, port: int = 53, timeout: float = 3.0, retries: int = 2) -> list[str]:
    clean = (domain or "").strip().strip(".")
    if not dns_ip or not clean:
        return []
    try:
        p = int(port)
        if not (1 <= p <= 65535):
            return []
    except (ValueError, TypeError):
        return []
    try:
        idna_dom = clean.encode("idna").decode("ascii")
        parts = idna_dom.split(".")
        if any(len(p) == 0 or len(p) > 63 for p in parts):
            return []
        qname = b"".join(bytes([len(p)]) + p.encode("ascii") for p in parts) + b"\x00"
        if len(qname) > 255:
            return []
    except Exception:
        return []

    for _ in range(max(1, retries)):
        qid = random.randint(1, 65535)
        pkt = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0) + qname + struct.pack("!HH", 1, 1)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(timeout)
                s.sendto(pkt, (dns_ip, p))
                data, _ = s.recvfrom(2048)
                if ips := _parse_dns_response(data, qid):
                    return ips
        except Exception:
            pass
    return []


def query_dns_server(server: str, domain: str, port: int = 53, timeout: float = 3.0, retries: int = 2) -> dict:
    try:
        dns_ip = resolve_target(server)
        if not dns_ip:
            return {"server": server, "dns_ip": None, "ips": [], "error": f"Не удалось разрешить '{server}'"}
        ips = query_a_records(dns_ip, domain, port=port, timeout=timeout, retries=retries)
        err = None if ips else f"DNS {dns_ip} не вернул A-записей ({timeout}с)"
        return {"server": server, "dns_ip": dns_ip, "ips": ips, "error": err}
    except Exception as e:
        return {"server": server, "dns_ip": None, "ips": [], "error": str(e)}


def query_all_dns_servers(servers: list[str], domain: str, port: int = 53, timeout: float = 3.0, retries: int = 2) -> list[dict]:
    cleaned = list(dict.fromkeys(s.strip() for s in (servers or []) if s and s.strip()))
    if not cleaned:
        return []
    with ThreadPoolExecutor(max_workers=min(len(cleaned), 8)) as ex:
        futs = {ex.submit(query_dns_server, s, domain, port, timeout, retries): s for s in cleaned}
        results = []
        for f in as_completed(futs):
            s = futs[f]
            try:
                results.append(f.result())
            except Exception as e:
                results.append({"server": s, "dns_ip": None, "ips": [], "error": str(e)})
        order = {s: i for i, s in enumerate(cleaned)}
        return sorted(results, key=lambda r: order.get(r["server"], 999))


class ResolutionResult:
    def __init__(self, summary: str, dns_results: list[dict], candidate_ips: list[str], config_nodes: list[str]):
        self.summary, self.dns_results = summary, dns_results
        self.candidate_ips, self.config_nodes = candidate_ips, config_nodes

    def __str__(self) -> str:
        return self.summary


def get_candidate_ips(config, domain: str) -> tuple[list[str], ResolutionResult]:
    servers = getattr(config, "smart_dns_servers", []) or []
    dns_results = query_all_dns_servers(
        servers, domain, port=getattr(config, "smart_dns_port", 53), timeout=getattr(config, "smart_dns_timeout", 3.0)
    )

    dns_ips = list(dict.fromkeys(ip for r in dns_results for ip in r["ips"]))
    working = [r for r in dns_results if r["ips"]]
    nodes = list(getattr(config, "nodes", []))
    combined = list(dict.fromkeys(dns_ips + nodes))

    if working:
        summary = f"SmartDNS ({len(working)}/{len(servers)} ответили, найдено {len(dns_ips)} IP)" + (f" + узлы ({len(nodes)})" if nodes else "")
    elif nodes:
        summary = f"Узлы из config.json" + (f" (все {len(servers)} DNS не ответили)" if servers else "")
    elif servers:
        summary = f"Все настроенные DNS-серверы ({len(servers)}) не вернули записей"
    else:
        summary = "DNS-серверы не настроены и список nodes пуст"

    return combined, ResolutionResult(summary, dns_results, combined, nodes)


# =====================================================================
# TLS SNI Handshake & Healthcheck
# =====================================================================

def test_ip(ip: str, domain: str, port: int = 443, timeout: float = 2.5) -> tuple[bool, float, str]:
    """Выполняет реальный TLS SNI-хэндшейк и HEAD-запрос к узлу."""
    if not ip or not (clean_ip := str(ip).strip()) or clean_ip in ("0.0.0.0", "255.255.255.255", "::") or clean_ip.startswith("0."):
        return False, 0.0, "Некорректный IP"
    if not domain or not (clean_dom := str(domain).strip()) or any(c in clean_dom for c in ("\r", "\n", " ", "\t")):
        return False, 0.0, "Некорректный домен"
    try:
        p = int(port)
        if not (1 <= p <= 65535):
            return False, 0.0, "Некорректный порт"
    except (ValueError, TypeError):
        return False, 0.0, "Некорректный порт"
    t0 = time.monotonic()
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        with socket.create_connection((clean_ip, p), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=clean_dom) as s:
                s.settimeout(timeout)
                lat = (time.monotonic() - t0) * 1000.0
                s.sendall(f"HEAD / HTTP/1.1\r\nHost: {clean_dom}\r\nConnection: close\r\n\r\n".encode())
                resp = s.recv(256)
                if not resp:
                    return False, 0.0, "Пустой ответ от сервера"
                raw_status = resp.split(b"\r\n")[0].decode("latin1", errors="ignore").strip()
                status = "".join(c if (c.isprintable() or c == " ") else " " for c in raw_status).strip() or "TLS/SNI OK"
                return True, lat, status
    except Exception as e:
        return False, 0.0, str(e)


def test_ip_with_retries(
    ip: str, domain: str, port: int = 443, timeout: float = 2.5, retries: int = 3, delay: float = 1.0
) -> tuple[bool, float, str]:
    err = ""
    attempts = max(1, retries)
    for attempt in range(attempts):
        ok, lat, detail = test_ip(ip, domain, port, timeout)
        if ok:
            return True, lat, detail
        err = detail
        if attempt < attempts - 1:
            time.sleep(max(0.0, delay))
    return False, 0.0, f"Не ответил после {attempts} попыток ({err})"


def check_all_ips(ips: list[str], domain: str, port: int = 443, timeout: float = 2.5) -> list[dict]:
    if not ips:
        return []
    unique_ips = list(dict.fromkeys(ips))
    with ThreadPoolExecutor(max_workers=min(len(unique_ips), 8)) as ex:
        futs = {ex.submit(test_ip, ip, domain, port, timeout): ip for ip in unique_ips}
        results = []
        for f in as_completed(futs):
            ip = futs[f]
            try:
                ok, lat, detail = f.result()
            except Exception as e:
                ok, lat, detail = False, 0.0, str(e)
            results.append({"ip": ip, "ok": ok, "latency_ms": lat if ok else 0.0, "detail": detail})
    order = {ip: i for i, ip in enumerate(unique_ips)}
    return sorted(results, key=lambda x: (not x["ok"], x["latency_ms"], order.get(x["ip"], 0)))


def rank_ips(ips: list[str], domain: str, port: int = 443, timeout: float = 2.5) -> list[tuple[str, float]]:
    return [(r["ip"], r["latency_ms"]) for r in check_all_ips(ips, domain, port, timeout) if r["ok"]]


# =====================================================================
# Failover & Self-heal (ранее monitor.py)
# =====================================================================

def check_and_heal(config, quiet: bool = False) -> tuple[str, str]:
    """Проверяет текущий узел и при сбое автоматически переключает на рабочий SNI."""
    from core import system

    entries = system.get_active_entries()
    if not entries:
        return "DISABLED", "Обход не включен в /etc/hosts"

    domain = config.domains[0] if config.domains else "daily-cloudcode-pa.googleapis.com"
    cur_ip = next((ip for ip, d in entries if d == domain), entries[0][0])
    alive, lat, detail = test_ip_with_retries(
        cur_ip, domain, port=config.check_port, timeout=config.check_timeout,
        retries=config.check_retries, delay=config.check_retry_delay
    )
    if alive:
        return "HEALTHY", f"Текущий узел {cur_ip} доступен (задержка {lat:.1f} мс)"

    if not quiet:
        print(f"[!] Узел {cur_ip} не отвечает ({detail}). Опрос DNS и поиск нового SNI...")

    candidates, res_info = get_candidate_ips(config, domain)
    if not candidates:
        return "FAILED", f"Не удалось получить узлы для замены: {res_info.summary}"

    ranked = rank_ips(candidates, domain, port=config.check_port, timeout=config.check_timeout)
    if not ranked:
        return "FAILED", f"Ни один из найденных узлов ({', '.join(candidates)}) не прошёл TLS SNI-проверку"

    new_ip, new_lat = ranked[0]
    if new_ip == cur_ip:
        return "HEALTHY", f"Узел {cur_ip} снова отвечает (задержка {new_lat:.1f} мс)"

    target_domains = config.domains if config.domains else [domain]
    ok, msg = system.set_entries([(new_ip, d) for d in target_domains])
    return ("HEALED", f"Переключено с {cur_ip} на {new_ip} ({new_lat:.1f} мс)") if ok else ("ERROR", f"Ошибка записи в /etc/hosts: {msg}")

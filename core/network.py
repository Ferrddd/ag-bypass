from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import random
import socket
import ssl
import struct
import time
import urllib.error
import urllib.parse
import urllib.request


# =====================================================================
# DNS Resolution & Queries
# =====================================================================

def _is_invalid_ip(ip: str) -> bool:
    return not ip or ip in ("0.0.0.0", "255.255.255.255", "::") or ip.startswith("0.")


def resolve_target(target: str | None) -> str | None:
    """Разрешает имя хоста в IPv4 или возвращает IP, если уже передан IP."""
    if _is_invalid_ip(t := (target or "").strip()):
        return None
    try:
        socket.inet_aton(t)
        return t
    except OSError:
        pass
    try:
        ip = socket.getaddrinfo(t, 53, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]
        return None if _is_invalid_ip(ip) else ip
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
                if ip not in ips and not _is_invalid_ip(ip):
                    ips.append(ip)
            off += rdlen
        return ips
    except Exception:
        return []


def _build_dns_query(domain: str, qid: int | None = None) -> tuple[bytes, int] | None:
    clean = (domain or "").strip().strip(".")
    if not clean:
        return None
    try:
        idna_dom = clean.encode("idna").decode("ascii")
        parts = idna_dom.split(".")
        if any(len(p) == 0 or len(p) > 63 for p in parts):
            return None
        qname = b"".join(bytes([len(p)]) + p.encode("ascii") for p in parts) + b"\x00"
        if len(qname) > 255:
            return None
    except Exception:
        return None
    q_id = random.randint(1, 65535) if qid is None else int(qid)
    pkt = struct.pack("!HHHHHH", q_id, 0x0100, 1, 0, 0, 0) + qname + struct.pack("!HH", 1, 1)
    return pkt, q_id


def _recv_exact(sock, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def query_a_records(dns_ip: str | None, domain: str, port: int = 53, timeout: float = 3.0, retries: int = 2) -> list[str]:
    if not dns_ip or not _build_dns_query(domain, qid=0):
        return []
    try:
        p = int(port)
        if not (1 <= p <= 65535):
            return []
    except (ValueError, TypeError):
        return []

    for _ in range(max(1, retries)):
        built = _build_dns_query(domain)
        if not built:
            return []
        pkt, qid = built
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
        return list(ex.map(lambda s: query_dns_server(s, domain, port, timeout, retries), cleaned))


DEFAULT_DOH_SERVERS = [
    "https://1.1.1.1/dns-query",
    "https://dns.google/resolve",
]

DEFAULT_DOT_SERVERS = [
    "tls://1.1.1.1",
    "tls://dns.google",
]


def _extract_valid_ipv4s(candidates: list[str]) -> list[str]:
    ips = []
    for raw in candidates:
        ip = str(raw or "").strip()
        if not _is_invalid_ip(ip) and ip not in ips:
            try:
                socket.inet_aton(ip)
                ips.append(ip)
            except OSError:
                pass
    return ips


def _is_dot_server(server: str) -> bool:
    s = (server or "").strip().lower()
    if s.startswith("tls://"):
        return True
    return not s.startswith(("http://", "https://")) and s.endswith(":853")


def _extract_plain_domain(server: str) -> str | None:
    """Return server hostname if it is a bare domain (not an IP, URL, or host:port)."""
    s = (server or "").strip().rstrip(".")
    if not s or "://" in s or "/" in s or ":" in s or "." not in s or any(c.isspace() for c in s):
        return None
    try:
        socket.inet_aton(s)
        return None
    except OSError:
        return s


def _url_hostname(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").strip().lower()
    except Exception:
        return ""


def _parse_dot_target(target: str, default_port: int = 853) -> tuple[str, int, str] | None:
    raw = (target or "").strip()
    if not raw or raw.lower().startswith(("http://", "https://")):
        return None
    normalized = raw if "://" in raw else f"tls://{raw}"
    try:
        parsed = urllib.parse.urlsplit(normalized)
    except Exception:
        return None
    if parsed.scheme.lower() != "tls" or not parsed.hostname:
        return None
    try:
        p = parsed.port if parsed.port is not None else int(default_port)
    except (ValueError, TypeError):
        return None
    if not (1 <= p <= 65535):
        return None
    sni = parsed.fragment.strip() if parsed.fragment else parsed.hostname
    return parsed.hostname, p, sni


def query_dot_server(target: str, domain: str, port: int = 853, timeout: float = 3.0) -> list[str]:
    """Резолвит A-записи через DNS over TLS (DoT, RFC 7858)."""
    parsed = _parse_dot_target(target, default_port=port)
    if not parsed:
        return []
    host, p, sni = parsed
    dns_ip = resolve_target(host)
    built = _build_dns_query(domain)
    if not dns_ip or not built:
        return []
    pkt, qid = built
    framed = struct.pack("!H", len(pkt)) + pkt
    try:
        ctx = ssl.create_default_context()
        try:
            ctx.set_alpn_protocols(["dot"])
        except Exception:
            pass
        with socket.create_connection((dns_ip, p), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=sni) as tls_sock:
                tls_sock.settimeout(timeout)
                tls_sock.sendall(framed)
                hdr = _recv_exact(tls_sock, 2)
                if len(hdr) < 2:
                    return []
                msg_len = struct.unpack("!H", hdr)[0]
                if msg_len < 12 or msg_len > 65535:
                    return []
                resp = _recv_exact(tls_sock, msg_len)
                return _parse_dns_response(resp, qid)
    except Exception:
        return []


def query_all_dot_servers(servers: list[str], domain: str, port: int = 853, timeout: float = 3.0) -> list[dict]:
    cleaned = list(dict.fromkeys(s.strip() for s in (servers or []) if s and s.strip()))
    if not cleaned:
        return []

    def _run_dot(s: str) -> dict:
        try:
            parsed = _parse_dot_target(s, default_port=port)
            dns_ip = resolve_target(parsed[0]) if parsed else None
            ips = query_dot_server(s, domain, port=port, timeout=timeout)
            err = None if ips else f"DoT {s} не вернул A-записей ({timeout}с)"
            return {"server": s, "dns_ip": dns_ip or s, "ips": ips, "error": err, "dot": True}
        except Exception as e:
            return {"server": s, "dns_ip": s, "ips": [], "error": str(e), "dot": True}

    with ThreadPoolExecutor(max_workers=min(len(cleaned), 4)) as ex:
        return list(ex.map(_run_dot, cleaned))


def query_doh_server(url: str, domain: str, timeout: float = 3.0) -> list[str]:
    """Резолвит A-записи через DNS over HTTPS (DoH: JSON API и RFC 8484 wire-format)."""
    clean_url = (url or "").strip()
    clean_dom = (domain or "").strip().strip(".")
    if not clean_url or not clean_dom or not clean_url.lower().startswith(("http://", "https://")):
        return []

    sep = "&" if "?" in clean_url else "?"
    encoded_dom = urllib.parse.quote(clean_dom, safe="")
    full_url = f"{clean_url}{sep}name={encoded_dom}&type=A"
    json_req = urllib.request.Request(
        full_url,
        headers={"Accept": "application/dns-json, application/json", "User-Agent": "ag-bypass/1.0"},
    )
    try_wire = False
    try:
        with urllib.request.urlopen(json_req, timeout=timeout) as resp:
            raw_body = resp.read()
        try:
            data = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Server may have returned binary DNS message directly
            if ips := _parse_dns_response(raw_body, 0):
                return ips
            try_wire = True
        else:
            if isinstance(data, dict) and ("Answer" in data or "Status" in data):
                return _extract_valid_ipv4s(
                    [ans.get("data", "") for ans in data.get("Answer", []) if isinstance(ans, dict) and ans.get("type") == 1]
                )
            try_wire = True
    except urllib.error.HTTPError:
        try_wire = True
    except Exception:
        return []

    if not try_wire:
        return []

    built = _build_dns_query(clean_dom, qid=0)
    if not built:
        return []
    pkt, qid = built
    wire_req = urllib.request.Request(
        clean_url,
        data=pkt,
        method="POST",
        headers={
            "Content-Type": "application/dns-message",
            "Accept": "application/dns-message",
            "User-Agent": "ag-bypass/1.0",
        },
    )
    try:
        with urllib.request.urlopen(wire_req, timeout=timeout) as resp:
            return _parse_dns_response(resp.read(), qid)
    except Exception:
        return []


def query_all_doh_servers(servers: list[str], domain: str, timeout: float = 3.0) -> list[dict]:
    cleaned = list(dict.fromkeys(s.strip() for s in (servers or []) if s and s.strip()))
    if not cleaned:
        return []

    def _run_doh(s: str) -> dict:
        try:
            ips = query_doh_server(s, domain, timeout)
            err = None if ips else f"DoH {s} не вернул A-записей ({timeout}с)"
            return {"server": s, "dns_ip": s, "ips": ips, "error": err, "doh": True}
        except Exception as e:
            return {"server": s, "dns_ip": s, "ips": [], "error": str(e), "doh": True}

    with ThreadPoolExecutor(max_workers=min(len(cleaned), 4)) as ex:
        return list(ex.map(_run_doh, cleaned))


class ResolutionResult:
    def __init__(self, summary: str, dns_results: list[dict], candidate_ips: list[str], config_nodes: list[str]):
        self.summary, self.dns_results = summary, dns_results
        self.candidate_ips, self.config_nodes = candidate_ips, config_nodes

    def __str__(self) -> str:
        return self.summary


def get_candidate_ips(config, domain: str) -> tuple[list[str], ResolutionResult]:
    all_servers = getattr(config, "smart_dns_servers", []) or []
    cfg_doh = list(getattr(config, "doh_servers", None) or [])
    cfg_dot = list(getattr(config, "dot_servers", None) or [])
    timeout = getattr(config, "smart_dns_timeout", 3.0)
    udp_port = getattr(config, "smart_dns_port", 53)
    dot_port = getattr(config, "smart_dns_dot_port", 853)

    explicit_doh = [s for s in all_servers if s.lower().startswith(("http://", "https://"))]
    explicit_dot_from_smart = [s for s in all_servers if _is_dot_server(s)]
    udp_servers = [s for s in all_servers if s not in explicit_doh and s not in explicit_dot_from_smart]
    if not all_servers and cfg_doh:
        explicit_doh = list(cfg_doh)
    explicit_dot = list(dict.fromkeys(explicit_dot_from_smart + cfg_dot))

    # Automatically derive DoT (tls://host) and DoH (https://host/dns-query) for bare domain entries
    plain_domains = [d for s in udp_servers if (d := _extract_plain_domain(s))]
    explicit_dot_hosts = {
        parsed[0].lower()
        for s in explicit_dot
        if (parsed := _parse_dot_target(s, default_port=dot_port))
    }
    explicit_doh_hosts = {h for s in explicit_doh if (h := _url_hostname(s))}

    auto_dot = [f"tls://{d}" for d in plain_domains if d.lower() not in explicit_dot_hosts]
    auto_doh = [f"https://{d}/dns-query" for d in plain_domains if d.lower() not in explicit_doh_hosts]

    dot_servers = list(dict.fromkeys(explicit_dot + auto_dot))
    doh_servers = list(dict.fromkeys(explicit_doh + auto_doh))

    udp_results: list[dict] = []
    dot_results: list[dict] = []
    doh_results: list[dict] = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        fut_udp = ex.submit(query_all_dns_servers, udp_servers, domain, udp_port, timeout) if udp_servers else None
        fut_dot = ex.submit(query_all_dot_servers, dot_servers, domain, dot_port, timeout) if dot_servers else None
        fut_doh = ex.submit(query_all_doh_servers, doh_servers, domain, timeout) if doh_servers else None
        if fut_udp:
            udp_results = list(fut_udp.result())
        if fut_dot:
            dot_results = list(fut_dot.result())
        if fut_doh:
            doh_results = list(fut_doh.result())

    dns_results = udp_results + dot_results + doh_results
    dns_ips = list(dict.fromkeys(ip for r in dns_results for ip in r["ips"]))
    working = [r for r in dns_results if r["ips"]]

    doh_fallback_used = False
    dot_fallback_used = False
    if not dns_ips and not explicit_doh and (all_servers or cfg_doh):
        fallback_doh = query_all_doh_servers(cfg_doh or DEFAULT_DOH_SERVERS, domain, timeout=timeout)
        dns_results.extend(fallback_doh)
        if any(r["ips"] for r in fallback_doh):
            dns_ips = list(dict.fromkeys(ip for r in dns_results for ip in r["ips"]))
            working = [r for r in dns_results if r["ips"]]
            doh_fallback_used = True

    if not dns_ips and (all_servers or explicit_doh):
        fallback_dot_list = [s for s in DEFAULT_DOT_SERVERS if s not in dot_servers]
        if fallback_dot_list:
            fallback_dot = query_all_dot_servers(fallback_dot_list, domain, port=dot_port, timeout=timeout)
            dns_results.extend(fallback_dot)
            if any(r["ips"] for r in fallback_dot):
                dns_ips = list(dict.fromkeys(ip for r in dns_results for ip in r["ips"]))
                working = [r for r in dns_results if r["ips"]]
                dot_fallback_used = True

    nodes = list(getattr(config, "nodes", []))
    combined = list(dict.fromkeys(dns_ips + nodes))

    if working:
        if doh_fallback_used:
            prefix = "SmartDNS (DoH)"
        elif dot_fallback_used:
            prefix = "SmartDNS (DoT)"
        else:
            prefix = "SmartDNS"
        summary = f"{prefix} ({len(working)}/{len(dns_results)} ответили, найдено {len(dns_ips)} IP)" + (f" + узлы ({len(nodes)})" if nodes else "")
    elif nodes:
        summary = "Узлы из config.json" + (" (все DNS/DoT/DoH не ответили)" if dns_results else "")
    elif dns_results:
        summary = f"Все настроенные DNS/DoT/DoH-серверы ({len(dns_results)}) не вернули записей"
    else:
        summary = "DNS-серверы не настроены и список nodes пуст"

    return combined, ResolutionResult(summary, dns_results, combined, nodes)


# =====================================================================
# TLS SNI Handshake & Healthcheck
# =====================================================================

def test_ip(ip: str, domain: str, port: int = 443, timeout: float = 2.5) -> tuple[bool, float, str]:
    """Выполняет реальный TLS SNI-хэндшейк и HEAD-запрос к узлу."""
    if not ip or _is_invalid_ip(clean_ip := str(ip).strip()):
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
        try:
            ctx.set_alpn_protocols(["http/1.1"])
        except Exception:
            pass
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
                code = None
                if raw_status.startswith("HTTP/"):
                    parts = raw_status.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        code = int(parts[1])
                if code in (502, 503, 504, 403):
                    return False, 0.0, f"Ошибка прокси: {status}"
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
        return "DISABLED", "Обход не включен в hosts"

    domain = config.domains[0] if config.domains else "daily-cloudcode-pa.googleapis.com"
    cur_ips = list(dict.fromkeys(ip for ip, d in entries if d == domain)) or [entries[0][0]]
    primary_ip = cur_ips[0]
    alive, lat, detail = test_ip_with_retries(
        primary_ip, domain, port=config.check_port, timeout=config.check_timeout,
        retries=config.check_retries, delay=config.check_retry_delay
    )
    if alive:
        return "HEALTHY", f"Текущий узел {primary_ip} доступен (задержка {lat:.1f} мс)"

    if not quiet:
        print(f"[!] Узел {primary_ip} не отвечает ({detail}). Опрос DNS и поиск нового SNI...")

    candidates, res_info = get_candidate_ips(config, domain)
    if not candidates:
        return "FAILED", f"Не удалось получить узлы для замены: {res_info.summary}"

    ranked = rank_ips(candidates, domain, port=config.check_port, timeout=config.check_timeout)
    if not ranked:
        return "FAILED", f"Ни один из найденных узлов ({', '.join(candidates)}) не прошёл TLS SNI-проверку"

    max_nodes = getattr(config, "max_hosts_ips", 2)
    new_ips = [r[0] for r in ranked[:max_nodes]]
    if new_ips == cur_ips:
        return "HEALTHY", f"Узел {primary_ip} снова отвечает (задержка {ranked[0][1]:.1f} мс)"

    target_domains = config.domains if config.domains else [domain]
    route_entries = [(ip, d) for ip in new_ips for d in target_domains]
    ok, msg = system.set_entries(route_entries)
    if ok:
        switched_to = ", ".join(new_ips)
        return "HEALED", f"Переключено с {primary_ip} на {switched_to} ({ranked[0][1]:.1f} мс)"
    return "ERROR", f"Ошибка записи в hosts: {msg}"

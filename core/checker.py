import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


def test_ip(
    ip: str, domain: str, port: int = 443, timeout: float = 2.5
) -> tuple[bool, float, str]:
    t0 = time.monotonic()
    raw_sock = None
    ssl_sock = None

    try:
        raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_sock.settimeout(timeout)
        raw_sock.connect((ip, port))

        # Test TLS Handshake with target SNI
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        ssl_sock = ctx.wrap_socket(raw_sock, server_hostname=domain)
        ssl_sock.settimeout(timeout)
        latency_ms = (time.monotonic() - t0) * 1000.0

        # Send a minimal HEAD request to ensure the proxy forwards and Google responds
        req = f"HEAD / HTTP/1.1\r\nHost: {domain}\r\nConnection: close\r\n\r\n".encode("utf-8")
        ssl_sock.sendall(req)
        resp_buf = ssl_sock.recv(256)
        if not resp_buf:
            return False, 0.0, "Пустой ответ от сервера"

        status_line = resp_buf.split(b"\r\n")[0].decode("latin1", errors="ignore")
        return True, latency_ms, status_line or "TLS Handshake OK"
    except Exception as e:
        return False, 0.0, str(e)
    finally:
        if ssl_sock:
            try:
                ssl_sock.close()
            except Exception:
                pass
        elif raw_sock:
            try:
                raw_sock.close()
            except Exception:
                pass


def test_ip_with_retries(
    ip: str,
    domain: str,
    port: int = 443,
    timeout: float = 2.5,
    retries: int = 3,
    delay: float = 1.0,
) -> tuple[bool, float, str]:
    retries = max(1, retries)
    last_err = ""
    for attempt in range(1, retries + 1):
        ok, latency, detail = test_ip(ip, domain, port=port, timeout=timeout)
        if ok:
            return True, latency, detail
        last_err = detail
        if attempt < retries:
            time.sleep(delay)
    return False, 0.0, f"Не ответил после {retries} попыток ({last_err})"


def check_all_ips(
    ips: list[str], domain: str, port: int = 443, timeout: float = 2.5
) -> list[dict]:
    if not ips:
        return []

    results: list[dict] = []
    max_workers = min(len(ips), 8)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ip = {
            executor.submit(test_ip, ip, domain, port, timeout): ip
            for ip in ips
        }
        for future in as_completed(future_to_ip):
            ip = future_to_ip[future]
            try:
                ok, latency, detail = future.result()
                results.append({
                    "ip": ip,
                    "ok": ok,
                    "latency_ms": latency if ok else 0.0,
                    "detail": detail,
                })
            except Exception as e:
                results.append({
                    "ip": ip,
                    "ok": False,
                    "latency_ms": 0.0,
                    "detail": str(e),
                })

    # Sort: working nodes first by ascending latency, then failing nodes
    results.sort(key=lambda item: (not item["ok"], item["latency_ms"]))
    return results


def rank_ips(
    ips: list[str], domain: str, port: int = 443, timeout: float = 2.5
) -> list[tuple[str, float]]:
    all_res = check_all_ips(ips, domain, port=port, timeout=timeout)
    return [(r["ip"], r["latency_ms"]) for r in all_res if r["ok"]]

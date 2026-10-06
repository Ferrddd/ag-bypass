import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


def test_ip(ip: str, domain: str, port: int = 443, timeout: float = 2.5) -> tuple[bool, float, str]:
    t0 = time.monotonic()
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        with socket.create_connection((ip, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=domain) as s:
                s.settimeout(timeout)
                lat = (time.monotonic() - t0) * 1000.0
                s.sendall(f"HEAD / HTTP/1.1\r\nHost: {domain}\r\nConnection: close\r\n\r\n".encode())
                resp = s.recv(256)
                if not resp:
                    return False, 0.0, "Пустой ответ от сервера"
                return True, lat, resp.split(b"\r\n")[0].decode("latin1", errors="ignore") or "TLS Handshake OK"
    except Exception as e:
        return False, 0.0, str(e)


def test_ip_with_retries(
    ip: str, domain: str, port: int = 443, timeout: float = 2.5, retries: int = 3, delay: float = 1.0
) -> tuple[bool, float, str]:
    err = ""
    for attempt in range(max(1, retries)):
        ok, lat, detail = test_ip(ip, domain, port, timeout)
        if ok:
            return True, lat, detail
        err = detail
        if attempt < retries - 1:
            time.sleep(delay)
    return False, 0.0, f"Не ответил после {retries} попыток ({err})"


def check_all_ips(ips: list[str], domain: str, port: int = 443, timeout: float = 2.5) -> list[dict]:
    if not ips:
        return []
    results, unique_ips = [], list(dict.fromkeys(ips))
    with ThreadPoolExecutor(max_workers=min(len(unique_ips), 8)) as ex:
        futs = {ex.submit(test_ip, ip, domain, port, timeout): ip for ip in unique_ips}
        for f in as_completed(futs):
            ip = futs[f]
            try:
                ok, lat, detail = f.result()
            except Exception as e:
                ok, lat, detail = False, 0.0, str(e)
            results.append({"ip": ip, "ok": ok, "latency_ms": lat if ok else 0.0, "detail": detail})
    return sorted(results, key=lambda x: (not x["ok"], x["latency_ms"]))


def rank_ips(ips: list[str], domain: str, port: int = 443, timeout: float = 2.5) -> list[tuple[str, float]]:
    return [(r["ip"], r["latency_ms"]) for r in check_all_ips(ips, domain, port, timeout) if r["ok"]]

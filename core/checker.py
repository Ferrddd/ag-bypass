import socket
import ssl
import time


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


def rank_ips(
    ips: list[str], domain: str, port: int = 443, timeout: float = 2.5
) -> list[tuple[str, float]]:
    results: list[tuple[str, float]] = []
    for ip in ips:
        ok, latency, _ = test_ip(ip, domain, port=port, timeout=timeout)
        if ok:
            results.append((ip, latency))

    # Sort by lowest latency first
    results.sort(key=lambda item: item[1])
    return results

import random
import socket
import struct


def resolve_dns_server(host_or_ip: str, fallback_ip: str = "45.155.204.190") -> str:
    try:
        socket.inet_aton(host_or_ip)
        return host_or_ip
    except socket.error:
        try:
            return socket.getaddrinfo(host_or_ip, 53, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]
        except Exception:
            return fallback_ip


def _skip_name(data: bytes, off: int) -> int:
    while off < len(data):
        b = data[off]
        if b == 0:
            return off + 1
        if b & 0xC0 == 0xC0:
            return off + 2
        off += 1 + b
    return len(data)


def _parse_response(data: bytes, qid: int) -> list[str]:
    if len(data) < 12:
        return []
    rid, fl, qdc, anc = struct.unpack("!HHHH", data[:8])
    if rid != qid or not (fl & 0x8000) or (fl & 0x000F) != 0 or anc == 0:
        return []
    off = 12
    for _ in range(qdc):
        off = _skip_name(data, off) + 4
    ips = []
    for _ in range(anc):
        if off >= len(data):
            break
        off = _skip_name(data, off)
        if off + 10 > len(data):
            break
        rtype, _, _, rdlen = struct.unpack("!HHIH", data[off : off + 10])
        off += 10
        if rtype == 1 and rdlen == 4 and off + 4 <= len(data):
            ip = socket.inet_ntoa(data[off : off + 4])
            if ip not in ips:
                ips.append(ip)
        off += rdlen
    return ips


def query_a_records(dns_ip: str, domain: str, port: int = 53, timeout: float = 3.0, retries: int = 2) -> list[str]:
    qname = b"".join(bytes([len(p)]) + p.encode("ascii") for p in domain.strip(".").split(".")) + b"\x00"
    for _ in range(max(1, retries)):
        qid = random.randint(1, 65535)
        pkt = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0) + qname + struct.pack("!HH", 1, 1)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(timeout)
                s.sendto(pkt, (dns_ip, port))
                data, _ = s.recvfrom(2048)
                if ips := _parse_response(data, qid):
                    return ips
        except Exception:
            pass
    return []

import socket
import struct
import random


def resolve_dns_server(host_or_ip: str, fallback_ip: str = "45.155.204.190") -> str:
    # Check if host_or_ip is already a valid IPv4
    try:
        socket.inet_aton(host_or_ip)
        return host_or_ip
    except socket.error:
        pass

    try:
        info = socket.getaddrinfo(host_or_ip, 53, socket.AF_INET, socket.SOCK_DGRAM)
        for _, _, _, _, sockaddr in info:
            return sockaddr[0]
    except Exception:
        pass

    return fallback_ip


def _build_dns_query(domain: str, query_id: int) -> bytes:
    # Header: ID, Flags (0x0100 standard query, RD=1), QDCOUNT=1, ANCOUNT=0, NSCOUNT=0, ARCOUNT=0
    header = struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
    # QNAME
    qname = b"".join(bytes([len(part)]) + part.encode("ascii") for part in domain.split(".")) + b"\x00"
    # QTYPE=1 (A), QCLASS=1 (IN)
    question = qname + struct.pack("!HH", 1, 1)
    return header + question


def _skip_dns_name(data: bytes, offset: int, max_steps: int = 128) -> int:
    steps = 0
    data_len = len(data)
    while offset < data_len and steps < max_steps:
        steps += 1
        length = data[offset]
        if length == 0:
            return offset + 1
        if (length & 0xC0) == 0xC0:  # 0xC0 pointer
            return offset + 2
        next_offset = offset + 1 + length
        if next_offset > data_len:
            return data_len
        offset = next_offset
    return data_len


def _parse_dns_response(data: bytes, query_id: int) -> list[str]:
    if len(data) < 12:
        return []

    res_id, flags, qdcount, ancount, _, _ = struct.unpack("!HHHHHH", data[:12])
    rcode = flags & 0x000F
    is_response = bool(flags & 0x8000)

    # Must match query ID, be a response, have RCODE 0 (NoError), and have at least 1 answer
    if res_id != query_id or not is_response or rcode != 0 or ancount == 0:
        return []

    offset = 12
    # Skip questions
    for _ in range(qdcount):
        offset = _skip_dns_name(data, offset)
        offset += 4  # QTYPE (2) + QCLASS (2)
        if offset > len(data):
            return []

    ips: list[str] = []
    # Parse answers
    for _ in range(ancount):
        if offset >= len(data):
            break
        offset = _skip_dns_name(data, offset)
        if offset + 10 > len(data):
            break

        rtype, rclass, ttl, rdlength = struct.unpack("!HHIH", data[offset : offset + 10])
        offset += 10
        if offset + rdlength > len(data):
            break

        rdata = data[offset : offset + rdlength]
        offset += rdlength

        if rtype == 1 and rdlength == 4:  # Type A (IPv4)
            ip_str = socket.inet_ntoa(rdata)
            if ip_str not in ips:
                ips.append(ip_str)

    return ips


def query_a_records(
    dns_server_ip: str, domain: str, port: int = 53, timeout: float = 3.0, retries: int = 2
) -> list[str]:
    retries = max(1, retries)
    for _ in range(retries):
        query_id = random.randint(1, 65535)
        packet = _build_dns_query(domain, query_id)

        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            sock.sendto(packet, (dns_server_ip, port))
            data, _ = sock.recvfrom(2048)
            ips = _parse_dns_response(data, query_id)
            if ips:
                return ips
        except Exception:
            pass
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
    return []

import os
import shutil
import subprocess
from pathlib import Path

TAG_BEGIN = "# BEGIN AG_BYPASS"
TAG_END = "# END AG_BYPASS"
DEFAULT_HOSTS_PATH = Path("/etc/hosts")


def flush_dns_cache() -> bool:
    flushed = False
    if shutil.which("resolvectl"):
        res = subprocess.run(["resolvectl", "flush-caches"], capture_output=True)
        if res.returncode == 0:
            flushed = True
    elif shutil.which("systemd-resolve"):
        res = subprocess.run(["systemd-resolve", "--flush-caches"], capture_output=True)
        if res.returncode == 0:
            flushed = True
    return flushed


def get_active_entries(hosts_path: Path = DEFAULT_HOSTS_PATH) -> list[tuple[str, str]]:
    if not hosts_path.is_file():
        return []

    try:
        content = hosts_path.read_text(encoding="utf-8")
    except Exception:
        return []

    lines = content.splitlines()
    in_block = False
    entries: list[tuple[str, str]] = []

    for line in lines:
        stripped = line.strip()
        if stripped == TAG_BEGIN:
            in_block = True
            continue
        if stripped == TAG_END:
            in_block = False
            continue
        if in_block and stripped and not stripped.startswith("#"):
            parts = stripped.split()
            if len(parts) >= 2:
                ip = parts[0]
                domain = parts[1]
                entries.append((ip, domain))

    return entries


def _strip_block(lines: list[str]) -> list[str]:
    out: list[str] = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if stripped == TAG_BEGIN:
            in_block = True
            continue
        if stripped == TAG_END:
            in_block = False
            continue
        if not in_block:
            out.append(line)
    return out


def set_entries(
    entries: list[tuple[str, str]], hosts_path: Path = DEFAULT_HOSTS_PATH
) -> tuple[bool, str]:
    if not hosts_path.is_file():
        return False, f"Файл не найден: {hosts_path}"

    try:
        content = hosts_path.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"Ошибка чтения {hosts_path}: {e}"

    lines = _strip_block(content.splitlines())

    # Build new block (keep single best IP per domain)
    seen_domains: set[str] = set()
    block_lines = [TAG_BEGIN]
    for ip, domain in entries:
        if domain not in seen_domains:
            seen_domains.add(domain)
            block_lines.append(f"{ip} {domain}")
    block_lines.append(TAG_END)

    # Append block cleanly
    cleaned = "\n".join(lines).rstrip()
    new_content = cleaned + "\n\n" + "\n".join(block_lines) + "\n"

    # Write atomically
    tmp_path = hosts_path.with_name(f"{hosts_path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(new_content, encoding="utf-8")
        st = os.stat(hosts_path)
        os.chmod(tmp_path, st.st_mode)
        os.replace(tmp_path, hosts_path)
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        return False, f"Ошибка записи {hosts_path}: {e}"

    flush_dns_cache()
    return True, "Записи успешно обновлены в /etc/hosts"


def clear_entries(hosts_path: Path = DEFAULT_HOSTS_PATH) -> tuple[bool, str]:
    if not hosts_path.is_file():
        return False, f"Файл не найден: {hosts_path}"

    try:
        content = hosts_path.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"Ошибка чтения {hosts_path}: {e}"

    if TAG_BEGIN not in content:
        return True, "Блок обхода отсутствует в /etc/hosts"

    lines = _strip_block(content.splitlines())
    new_content = "\n".join(lines).rstrip() + "\n"

    tmp_path = hosts_path.with_name(f"{hosts_path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(new_content, encoding="utf-8")
        st = os.stat(hosts_path)
        os.chmod(tmp_path, st.st_mode)
        os.replace(tmp_path, hosts_path)
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        return False, f"Ошибка записи {hosts_path}: {e}"

    flush_dns_cache()
    return True, "Блок обхода успешно удалён из /etc/hosts"

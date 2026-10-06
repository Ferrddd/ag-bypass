import os
import errno
import shutil
import subprocess
from pathlib import Path

TAG_BEGIN = "# BEGIN AG_BYPASS"
TAG_END = "# END AG_BYPASS"
DEFAULT_HOSTS_PATH = Path("/etc/hosts")
BACKUP_SUFFIX = ".ag_bak"


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


def create_backup(hosts_path: Path = DEFAULT_HOSTS_PATH) -> Path | None:
    """Create a backup of hosts file if backup does not already exist."""
    backup_path = hosts_path.with_name(hosts_path.name + BACKUP_SUFFIX)
    if not backup_path.exists() and hosts_path.exists():
        try:
            shutil.copy2(hosts_path, backup_path)
            return backup_path
        except Exception:
            pass
    return None


def restore_backup(hosts_path: Path = DEFAULT_HOSTS_PATH) -> tuple[bool, str]:
    """Restore hosts file from backup."""
    backup_path = hosts_path.with_name(hosts_path.name + BACKUP_SUFFIX)
    if not backup_path.is_file():
        return False, f"Бэкап не найден: {backup_path}"
    try:
        st = backup_path.stat()
        shutil.copy2(backup_path, hosts_path)
        try:
            os.chmod(hosts_path, st.st_mode)
            os.chown(hosts_path, st.st_uid, st.st_gid)
        except Exception:
            pass
        flush_dns_cache()
        return True, f"Файл hosts успешно восстановлен из {backup_path.name}"
    except Exception as e:
        return False, f"Ошибка восстановления hosts: {e}"


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


def _write_hosts_safely(hosts_path: Path, new_content: str) -> None:
    # 1. Create backup before modification if not present
    create_backup(hosts_path)

    # 2. Preserve permissions and ownership
    st = hosts_path.stat()
    mode = st.st_mode
    uid = st.st_uid
    gid = st.st_gid

    tmp_path = hosts_path.with_name(f"{hosts_path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(new_content, encoding="utf-8")
        try:
            os.chmod(tmp_path, mode)
            os.chown(tmp_path, uid, gid)
        except Exception:
            pass

        try:
            os.replace(tmp_path, hosts_path)
            return
        except OSError as e:
            # Fallback for EBUSY (Docker/bind-mount), EXDEV (cross-filesystem), or EPERM
            if e.errno in (errno.EBUSY, errno.EXDEV, errno.EPERM, errno.EACCES):
                with open(hosts_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                try:
                    os.chmod(hosts_path, mode)
                    os.chown(hosts_path, uid, gid)
                except Exception:
                    pass
                return
            raise
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


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

    cleaned = "\n".join(lines).rstrip()
    new_content = (cleaned + "\n\n" if cleaned else "") + "\n".join(block_lines) + "\n"

    try:
        _write_hosts_safely(hosts_path, new_content)
    except Exception as e:
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
    cleaned = "\n".join(lines).rstrip()
    new_content = (cleaned + "\n") if cleaned else ""

    try:
        _write_hosts_safely(hosts_path, new_content)
    except Exception as e:
        return False, f"Ошибка записи {hosts_path}: {e}"

    flush_dns_cache()
    return True, "Блок обхода успешно удалён из /etc/hosts"

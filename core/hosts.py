import errno
import os
import re
import shutil
import subprocess
from pathlib import Path

TAG_BEGIN, TAG_END = "# BEGIN AG_BYPASS", "# END AG_BYPASS"
DEFAULT_HOSTS_PATH, BACKUP_SUFFIX = Path("/etc/hosts"), ".ag_bak"


def flush_dns_cache() -> bool:
    cmd = ["resolvectl", "flush-caches"] if shutil.which("resolvectl") else (
        ["systemd-resolve", "--flush-caches"] if shutil.which("systemd-resolve") else None
    )
    return bool(cmd and subprocess.run(cmd, capture_output=True).returncode == 0)


def _apply_perms(path: Path, st: os.stat_result):
    try:
        os.chmod(path, st.st_mode)
        os.chown(path, st.st_uid, st.st_gid)
    except Exception:
        pass


def create_backup(hosts_path: Path = DEFAULT_HOSTS_PATH) -> Path | None:
    bak = hosts_path.with_name(hosts_path.name + BACKUP_SUFFIX)
    if not bak.exists() and hosts_path.exists():
        try:
            return shutil.copy2(hosts_path, bak)
        except Exception:
            pass
    return None


def restore_backup(hosts_path: Path = DEFAULT_HOSTS_PATH) -> tuple[bool, str]:
    bak = hosts_path.with_name(hosts_path.name + BACKUP_SUFFIX)
    if not bak.is_file():
        return False, f"Бэкап не найден: {bak}"
    try:
        shutil.copy2(bak, hosts_path)
        _apply_perms(hosts_path, bak.stat())
        flush_dns_cache()
        return True, f"Файл hosts успешно восстановлен из {bak.name}"
    except Exception as e:
        return False, f"Ошибка восстановления hosts: {e}"


def get_active_entries(hosts_path: Path = DEFAULT_HOSTS_PATH) -> list[tuple[str, str]]:
    if not hosts_path.is_file():
        return []
    try:
        m = re.search(rf"{TAG_BEGIN}\n?(.*?){TAG_END}", hosts_path.read_text(encoding="utf-8"), re.DOTALL)
        return [
            (p[0], p[1])
            for line in (m.group(1).splitlines() if m else [])
            if (p := line.strip().split()) and len(p) >= 2 and not p[0].startswith("#")
        ]
    except Exception:
        return []


def _write_safely(hosts_path: Path, content: str) -> None:
    create_backup(hosts_path)
    st = hosts_path.stat()
    tmp = hosts_path.with_name(f"{hosts_path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        _apply_perms(tmp, st)
        try:
            os.replace(tmp, hosts_path)
        except OSError as e:
            if e.errno in (errno.EBUSY, errno.EXDEV, errno.EPERM, errno.EACCES):
                hosts_path.write_text(content, encoding="utf-8")
                _apply_perms(hosts_path, st)
            else:
                raise
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def set_entries(entries: list[tuple[str, str]], hosts_path: Path = DEFAULT_HOSTS_PATH) -> tuple[bool, str]:
    if not hosts_path.is_file():
        return False, f"Файл не найден: {hosts_path}"
    try:
        clean = re.sub(rf"{TAG_BEGIN}.*?{TAG_END}\n?", "", hosts_path.read_text(encoding="utf-8"), flags=re.DOTALL).rstrip()
        seen = set()
        body = "\n".join(f"{ip} {d}" for ip, d in entries if not (d in seen or seen.add(d)))
        _write_safely(hosts_path, (clean + "\n\n" if clean else "") + f"{TAG_BEGIN}\n{body}\n{TAG_END}\n")
        flush_dns_cache()
        return True, "Записи успешно обновлены в /etc/hosts"
    except Exception as e:
        return False, f"Ошибка записи {hosts_path}: {e}"


def clear_entries(hosts_path: Path = DEFAULT_HOSTS_PATH) -> tuple[bool, str]:
    if not hosts_path.is_file():
        return False, f"Файл не найден: {hosts_path}"
    try:
        raw = hosts_path.read_text(encoding="utf-8")
        if TAG_BEGIN not in raw:
            return True, "Блок обхода отсутствует в /etc/hosts"
        clean = re.sub(rf"{TAG_BEGIN}.*?{TAG_END}\n?", "", raw, flags=re.DOTALL).rstrip()
        _write_safely(hosts_path, (clean + "\n") if clean else "")
        flush_dns_cache()
        return True, "Блок обхода успешно удалён из /etc/hosts"
    except Exception as e:
        return False, f"Ошибка записи {hosts_path}: {e}"

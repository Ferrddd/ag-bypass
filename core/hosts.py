import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

TAG_BEGIN, TAG_END = "# BEGIN AG_BYPASS", "# END AG_BYPASS"
BACKUP_SUFFIX = ".ag_bak"


def get_default_hosts_path() -> Path:
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    return Path(system_root) / "System32" / "drivers" / "etc" / "hosts"


DEFAULT_HOSTS_PATH = get_default_hosts_path()


def flush_dns_cache() -> bool:
    try:
        res = subprocess.run(["ipconfig", "/flushdns"], capture_output=True, text=True, check=False)
        return res.returncode == 0
    except Exception:
        return False


def _ensure_writable(path: Path):
    try:
        if path.exists():
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except Exception:
        pass


def create_backup(hosts_path: Path = DEFAULT_HOSTS_PATH) -> Path | None:
    bak = hosts_path.with_name(hosts_path.name + BACKUP_SUFFIX)
    if not bak.exists() and hosts_path.exists():
        try:
            _ensure_writable(bak)
            return shutil.copy2(hosts_path, bak)
        except Exception:
            pass
    return None


def restore_backup(hosts_path: Path = DEFAULT_HOSTS_PATH) -> tuple[bool, str]:
    bak = hosts_path.with_name(hosts_path.name + BACKUP_SUFFIX)
    if not bak.is_file():
        return False, f"Бэкап не найден: {bak}"
    try:
        _ensure_writable(hosts_path)
        shutil.copy2(bak, hosts_path)
        flush_dns_cache()
        return True, f"Файл hosts успешно восстановлен из {bak.name}"
    except Exception as e:
        return False, f"Ошибка восстановления hosts: {e}"


def get_active_entries(hosts_path: Path = DEFAULT_HOSTS_PATH) -> list[tuple[str, str]]:
    if not hosts_path.is_file():
        return []
    try:
        content = hosts_path.read_text(encoding="utf-8", errors="ignore")
        m = re.search(rf"{TAG_BEGIN}\n?(.*?){TAG_END}", content, re.DOTALL)
        return [
            (p[0], p[1])
            for line in (m.group(1).splitlines() if m else [])
            if (p := line.strip().split()) and len(p) >= 2 and not p[0].startswith("#")
        ]
    except Exception:
        return []


def _write_safely(hosts_path: Path, content: str) -> None:
    create_backup(hosts_path)
    _ensure_writable(hosts_path)
    tmp = hosts_path.with_name(f"{hosts_path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        try:
            os.replace(tmp, hosts_path)
        except OSError:
            hosts_path.write_text(content, encoding="utf-8")
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
        raw = hosts_path.read_text(encoding="utf-8", errors="ignore")
        clean = re.sub(rf"{TAG_BEGIN}.*?{TAG_END}\n?", "", raw, flags=re.DOTALL).rstrip()
        seen = set()
        body = "\n".join(f"{ip} {d}" for ip, d in entries if not (d in seen or seen.add(d)))
        _write_safely(hosts_path, (clean + "\n\n" if clean else "") + f"{TAG_BEGIN}\n{body}\n{TAG_END}\n")
        flush_dns_cache()
        return True, f"Записи успешно обновлены в {hosts_path}"
    except Exception as e:
        return False, f"Ошибка записи {hosts_path}: {e}"


def clear_entries(hosts_path: Path = DEFAULT_HOSTS_PATH) -> tuple[bool, str]:
    if not hosts_path.is_file():
        return False, f"Файл не найден: {hosts_path}"
    try:
        raw = hosts_path.read_text(encoding="utf-8", errors="ignore")
        if TAG_BEGIN not in raw:
            return True, "Блок обхода отсутствует в файле hosts"
        clean = re.sub(rf"{TAG_BEGIN}.*?{TAG_END}\n?", "", raw, flags=re.DOTALL).rstrip()
        _write_safely(hosts_path, (clean + "\n") if clean else "")
        flush_dns_cache()
        return True, "Блок обхода успешно удалён из hosts"
    except Exception as e:
        return False, f"Ошибка записи {hosts_path}: {e}"

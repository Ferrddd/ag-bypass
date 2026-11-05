import errno
import os
import pwd
import re
import shutil
import subprocess
from pathlib import Path

# =====================================================================
# Context & Permissions
# =====================================================================

def is_admin() -> bool:
    try:
        return os.geteuid() == 0
    except Exception:
        return False


def user_home() -> Path:
    if u := os.environ.get("SUDO_USER"):
        try:
            return Path(pwd.getpwnam(u).pw_dir)
        except Exception:
            pass
    return Path.home()


def _apply_perms(path: Path, st: os.stat_result):
    try:
        os.chmod(path, st.st_mode & 0o7777)
        os.chown(path, st.st_uid, st.st_gid)
    except Exception:
        pass


# =====================================================================
# /etc/hosts & DNS Cache
# =====================================================================

TAG_BEGIN, TAG_END = "# BEGIN AG_BYPASS", "# END AG_BYPASS"
DEFAULT_HOSTS_PATH = Path("/etc/hosts")


def flush_dns_cache() -> bool:
    cmd = ["resolvectl", "flush-caches"] if shutil.which("resolvectl") else (
        ["systemd-resolve", "--flush-caches"] if shutil.which("systemd-resolve") else None
    )
    if not cmd:
        return False
    try:
        return subprocess.run(cmd, capture_output=True, timeout=3).returncode == 0
    except Exception:
        return False


def get_active_entries(hosts_path: Path = DEFAULT_HOSTS_PATH) -> list[tuple[str, str]]:
    if not hosts_path.is_file():
        return []
    try:
        m = re.search(rf"{TAG_BEGIN}\n?(.*?){TAG_END}", hosts_path.read_text(encoding="utf-8"), re.DOTALL)
        entries = []
        for line in (m.group(1).splitlines() if m else []):
            parts = line.strip().split()
            if parts and not parts[0].startswith("#") and len(parts) >= 2:
                ip = parts[0]
                for d in parts[1:]:
                    if d.startswith("#"):
                        break
                    entries.append((ip, d))
        return entries
    except Exception:
        return []


def _write_hosts(hosts_path: Path, content: str):
    real_path = hosts_path.resolve()
    bak = hosts_path.with_name(hosts_path.name + ".ag_bak")
    if not bak.exists() and real_path.exists():
        try:
            shutil.copy2(real_path, bak)
        except Exception:
            pass
    st = real_path.stat()
    tmp = real_path.with_name(f"{real_path.name}.{os.getpid()}.tmp")
    try:
        try:
            tmp.write_text(content, encoding="utf-8")
            _apply_perms(tmp, st)
            os.replace(tmp, real_path)
        except OSError as e:
            if e.errno in (errno.EBUSY, errno.EXDEV, errno.EPERM, errno.EACCES, errno.EROFS):
                real_path.write_text(content, encoding="utf-8")
                _apply_perms(real_path, st)
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
    if not entries:
        return clear_entries(hosts_path)
    try:
        raw = hosts_path.read_text(encoding="utf-8")
        clean = re.sub(rf"{TAG_BEGIN}.*?{TAG_END}\n?", "", raw, flags=re.DOTALL)
        if TAG_BEGIN in clean:
            clean = re.sub(rf"{TAG_BEGIN}.*$", "", clean, flags=re.DOTALL)
        if TAG_END in clean:
            clean = clean.replace(TAG_END, "")
        clean = clean.rstrip()
        seen = set()
        clean_entries = []
        for ip, d in entries:
            clean_ip = str(ip).strip().split()[0] if str(ip).strip() else ""
            clean_d = str(d).strip().split()[0] if str(d).strip() else ""
            if clean_ip and clean_d and clean_d not in seen:
                seen.add(clean_d)
                clean_entries.append(f"{clean_ip} {clean_d}")
        if not clean_entries:
            return clear_entries(hosts_path)
        body = "\n".join(clean_entries)
        _write_hosts(hosts_path, (clean + "\n\n" if clean else "") + f"{TAG_BEGIN}\n{body}\n{TAG_END}\n")
        flush_dns_cache()
        return True, "Записи успешно обновлены в /etc/hosts"
    except Exception as e:
        return False, f"Ошибка записи {hosts_path}: {e}"


def clear_entries(hosts_path: Path = DEFAULT_HOSTS_PATH) -> tuple[bool, str]:
    if not hosts_path.is_file():
        return False, f"Файл не найден: {hosts_path}"
    try:
        raw = hosts_path.read_text(encoding="utf-8")
        if TAG_BEGIN not in raw and TAG_END not in raw:
            return True, "Блок обхода отсутствует в /etc/hosts"
        clean = re.sub(rf"{TAG_BEGIN}.*?{TAG_END}\n?", "", raw, flags=re.DOTALL)
        if TAG_BEGIN in clean:
            clean = re.sub(rf"{TAG_BEGIN}.*$", "", clean, flags=re.DOTALL)
        if TAG_END in clean:
            clean = clean.replace(TAG_END, "")
        clean = clean.rstrip()
        _write_hosts(hosts_path, (clean + "\n") if clean else "")
        flush_dns_cache()
        return True, "Блок обхода успешно удалён из /etc/hosts"
    except Exception as e:
        return False, f"Ошибка записи {hosts_path}: {e}"


# =====================================================================
# CLOUD_CODE_URL Environment Files
# =====================================================================

_SYS_ENV = Path("/etc/environment.d/10-antigravity.conf")
_SYS_PROF = Path("/etc/profile.d/antigravity.sh")


def _user_env_file() -> Path:
    return user_home() / ".config/environment.d/10-antigravity.conf"


def apply_endpoint(url: str) -> list[str]:
    out, uf = [], _user_env_file()
    clean_url = url.strip().splitlines()[0] if url else ""
    safe_url = re.sub(r'["`$\\;]', '', clean_url)
    try:
        uf.parent.mkdir(parents=True, exist_ok=True)
        uf.write_text(f"CLOUD_CODE_URL={safe_url}\n")
        if u := os.environ.get("SUDO_USER"):
            try:
                pw = pwd.getpwnam(u)
                p = uf.parent
                h = user_home()
                while p != h and p != p.parent:
                    os.chown(p, pw.pw_uid, pw.pw_gid)
                    p = p.parent
                os.chown(uf, pw.pw_uid, pw.pw_gid)
            except Exception:
                pass
        out.append(str(uf))
    except Exception:
        pass

    if os.geteuid() == 0:
        for t, text in [(_SYS_ENV, f"CLOUD_CODE_URL={safe_url}\n"), (_SYS_PROF, f'export CLOUD_CODE_URL="{safe_url}"\n')]:
            if t.parent.exists():
                try:
                    t.write_text(text)
                    if t.suffix == ".sh":
                        t.chmod(0o755)
                    out.append(str(t))
                except Exception:
                    pass
    return out


def remove_endpoint() -> list[str]:
    targets = [_user_env_file()] + ([_SYS_ENV, _SYS_PROF] if os.geteuid() == 0 else [])
    removed = []
    for p in targets:
        if p.is_file():
            try:
                p.unlink()
                removed.append(str(p))
            except Exception:
                pass
    return removed


def get_endpoint_status() -> tuple[bool, str]:
    if v := os.environ.get("CLOUD_CODE_URL"):
        if v.strip():
            return True, f"В текущей сессии: {v.strip()}"
    for p, lbl in [(_user_env_file(), "~/.config/environment.d"), (_SYS_ENV, "/etc/environment.d")]:
        if p.is_file():
            try:
                for line in p.read_text().splitlines():
                    if line.startswith("CLOUD_CODE_URL="):
                        val = line.split("=", 1)[1].strip("\"' ")
                        if val:
                            return True, f"В {lbl}: {val}"
            except Exception:
                pass
    if _SYS_PROF.is_file():
        try:
            for line in _SYS_PROF.read_text().splitlines():
                if "CLOUD_CODE_URL=" in line:
                    val = line.split("CLOUD_CODE_URL=", 1)[1].strip("\"' ")
                    if val:
                        return True, f"В /etc/profile.d: {val}"
        except Exception:
            pass
    return False, "Не настроен (клиент будет использовать дефолтный адрес)"


# =====================================================================
# Binary Patcher (agy ineligible -> inexigible)
# =====================================================================

SIG_ORIGINAL, SIG_PATCHED = b"ineligible", b"inexigible"


def find_binary(configured: str | None = "auto") -> Path | None:
    home = user_home()
    if configured and configured != "auto":
        if configured.startswith("~/"):
            p = (home / configured[2:]).resolve()
        elif configured == "~":
            p = home.resolve()
        else:
            p = Path(os.path.expanduser(configured)).resolve()
        return p if p.is_file() else None

    cands = [
        home / ".local/bin/agy", home / ".local/share/agy/bin/agy",
        Path.home() / ".local/bin/agy", Path.home() / ".local/share/agy/bin/agy",
        Path("/usr/local/bin/agy"), Path("/usr/bin/agy"), Path("/opt/antigravity/bin/agy"), Path("/opt/agy/agy"),
    ] + ([Path(w)] if (w := shutil.which("agy")) else [])

    for c in cands:
        try:
            if (p := c.resolve()).is_file():
                return p
        except Exception:
            pass
    return None


def get_binary_status(bin_path: Path) -> str:
    real_bin = bin_path.resolve()
    if not real_bin.is_file():
        return "FILE_NOT_FOUND"
    try:
        d = real_bin.read_bytes()
        return "ALREADY_PATCHED" if SIG_PATCHED in d else ("READY_TO_PATCH" if SIG_ORIGINAL in d else "SIGNATURE_NOT_FOUND")
    except Exception as e:
        return f"ERROR: {e}"


def apply_patch(bin_path: Path) -> tuple[bool, str]:
    real_bin = bin_path.resolve()
    if not real_bin.is_file():
        return False, f"Файл не найден: {bin_path}"
    try:
        d = real_bin.read_bytes()
    except Exception as e:
        return False, f"Ошибка чтения {real_bin}: {e}"
    if SIG_PATCHED in d:
        return True, "Бинарник уже пропатчен"
    if SIG_ORIGINAL not in d:
        return False, "Сигнатура 'ineligible' не найдена в бинарнике"

    st = real_bin.stat()
    bak = real_bin.with_name(real_bin.name + ".bak")
    if not bak.exists():
        try:
            shutil.copy2(real_bin, bak)
            _apply_perms(bak, st)
        except Exception as e:
            return False, f"Не удалось создать бэкап {bak}: {e}"

    tmp = real_bin.with_name(f"{real_bin.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(d.replace(SIG_ORIGINAL, SIG_PATCHED))
        _apply_perms(tmp, st)
        os.replace(tmp, real_bin)
        return True, f"Бинарник успешно пропатчен (бэкап сохранён в {bak.name})"
    except Exception as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        return False, f"Ошибка записи: {e}"


def restore_backup(bin_path: Path) -> tuple[bool, str]:
    real_bin = bin_path.resolve()
    bak = real_bin.with_name(real_bin.name + ".bak")
    if not bak.is_file():
        return False, f"Бэкап не найден: {bak}"
    st = bak.stat()
    tmp = real_bin.with_name(f"{real_bin.name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(bak, tmp)
        _apply_perms(tmp, st)
        os.replace(tmp, real_bin)
        try:
            bak.unlink()
        except Exception:
            pass
        return True, f"Оригинальный бинарник восстановлен из {bak.name}"
    except Exception as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        return False, f"Ошибка восстановления бэкапа: {e}"


# =====================================================================
# Background Timer
# =====================================================================

def is_timer_active() -> bool:
    try:
        res = subprocess.run(
            ["systemctl", "is-active", "ag-check.timer"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return res.stdout.strip() == "active"
    except Exception:
        return False

"""Antigravity System Module for Windows"""
import errno
import os
import re
import shutil
import subprocess
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None

# =====================================================================
# Constants & Paths
# =====================================================================

TAG_BEGIN, TAG_END = "# BEGIN AG_BYPASS", "# END AG_BYPASS"

_sys_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
DEFAULT_HOSTS_PATH = Path(_sys_root) / "System32" / "drivers" / "etc" / "hosts"

SIG_ORIGINAL, SIG_PATCHED = b"ineligible", b"inexigible"


# =====================================================================
# Context & Permissions
# =====================================================================

user_home = Path.home


def is_admin() -> bool:
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


# =====================================================================
# Windows Hosts & DNS Cache
# =====================================================================

def flush_dns_cache() -> bool:
    try:
        return subprocess.run(
            ["ipconfig", "/flushdns"],
            capture_output=True,
            timeout=5,
        ).returncode == 0
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
    tmp = real_path.with_name(f"{real_path.name}.{os.getpid()}.tmp")
    try:
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, real_path)
        except OSError:
            real_path.write_text(content, encoding="utf-8")
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
        return True, "Записи успешно обновлены в hosts"
    except Exception as e:
        return False, f"Ошибка записи {hosts_path}: {e}"


def clear_entries(hosts_path: Path = DEFAULT_HOSTS_PATH) -> tuple[bool, str]:
    if not hosts_path.is_file():
        return False, f"Файл не найден: {hosts_path}"
    try:
        raw = hosts_path.read_text(encoding="utf-8")
        if TAG_BEGIN not in raw and TAG_END not in raw:
            return True, "Блок обхода отсутствует в hosts"
        clean = re.sub(rf"{TAG_BEGIN}.*?{TAG_END}\n?", "", raw, flags=re.DOTALL)
        if TAG_BEGIN in clean:
            clean = re.sub(rf"{TAG_BEGIN}.*$", "", clean, flags=re.DOTALL)
        if TAG_END in clean:
            clean = clean.replace(TAG_END, "")
        clean = clean.rstrip()
        _write_hosts(hosts_path, (clean + "\n") if clean else "")
        flush_dns_cache()
        return True, "Блок обхода успешно удалён из hosts"
    except Exception as e:
        return False, f"Ошибка записи {hosts_path}: {e}"


# =====================================================================
# CLOUD_CODE_URL Environment (Registry & setx)
# =====================================================================

def apply_endpoint(url: str) -> list[str]:
    clean_url = url.strip().splitlines()[0] if url else ""
    safe_url = re.sub(r'["`$\\;]', '', clean_url)
    os.environ["CLOUD_CODE_URL"] = safe_url
    applied = []

    reg_success = False
    if winreg is not None:
        try:
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE)
            except FileNotFoundError:
                key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Environment")
            with key:
                winreg.SetValueEx(key, "CLOUD_CODE_URL", 0, winreg.REG_SZ, safe_url)
                reg_success = True
                applied.append(r"HKCU\Environment:CLOUD_CODE_URL")
        except Exception:
            pass

    if not reg_success:
        try:
            res = subprocess.run(["setx", "CLOUD_CODE_URL", safe_url], capture_output=True, timeout=5)
            if res.returncode == 0:
                applied.append(r"HKCU\Environment:CLOUD_CODE_URL (setx)")
        except Exception:
            pass
    return applied


def remove_endpoint() -> list[str]:
    removed = []
    if "CLOUD_CODE_URL" in os.environ:
        del os.environ["CLOUD_CODE_URL"]
        removed.append("os.environ:CLOUD_CODE_URL")

    reg_deleted = False
    if winreg is not None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE) as key:
                try:
                    winreg.DeleteValue(key, "CLOUD_CODE_URL")
                    reg_deleted = True
                    removed.append(r"HKCU\Environment:CLOUD_CODE_URL")
                except FileNotFoundError:
                    reg_deleted = True
        except FileNotFoundError:
            reg_deleted = True
        except Exception:
            pass

    if not reg_deleted:
        try:
            res = subprocess.run(
                ["reg", "delete", r"HKCU\Environment", "/v", "CLOUD_CODE_URL", "/f"],
                capture_output=True,
                timeout=5,
            )
            if res.returncode == 0:
                removed.append(r"HKCU\Environment:CLOUD_CODE_URL (reg delete)")
        except Exception:
            pass
    return removed


def get_endpoint_status() -> tuple[bool, str]:
    if v := os.environ.get("CLOUD_CODE_URL"):
        if v.strip():
            return True, f"В текущей сессии: {v.strip()}"
    if winreg is not None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_QUERY_VALUE) as key:
                val, _ = winreg.QueryValueEx(key, "CLOUD_CODE_URL")
                if val and str(val).strip():
                    return True, f"В реестре (HKCU\\Environment): {str(val).strip()}"
        except Exception:
            pass
    try:
        res = subprocess.run(
            ["reg", "query", r"HKCU\Environment", "/v", "CLOUD_CODE_URL"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if "CLOUD_CODE_URL" in line and "REG_SZ" in line:
                    val = line.split("REG_SZ", 1)[1].strip()
                    if val:
                        return True, f"В реестре (HKCU\\Environment): {val}"
    except Exception:
        pass
    return False, "Не настроен (клиент будет использовать дефолтный адрес)"


# =====================================================================
# Binary Finder & Patcher (agy.exe ineligible -> inexigible)
# =====================================================================

def find_binary(configured: str | None = "auto") -> Path | None:
    if configured and configured != "auto":
        expanded = os.path.expandvars(os.path.expanduser(configured))
        p = Path(expanded).resolve()
        return p if p.is_file() else None

    cands = []
    local_appdata = os.environ.get("LOCALAPPDATA")
    appdata = os.environ.get("APPDATA")
    prog_files = os.environ.get("ProgramFiles")
    prog_files_x86 = os.environ.get("ProgramFiles(x86)")

    if local_appdata:
        cands.append(Path(local_appdata) / "Programs" / "antigravity" / "agy.exe")
        cands.append(Path(local_appdata) / "antigravity" / "agy.exe")
    if appdata:
        cands.append(Path(appdata) / "antigravity" / "agy.exe")
    if prog_files:
        cands.append(Path(prog_files) / "Antigravity" / "agy.exe")
    if prog_files_x86:
        cands.append(Path(prog_files_x86) / "Antigravity" / "agy.exe")

    for name in ("agy.exe", "antigravity.exe", "agy"):
        if w := shutil.which(name):
            cands.append(Path(w))

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

    bak = real_bin.with_name(real_bin.name + ".bak")
    if not bak.exists():
        try:
            shutil.copy2(real_bin, bak)
        except Exception as e:
            return False, f"Не удалось создать бэкап {bak}: {e}"

    tmp = real_bin.with_name(f"{real_bin.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(d.replace(SIG_ORIGINAL, SIG_PATCHED))
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
    tmp = real_bin.with_name(f"{real_bin.name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(bak, tmp)
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
# Background Timer (Task Scheduler)
# =====================================================================

def is_timer_active() -> bool:
    try:
        return subprocess.run(
            ["schtasks", "/query", "/tn", "AntigravityBypassCheck"],
            capture_output=True,
            timeout=5,
        ).returncode == 0
    except Exception:
        return False

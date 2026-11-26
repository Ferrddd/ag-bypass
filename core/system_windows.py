"""Antigravity System Module for Windows"""
import mmap
import os
import re
import shutil
import subprocess
import sys
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


def _strip_hosts_block(raw: str) -> str:
    clean = re.sub(rf"{TAG_BEGIN}.*?{TAG_END}\n?", "", raw, flags=re.DOTALL)
    if TAG_BEGIN in clean:
        clean = re.sub(rf"{TAG_BEGIN}.*$", "", clean, flags=re.DOTALL)
    if TAG_END in clean:
        clean = clean.replace(TAG_END, "")
    return clean.rstrip()


def set_entries(entries: list[tuple[str, str]], hosts_path: Path = DEFAULT_HOSTS_PATH) -> tuple[bool, str]:
    if not hosts_path.is_file():
        return False, f"Файл не найден: {hosts_path}"
    if not entries:
        return clear_entries(hosts_path)
    try:
        clean = _strip_hosts_block(hosts_path.read_text(encoding="utf-8"))
        seen = set()
        clean_entries = []
        for ip, d in entries:
            clean_ip = str(ip).strip().split()[0] if str(ip).strip() else ""
            clean_d = str(d).strip().split()[0] if str(d).strip() else ""
            key = (clean_ip, clean_d)
            if clean_ip and clean_d and key not in seen:
                seen.add(key)
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
        clean = _strip_hosts_block(raw)
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
# Binary Finder & Patcher (Antigravity.exe / agy.exe ineligible -> inexigible)
# =====================================================================

def _win_dir_candidates(base_dir: Path) -> list[Path]:
    """Return candidate Antigravity executables inside an installation directory."""
    return [
        base_dir / "resources" / "bin" / "language_server.exe",
        base_dir / "resources" / "app" / "extensions" / "antigravity" / "bin" / "language_server_windows_x64.exe",
        base_dir / "language_server.exe",
        base_dir / "Antigravity.exe",
        base_dir / "antigravity.exe",
        base_dir / "agy.exe",
    ]


def _registry_install_dirs() -> list[Path]:
    """Find Antigravity installation directories from Windows Uninstall registry keys."""
    if winreg is None:
        return []
    dirs: list[Path] = []
    roots = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, subkey in roots:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(key, i)
                        i += 1
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(key, name) as app_key:
                            disp = ""
                            try:
                                disp, _ = winreg.QueryValueEx(app_key, "DisplayName")
                            except OSError:
                                pass
                            if "antigravity" not in name.lower() and "antigravity" not in str(disp).lower():
                                continue
                            for val_name in ("InstallLocation", "DisplayIcon"):
                                try:
                                    raw_val, _ = winreg.QueryValueEx(app_key, val_name)
                                    clean = str(raw_val).split(",", 1)[0].strip().strip('"')
                                    if clean:
                                        p = Path(clean)
                                        dirs.append(p.parent if p.suffix.lower() == ".exe" else p)
                                except OSError:
                                    pass
                    except OSError:
                        pass
        except OSError:
            pass
    return dirs


def _add_unique_file(found: list[Path], seen: set[Path], path: Path) -> None:
    try:
        rp = path.resolve()
        if rp.is_file() and rp not in seen:
            seen.add(rp)
            found.append(rp)
    except Exception:
        pass


def _first_existing_file(candidates: list[Path]) -> Path | None:
    for c in candidates:
        try:
            if (p := c.resolve()).is_file():
                return p
        except Exception:
            pass
    return None


def find_binaries(configured: str | None = "auto") -> list[Path]:
    """Discover all matching CLI and GUI Antigravity binaries on Windows."""
    if configured and configured != "auto":
        clean = configured.strip().strip('"').strip("'")
        expanded = os.path.expandvars(os.path.expanduser(clean))
        p = Path(expanded).resolve()
        if p.is_file():
            return [p]
        if p.is_dir():
            if found := _first_existing_file(_win_dir_candidates(p) + [p / "Antigravity", p / "antigravity", p / "agy"]):
                return [found]
        return []

    local_appdata = os.environ.get("LOCALAPPDATA")
    appdata = os.environ.get("APPDATA")
    prog_files = os.environ.get("ProgramFiles")
    prog_files_x86 = os.environ.get("ProgramFiles(x86)")
    userprofile = os.environ.get("USERPROFILE")

    candidate_dirs = []
    for base in [p for p in (local_appdata, appdata) if p]:
        bp = Path(base)
        candidate_dirs.extend([
            bp / "Programs" / "antigravity",
            bp / "Programs" / "Antigravity",
            bp / "Programs" / "Antigravity IDE",
            bp / "antigravity",
            bp / "Antigravity",
            bp / "Antigravity IDE",
        ])
    for pf in [p for p in (prog_files, prog_files_x86) if p]:
        pfp = Path(pf)
        candidate_dirs.extend([
            pfp / "Antigravity",
            pfp / "antigravity",
            pfp / "Antigravity IDE",
            pfp / "Programs" / "antigravity",
            pfp / "Programs" / "Antigravity",
        ])
    candidate_dirs.extend(_registry_install_dirs())

    found: list[Path] = []
    seen: set[Path] = set()
    seen_dirs: set[Path] = set()

    if userprofile:
        up = Path(userprofile)
        for cli_cand in (up / ".antigravity" / "bin" / "agy.exe", up / ".local" / "bin" / "agy.exe"):
            _add_unique_file(found, seen, cli_cand)

    for d in candidate_dirs:
        try:
            rd = d.resolve()
        except Exception:
            rd = d
        if rd not in seen_dirs:
            seen_dirs.add(rd)
            if bp := _first_existing_file(_win_dir_candidates(rd)):
                _add_unique_file(found, seen, bp)

    for name in ("language_server.exe", "Antigravity.exe", "antigravity.exe", "agy.exe", "Antigravity", "antigravity", "agy"):
        if w := shutil.which(name):
            wp = Path(w)
            try:
                rwp = wp.resolve()
                ls = _first_existing_file([
                    rwp.parent / "resources" / "bin" / "language_server.exe",
                    rwp.parent.parent / "resources" / "bin" / "language_server.exe",
                    rwp.parent / "resources" / "app" / "extensions" / "antigravity" / "bin" / "language_server_windows_x64.exe",
                ])
                _add_unique_file(found, seen, ls if ls else rwp)
            except Exception:
                pass

    # Fallback for SYSTEM / elevated service accounts: scan user profiles
    if local_appdata and "systemprofile" in local_appdata.lower():
        try:
            sys_drive = os.environ.get("SystemDrive") or "C:"
            users_root = Path(sys_drive) / "Users"
            if users_root.is_dir():
                for u in sorted(users_root.iterdir()):
                    if u.is_dir() and u.name.lower() not in ("default", "public", "all users", "default user"):
                        for cli_cand in (u / ".antigravity" / "bin" / "agy.exe", u / ".local" / "bin" / "agy.exe"):
                            _add_unique_file(found, seen, cli_cand)
                        for base in (
                            u / "AppData" / "Local" / "Programs" / "antigravity",
                            u / "AppData" / "Local" / "Programs" / "Antigravity",
                            u / "AppData" / "Local" / "Programs" / "Antigravity IDE",
                            u / "AppData" / "Local" / "antigravity",
                            u / "AppData" / "Local" / "Antigravity",
                        ):
                            if bp := _first_existing_file(_win_dir_candidates(base)):
                                _add_unique_file(found, seen, bp)
        except Exception:
            pass

    return found


def find_binary(configured: str | None = "auto") -> Path | None:
    bins = find_binaries(configured)
    return bins[0] if bins else None


def get_binary_status(bin_path: Path) -> str:
    real_bin = bin_path.resolve()
    if not real_bin.is_file():
        return "FILE_NOT_FOUND"
    try:
        if real_bin.stat().st_size == 0:
            return "SIGNATURE_NOT_FOUND"
        with real_bin.open("rb") as f:
            try:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    if mm.find(SIG_PATCHED) != -1:
                        return "ALREADY_PATCHED"
                    return "READY_TO_PATCH" if mm.find(SIG_ORIGINAL) != -1 else "SIGNATURE_NOT_FOUND"
            except (ValueError, OSError):
                d = f.read()
                return "ALREADY_PATCHED" if SIG_PATCHED in d else ("READY_TO_PATCH" if SIG_ORIGINAL in d else "SIGNATURE_NOT_FOUND")
    except Exception as e:
        return f"ERROR: {e}"


def _stream_replace_to_tmp(src: Path, tmp: Path, old: bytes, new: bytes, chunk_size: int = 4 * 1024 * 1024) -> None:
    overlap = len(old) - 1
    tail = b""
    with src.open("rb") as fin, tmp.open("wb") as fout:
        while chunk := fin.read(chunk_size):
            replaced = (tail + chunk).replace(old, new)
            if overlap > 0 and len(replaced) > overlap:
                fout.write(replaced[:-overlap])
                tail = replaced[-overlap:]
            else:
                tail = replaced
        if tail:
            fout.write(tail)


def _patch_binary(bin_path: Path, old: bytes, new: bytes, apply_mode: bool) -> tuple[bool, str]:
    real_bin = bin_path.resolve()
    status = get_binary_status(real_bin)
    if status == "FILE_NOT_FOUND":
        return False, f"Файл не найден: {bin_path}"
    if status.startswith("ERROR: "):
        return False, f"Ошибка чтения {real_bin}: {status[7:]}"
    if apply_mode:
        if status == "ALREADY_PATCHED":
            return True, "Бинарник уже пропатчен"
        if status != "READY_TO_PATCH":
            return False, "Сигнатура 'ineligible' не найдена в бинарнике"
    elif status != "ALREADY_PATCHED":
        return False, "Патч не обнаружен в бинарнике"

    tmp = real_bin.with_name(f"{real_bin.name}.{os.getpid()}.tmp")
    try:
        _stream_replace_to_tmp(real_bin, tmp, old, new)
        os.replace(tmp, real_bin)
        return True, ("Бинарник успешно пропатчен" if apply_mode else "Патч успешно откачен")
    except Exception as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        if isinstance(e, PermissionError) or getattr(e, "winerror", None) in (5, 32):
            return False, (
                f"Файл заблокирован или недостаточно прав ({e}). "
                "Закройте Antigravity (и процесс language_server.exe) и повторите от имени Администратора."
            )
        return False, (f"Ошибка записи: {e}" if apply_mode else f"Ошибка отката патча: {e}")


def apply_patch(bin_path: Path) -> tuple[bool, str]:
    return _patch_binary(bin_path, SIG_ORIGINAL, SIG_PATCHED, apply_mode=True)


def restore_backup(bin_path: Path) -> tuple[bool, str]:
    return _patch_binary(bin_path, SIG_PATCHED, SIG_ORIGINAL, apply_mode=False)


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


# =====================================================================
# Windows Install / Uninstall (used by install.bat / uninstall.bat)
# =====================================================================

def _notify_env_changed() -> None:
    try:
        import ctypes
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 1000, None)
    except Exception:
        pass


def update_user_path(entry: Path, remove: bool = False) -> tuple[bool, str]:
    """Add or remove a directory from HKCU\\Environment\\Path."""
    target = os.path.normpath(str(entry))
    norm_target = os.path.normcase(target)

    if winreg is not None:
        try:
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Environment",
                    0,
                    winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
                )
            except FileNotFoundError:
                if remove:
                    return True, f"Каталог {target} отсутствует в User Path"
                key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Environment")

            with key:
                reg_expand_sz = getattr(winreg, "REG_EXPAND_SZ", winreg.REG_SZ)
                try:
                    raw_val, val_type = winreg.QueryValueEx(key, "Path")
                except FileNotFoundError:
                    raw_val, val_type = "", reg_expand_sz

                parts = [p for p in str(raw_val).split(";") if p.strip()]
                filtered = [p for p in parts if os.path.normcase(os.path.normpath(p.strip())) != norm_target]

                if remove:
                    if len(filtered) == len(parts):
                        return True, f"Каталог {target} отсутствует в User Path"
                    new_val = ";".join(filtered)
                else:
                    if len(filtered) < len(parts):
                        return True, f"Каталог {target} уже присутствует в User Path"
                    new_val = ";".join(parts + [target])

                reg_type = val_type if val_type in (winreg.REG_SZ, reg_expand_sz) else reg_expand_sz
                winreg.SetValueEx(key, "Path", 0, reg_type, new_val)
            _notify_env_changed()
            verb = "удален из" if remove else "добавлен в"
            return True, f"Каталог {target} {verb} User Path"
        except Exception as e:
            return False, f"Ошибка обновления User Path: {e}"

    return False, "Модуль winreg недоступен"


def install_windows(app_dir: Path, interval_minutes: int = 2) -> tuple[bool, list[str]]:
    """Configure Windows Task Scheduler failover job and User Path."""
    msgs: list[str] = []
    ag_path = (app_dir / "ag").resolve()
    py_exe = sys.executable or "python"
    interval = max(1, int(interval_minutes or 2))
    task_cmd = f'"{py_exe}" "{ag_path}" check --quiet'

    try:
        res = subprocess.run(
            ["schtasks", "/create", "/tn", "AntigravityBypassCheck", "/tr", task_cmd, "/sc", "minute", "/mo", str(interval), "/f"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "").strip()
            msgs.append(f"[!] Ошибка создания задачи в schtasks: {err or res.returncode}")
            return False, msgs
        msgs.append(f"[✓] Задача 'AntigravityBypassCheck' создана в Планировщике задач (интервал: {interval} мин).")
    except Exception as e:
        msgs.append(f"[!] Ошибка запуска schtasks: {e}")
        return False, msgs

    ok_path, path_msg = update_user_path(app_dir, remove=False)
    msgs.append(f"[{'✓' if ok_path else 'WARN'}] {path_msg}.")
    return True, msgs


def uninstall_windows(app_dir: Path, configured_binary_path: str = "auto") -> tuple[bool, list[str]]:
    """Remove hosts entries, revert binary patches, delete Task Scheduler job, and clean Path/registry."""
    msgs: list[str] = []
    ok_hosts, hosts_msg = clear_entries()
    msgs.append(f"[{'✓' if ok_hosts else 'ERR'}] {hosts_msg}")

    for bp in find_binaries(configured_binary_path):
        if get_binary_status(bp) == "ALREADY_PATCHED":
            ok_unp, unp_msg = restore_backup(bp)
            msgs.append(f"[{'✓' if ok_unp else 'WARN'}] {bp}: {unp_msg}")

    try:
        res = subprocess.run(
            ["schtasks", "/delete", "/tn", "AntigravityBypassCheck", "/f"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if res.returncode == 0:
            msgs.append("[✓] Задача 'AntigravityBypassCheck' удалена из Планировщика задач.")
        else:
            msgs.append("[i] Задача 'AntigravityBypassCheck' не найдена в Планировщике.")
    except Exception as e:
        msgs.append(f"[WARN] Ошибка удаления задачи schtasks: {e}")

    rm_ep = remove_endpoint()
    if rm_ep:
        msgs.append(f"[✓] Переменная CLOUD_CODE_URL удалена ({', '.join(rm_ep)}).")

    ok_path, path_msg = update_user_path(app_dir, remove=True)
    msgs.append(f"[{'✓' if ok_path else 'WARN'}] {path_msg}.")
    return ok_hosts, msgs

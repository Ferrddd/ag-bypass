"""Antigravity System Module for Windows"""
import csv
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


def _run_hidden(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess without flashing a console window on Windows."""
    if flags := getattr(subprocess, "CREATE_NO_WINDOW", 0):
        kwargs.setdefault("creationflags", flags)
    return subprocess.run(cmd, **kwargs)


def _resolve_pythonw(executable: str | None = None) -> str:
    """Resolve pythonw.exe next to executable (or on PATH) to avoid console popups."""
    py_exe = executable or sys.executable or "python"
    p = Path(py_exe)
    lower_name = p.name.lower()
    if lower_name.startswith("pythonw"):
        return py_exe
    candidates = [p.with_name("pythonw.exe")]
    if lower_name.startswith("python"):
        candidates.append(p.with_name("pythonw" + p.name[6:]))
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    if which_pw := shutil.which("pythonw"):
        return which_pw
    return py_exe


# =====================================================================
# Windows Hosts & DNS Cache
# =====================================================================

def flush_dns_cache() -> bool:
    try:
        return _run_hidden(
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
            res = _run_hidden(["setx", "CLOUD_CODE_URL", safe_url], capture_output=True, timeout=5)
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
            res = _run_hidden(
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
        res = _run_hidden(
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

_TASK_NAME = "AntigravityBypassCheck"


def _task_state(task_name: str = _TASK_NAME) -> tuple[bool, bool]:
    """Return (exists, enabled) for a scheduled task via locale-independent XML output."""
    try:
        res = _run_hidden(
            ["schtasks", "/query", "/tn", task_name, "/xml"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode != 0:
            return False, False
        xml_text = res.stdout if isinstance(res.stdout, str) else ""
        enabled = "<enabled>false</enabled>" not in xml_text.lower()
        return True, enabled
    except Exception:
        return False, False


def _task_exists(task_name: str = _TASK_NAME) -> bool:
    exists, _ = _task_state(task_name)
    return exists


def is_timer_active() -> bool:
    _, enabled = _task_state(_TASK_NAME)
    return enabled


def _find_matching_task_names(task_name: str = _TASK_NAME) -> list[str]:
    """Return task_name plus any subfolder tasks ending with \\task_name."""
    targets = [task_name]
    base_lower = task_name.lstrip("\\").lower()
    seen = {base_lower}
    try:
        res = _run_hidden(
            ["schtasks", "/query", "/fo", "CSV", "/nh"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if res.returncode == 0 and isinstance(res.stdout, str):
            for row in csv.reader(res.stdout.splitlines()):
                if not row:
                    continue
                tn = row[0].strip()
                norm = tn.lstrip("\\").lower()
                if norm not in seen and (norm == base_lower or norm.endswith(f"\\{base_lower}")):
                    targets.append(tn)
                    seen.add(norm)
    except Exception:
        pass
    return targets


def _kill_background_check_processes() -> None:
    """Terminate any lingering python/pythonw processes running 'ag check'."""
    ps_script = (
        f"$me = {os.getpid()}; "
        "Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%.exe'\" "
        "-ErrorAction SilentlyContinue | "
        "Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -match "
        "'(?i)([\\\\/]|^|[\"\\s])ag[\"'']?\\s+check(\\s|$)' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    try:
        _run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception:
        pass


def _ps_task_cmd(tn: str, action: str) -> str:
    trimmed = tn.lstrip("\\")
    short_name = trimmed.rsplit("\\", 1)[-1].replace("'", "''")
    if "\\" in trimmed:
        task_path = ("\\" + trimmed.rsplit("\\", 1)[0] + "\\").replace("'", "''")
        path_arg = f"-TaskPath '{task_path}' "
    else:
        path_arg = ""
    confirm = " -Confirm:$false" if action == "Unregister-ScheduledTask" else ""
    return f"{action} {path_arg}-TaskName '{short_name}'{confirm}"


def start_timer(task_name: str = _TASK_NAME) -> tuple[bool, str]:
    """Enable an already-installed scheduled task without recreating it."""
    exists, enabled = _task_state(task_name)
    if not exists:
        return True, f"Задача '{task_name}' не установлена (пропущено)."
    if enabled:
        return True, f"Задача '{task_name}' уже активна."
    try:
        res = _run_hidden(
            ["schtasks", "/change", "/tn", task_name, "/enable"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if res.returncode == 0:
            return True, f"Задача '{task_name}' включена в Планировщике задач."
        _run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _ps_task_cmd(task_name, "Enable-ScheduledTask")],
            capture_output=True,
            text=True,
            timeout=15,
        )
        _, enabled_now = _task_state(task_name)
        if enabled_now:
            return True, f"Задача '{task_name}' включена в Планировщике задач."
        detail = (res.stderr or res.stdout or "").strip() or f"код {res.returncode}"
        return False, f"Не удалось включить задачу '{task_name}': {detail}"
    except Exception as e:
        return False, f"Ошибка включения задачи '{task_name}': {e}"


def stop_timer(task_name: str = _TASK_NAME, remove: bool = False) -> tuple[bool, str]:
    """Stop lingering background checks and disable (or remove on uninstall) scheduled tasks."""
    targets = _find_matching_task_names(task_name)
    changed_any = False
    errors: list[str] = []

    for tn in targets:
        try:
            _run_hidden(
                ["schtasks", "/end", "/tn", tn],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if remove:
                cmd = ["schtasks", "/delete", "/tn", tn, "/f"]
                ps_action = "Unregister-ScheduledTask"
            else:
                cmd = ["schtasks", "/change", "/tn", tn, "/disable"]
                ps_action = "Disable-ScheduledTask"

            res = _run_hidden(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0:
                changed_any = True
                continue

            exists, enabled = _task_state(tn)
            needs_fallback = exists if remove else enabled
            if needs_fallback:
                _run_hidden(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", _ps_task_cmd(tn, ps_action)],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                exists_after, enabled_after = _task_state(tn)
                still_bad = exists_after if remove else enabled_after
                if still_bad:
                    detail = (res.stderr or res.stdout or "").strip() or f"код {res.returncode}"
                    errors.append(f"{tn}: {detail}")
                else:
                    changed_any = True
        except Exception as e:
            errors.append(f"{tn}: {e}")

    _kill_background_check_processes()

    verb = "удалена" if remove else "отключена"
    if errors:
        return False, f"Не удалось {'удалить' if remove else 'отключить'} задачу '{task_name}': {'; '.join(errors)}"
    if changed_any:
        return True, f"Задача '{task_name}' остановлена и {verb} в Планировщике задач."
    return True, f"Задача '{task_name}' не найдена в Планировщике."


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
    py_exe = _resolve_pythonw()
    interval = max(1, int(interval_minutes or 2))
    task_cmd = f'"{py_exe}" "{ag_path}" check --quiet'

    try:
        res = _run_hidden(
            [
                "schtasks", "/create",
                "/tn", _TASK_NAME,
                "/tr", task_cmd,
                "/sc", "minute",
                "/mo", str(interval),
                "/rl", "HIGHEST",
                "/f",
            ],
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

    ok_timer, timer_msg = stop_timer(remove=True)
    tag = "✓" if (ok_timer and "удалена" in timer_msg) else ("i" if ok_timer else "ERR")
    msgs.append(f"[{tag}] {timer_msg}")

    rm_ep = remove_endpoint()
    if rm_ep:
        msgs.append(f"[✓] Переменная CLOUD_CODE_URL удалена ({', '.join(rm_ep)}).")

    ok_path, path_msg = update_user_path(app_dir, remove=True)
    msgs.append(f"[{'✓' if ok_path else 'WARN'}] {path_msg}.")
    return ok_hosts and ok_timer, msgs

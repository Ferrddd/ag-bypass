"""Antigravity System Module for Linux"""
import errno
import os
import platform
import plistlib
try:
    import pwd
except ImportError:
    pwd = None
import re
import shutil
import subprocess
import sys
from pathlib import Path

# =====================================================================
# Context & Permissions
# =====================================================================

def is_admin() -> bool:
    try:
        return os.geteuid() == 0
    except (AttributeError, Exception):
        return False


def user_home() -> Path:
    if (u := os.environ.get("SUDO_USER")) and pwd:
        try:
            return Path(pwd.getpwnam(u).pw_dir)
        except Exception:
            pass
    return Path.home()


def _cur_user_home() -> Path:
    cs = sys.modules.get("core.system")
    if cs and hasattr(cs, "user_home") and cs.user_home is not user_home:
        try:
            return Path(cs.user_home())
        except Exception:
            pass
    return user_home()


def _cur_sys_env() -> Path:
    cs = sys.modules.get("core.system")
    if cs and hasattr(cs, "_SYS_ENV"):
        return Path(cs._SYS_ENV)
    return _SYS_ENV


def _cur_sys_prof() -> Path:
    cs = sys.modules.get("core.system")
    if cs and hasattr(cs, "_SYS_PROF"):
        return Path(cs._SYS_PROF)
    return _SYS_PROF


def _cur_is_admin() -> bool:
    cs = sys.modules.get("core.system")
    if cs and hasattr(cs, "is_admin") and cs.is_admin is not is_admin:
        try:
            return bool(cs.is_admin())
        except Exception:
            pass
    return is_admin()


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
    if sys.platform == "darwin":
        try:
            r1 = subprocess.run(["dscacheutil", "-flushcache"], capture_output=True, timeout=3)
            r2 = subprocess.run(["killall", "-HUP", "mDNSResponder"], capture_output=True, timeout=3)
            return r1.returncode == 0 or r2.returncode == 0
        except Exception:
            return False
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
            key = (clean_ip, clean_d)
            if clean_ip and clean_d and key not in seen:
                seen.add(key)
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
    return _cur_user_home() / ".config/environment.d/10-antigravity.conf"


def apply_endpoint(url: str) -> list[str]:
    out, uf = [], _user_env_file()
    clean_url = url.strip().splitlines()[0] if url else ""
    safe_url = re.sub(r'["`$\\;]', '', clean_url)
    try:
        uf.parent.mkdir(parents=True, exist_ok=True)
        uf.write_text(f"CLOUD_CODE_URL={safe_url}\n")
        if (u := os.environ.get("SUDO_USER")) and pwd:
            try:
                pw = pwd.getpwnam(u)
                p = uf.parent
                h = _cur_user_home()
                while p != h and p != p.parent:
                    if hasattr(os, "chown"):
                        os.chown(p, pw.pw_uid, pw.pw_gid)
                    p = p.parent
                if hasattr(os, "chown"):
                    os.chown(uf, pw.pw_uid, pw.pw_gid)
            except Exception:
                pass
        out.append(str(uf))
    except Exception:
        pass

    sys_env = _cur_sys_env()
    sys_prof = _cur_sys_prof()
    if _cur_is_admin():
        for t, text in [(sys_env, f"CLOUD_CODE_URL={safe_url}\n"), (sys_prof, f'export CLOUD_CODE_URL="{safe_url}"\n')]:
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
    sys_env = _cur_sys_env()
    sys_prof = _cur_sys_prof()
    targets = [_user_env_file()] + ([sys_env, sys_prof] if _cur_is_admin() else [])
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
    sys_env = _cur_sys_env()
    sys_prof = _cur_sys_prof()
    for p, lbl in [(_user_env_file(), "~/.config/environment.d"), (sys_env, "/etc/environment.d")]:
        if p.is_file():
            try:
                for line in p.read_text().splitlines():
                    if line.startswith("CLOUD_CODE_URL="):
                        val = line.split("=", 1)[1].strip("\"' ")
                        if val:
                            return True, f"В {lbl}: {val}"
            except Exception:
                pass
    if sys_prof.is_file():
        try:
            for line in sys_prof.read_text().splitlines():
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
_SYS_APPS = Path("/Applications")
_MACOS_APP_NAMES = ("Antigravity.app", "Antigravity IDE.app")


def _cur_sys_apps() -> Path:
    cs = sys.modules.get("core.system")
    if cs and hasattr(cs, "_SYS_APPS"):
        return Path(cs._SYS_APPS)
    return _SYS_APPS


def _app_bundle_candidates(app_dir: Path) -> list[Path]:
    """Return candidate executable paths inside a macOS .app bundle."""
    contents = app_dir / "Contents"
    macos_dir = contents / "MacOS"
    cands: list[Path] = [
        contents / "Resources" / "bin" / "language_server",
    ]

    mach = platform.machine().lower()
    ls_names = (
        ("language_server_macos_arm", "language_server_macos_x64")
        if ("arm" in mach or "aarch64" in mach)
        else ("language_server_macos_x64", "language_server_macos_arm")
    )
    ls_dir = contents / "Resources" / "app" / "extensions" / "antigravity" / "bin"
    for name in ls_names:
        cands.append(ls_dir / name)

    bin_dir = contents / "Resources" / "app" / "bin"
    for name in ("antigravity", "agy"):
        cands.append(bin_dir / name)

    plist_path = contents / "Info.plist"
    if plist_path.is_file():
        try:
            with plist_path.open("rb") as f:
                info = plistlib.load(f)
            if isinstance(info, dict):
                exe = info.get("CFBundleExecutable")
                if isinstance(exe, str) and exe.strip():
                    cands.append(macos_dir / exe.strip())
        except Exception:
            pass
    for name in ("Electron", "Antigravity", "antigravity", "agy"):
        cands.append(macos_dir / name)
    return cands


def _first_existing_file(candidates: list[Path]) -> Path | None:
    for c in candidates:
        try:
            if (p := c.resolve()).is_file():
                return p
        except Exception:
            pass
    return None


def _add_unique_file(found: list[Path], seen: set[Path], path: Path) -> None:
    try:
        rp = path.resolve()
        if rp.is_file() and rp not in seen:
            seen.add(rp)
            found.append(rp)
    except Exception:
        pass


def _resolve_linux_app_or_bin(path: Path) -> Path | None:
    """If path resolves to an Electron desktop wrapper beside resources/bin/language_server, prefer language_server."""
    try:
        rp = path.resolve()
        if not rp.is_file():
            return None
        ls = (rp.parent / "resources" / "bin" / "language_server").resolve()
        return ls if ls.is_file() else rp
    except Exception:
        return None


def find_binaries(configured: str | None = "auto") -> list[Path]:
    """Discover all matching CLI and GUI Antigravity binaries."""
    home = _cur_user_home()
    if configured and configured != "auto":
        clean = configured.strip().strip('"').strip("'")
        if clean.startswith("~/"):
            p = (home / clean[2:]).resolve()
        elif clean == "~":
            p = home.resolve()
        else:
            p = Path(os.path.expanduser(clean)).resolve()
        if p.is_file():
            return [p]
        if p.is_dir():
            if p.suffix == ".app" or (p / "Contents").is_dir():
                if found := _first_existing_file(_app_bundle_candidates(p)):
                    return [found]
            if (ls := (p / "resources" / "bin" / "language_server").resolve()).is_file():
                return [ls]
            for name in ("language_server", "agy", "Antigravity", "antigravity", "Electron"):
                cand = (p / name).resolve()
                if cand.is_file():
                    return [cand]
        return []

    found: list[Path] = []
    seen: set[Path] = set()

    # 1. CLI binaries (agy)
    cli_cands = [
        home / ".local/bin/agy", home / ".local/share/agy/bin/agy",
        Path.home() / ".local/bin/agy", Path.home() / ".local/share/agy/bin/agy",
        home / "Library/Application Support/antigravity/bin/agy",
        Path("/usr/local/bin/agy"), Path("/usr/bin/agy"), Path("/opt/antigravity/bin/agy"), Path("/opt/agy/agy"),
    ]
    if w := shutil.which("agy"):
        cli_cands.append(Path(w))
    for c in cli_cands:
        _add_unique_file(found, seen, c)

    # 2. macOS GUI .app bundles
    seen_apps: set[Path] = set()
    app_roots = list(dict.fromkeys([_cur_sys_apps(), home / "Applications", Path.home() / "Applications"]))
    for root in app_roots:
        for app_name in _MACOS_APP_NAMES:
            app_dir = root / app_name
            if app_dir.is_dir():
                try:
                    resolved_app = app_dir.resolve()
                except Exception:
                    resolved_app = app_dir
                if resolved_app not in seen_apps:
                    seen_apps.add(resolved_app)
                    if bp := _first_existing_file(_app_bundle_candidates(resolved_app)):
                        _add_unique_file(found, seen, bp)

    # 3. Linux GUI desktop app directories & PATH fallbacks
    for desktop_dir in (Path("/usr/share/antigravity"), Path("/opt/antigravity")):
        if desktop_dir.is_dir():
            if bp := _first_existing_file([
                desktop_dir / "resources" / "bin" / "language_server",
                desktop_dir / "antigravity",
            ]):
                _add_unique_file(found, seen, bp)

    gui_cands = [Path("/usr/local/bin/antigravity"), Path("/usr/bin/antigravity")]
    for name in ("antigravity", "Antigravity"):
        if w := shutil.which(name):
            gui_cands.append(Path(w))
    for c in gui_cands:
        if resolved := _resolve_linux_app_or_bin(c):
            _add_unique_file(found, seen, resolved)

    return found


def find_binary(configured: str | None = "auto") -> Path | None:
    bins = find_binaries(configured)
    return bins[0] if bins else None


def get_binary_status(bin_path: Path) -> str:
    real_bin = bin_path.resolve()
    if not real_bin.is_file():
        return "FILE_NOT_FOUND"
    try:
        d = real_bin.read_bytes()
        return "ALREADY_PATCHED" if SIG_PATCHED in d else ("READY_TO_PATCH" if SIG_ORIGINAL in d else "SIGNATURE_NOT_FOUND")
    except Exception as e:
        return f"ERROR: {e}"


_MACHO_MAGICS = (
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
)


def _is_macho_file(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(4) in _MACHO_MAGICS
    except Exception:
        return False


def _resign_macos_macho(path: Path) -> bool | None:
    """Re-sign a patched Mach-O binary on macOS; returns None if not applicable."""
    if sys.platform != "darwin" or not _is_macho_file(path):
        return None
    if not shutil.which("codesign"):
        return False
    try:
        res = subprocess.run(
            ["codesign", "--force", "--sign", "-", str(path)],
            capture_output=True,
            timeout=15,
        )
        return res.returncode == 0
    except Exception:
        return False


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
    tmp = real_bin.with_name(f"{real_bin.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(d.replace(SIG_ORIGINAL, SIG_PATCHED))
        _apply_perms(tmp, st)
        os.replace(tmp, real_bin)
        resigned = _resign_macos_macho(real_bin)
        if resigned is True:
            return True, "Бинарник успешно пропатчен и переподписан"
        if resigned is False:
            return True, "Бинарник пропатчен, но не удалось переподписать через codesign"
        return True, "Бинарник успешно пропатчен"
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
    if bak.is_file():
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

    if not real_bin.is_file():
        return False, f"Файл не найден: {bin_path}"
    try:
        d = real_bin.read_bytes()
    except Exception as e:
        return False, f"Ошибка чтения {real_bin}: {e}"
    if SIG_PATCHED not in d:
        return False, "Патч не обнаружен в бинарнике"

    st = real_bin.stat()
    tmp = real_bin.with_name(f"{real_bin.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(d.replace(SIG_PATCHED, SIG_ORIGINAL))
        _apply_perms(tmp, st)
        os.replace(tmp, real_bin)
        _resign_macos_macho(real_bin)
        return True, "Патч успешно откачен"
    except Exception as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        return False, f"Ошибка отката патча: {e}"


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

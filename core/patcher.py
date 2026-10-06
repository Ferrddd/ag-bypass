import os
import pwd
import shutil
from pathlib import Path

SIG_ORIGINAL, SIG_PATCHED = b"ineligible", b"inexigible"


def _user_home() -> Path:
    if u := os.environ.get("SUDO_USER"):
        try:
            return Path(pwd.getpwnam(u).pw_dir)
        except Exception:
            pass
    return Path.home()


def find_binary(configured: str = "auto") -> Path | None:
    home = _user_home()
    if configured and configured != "auto":
        p = Path(str(home) + configured[1:] if configured.startswith("~") else os.path.expanduser(configured)).resolve()
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


def get_running_pids(bin_path: Path) -> list[int]:
    target, pids, proc = bin_path.resolve(), [], Path("/proc")
    if proc.is_dir():
        for e in proc.iterdir():
            if e.name.isdigit():
                try:
                    if (e / "exe").resolve() == target:
                        pids.append(int(e.name))
                except Exception:
                    pass
    return pids


def is_binary_running(bin_path: Path) -> bool:
    return bool(get_running_pids(bin_path))


def get_status(bin_path: Path) -> str:
    if not bin_path.is_file():
        return "FILE_NOT_FOUND"
    try:
        d = bin_path.read_bytes()
        return "ALREADY_PATCHED" if SIG_PATCHED in d else ("READY_TO_PATCH" if SIG_ORIGINAL in d else "SIGNATURE_NOT_FOUND")
    except Exception as e:
        return f"ERROR: {e}"


def apply_patch(bin_path: Path) -> tuple[bool, str]:
    if not bin_path.is_file():
        return False, f"Файл не найден: {bin_path}"
    try:
        d = bin_path.read_bytes()
    except Exception as e:
        return False, f"Ошибка чтения {bin_path}: {e}"
    if SIG_PATCHED in d:
        return True, "Бинарник уже пропатчен (inexigible найден)"
    if SIG_ORIGINAL not in d:
        return False, "Сигнатура 'ineligible' не найдена в бинарнике"
    bak = bin_path.with_name(bin_path.name + ".bak")
    if not bak.exists():
        try:
            shutil.copy2(bin_path, bak)
        except Exception as e:
            return False, f"Не удалось создать бэкап {bak}: {e}"
    tmp = bin_path.with_name(f"{bin_path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(d.replace(SIG_ORIGINAL, SIG_PATCHED))
        os.chmod(tmp, bin_path.stat().st_mode)
        os.replace(tmp, bin_path)
        return True, f"Бинарник успешно пропатчен ({d.count(SIG_ORIGINAL)} замен, бэкап сохранён в {bak.name})"
    except Exception as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        return False, f"Ошибка записи пропатченного бинарника: {e}"


def restore_backup(bin_path: Path) -> tuple[bool, str]:
    bak = bin_path.with_name(bin_path.name + ".bak")
    if not bak.is_file():
        return False, f"Бэкап не найден: {bak}"
    try:
        shutil.copy2(bak, bin_path)
        os.chmod(bin_path, bak.stat().st_mode)
        bak.unlink()
        return True, f"Оригинальный бинарник восстановлен из {bak.name}"
    except Exception as e:
        return False, f"Ошибка восстановления бэкапа: {e}"
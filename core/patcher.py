import os
import shutil
import stat
import subprocess
from pathlib import Path

SIG_ORIGINAL, SIG_PATCHED = b"ineligible", b"inexigible"


def find_binary(configured: str = "auto") -> Path | None:
    if configured and configured != "auto":
        p = Path(os.path.expandvars(os.path.expanduser(configured))).resolve()
        if p.is_file():
            return p
        if not p.suffix and p.with_suffix(".exe").is_file():
            return p.with_suffix(".exe")
        return None

    user_profile = Path(os.environ.get("USERPROFILE") or Path.home())
    local_app_data = Path(os.environ.get("LOCALAPPDATA") or user_profile / "AppData/Local")
    app_data = Path(os.environ.get("APPDATA") or user_profile / "AppData/Roaming")
    prog_files = Path(os.environ.get("ProgramFiles") or r"C:\Program Files")
    prog_files_x86 = Path(os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)")

    cands = [
        user_profile / ".local/bin/agy.exe",
        user_profile / ".local/bin/agy",
        local_app_data / "Programs/agy/agy.exe",
        local_app_data / "agy/agy.exe",
        local_app_data / "Google/Antigravity/agy.exe",
        app_data / "agy/bin/agy.exe",
        prog_files / "Google/Antigravity/agy.exe",
        prog_files_x86 / "Google/Antigravity/agy.exe",
        prog_files / "Antigravity/agy.exe",
    ]
    if w := (shutil.which("agy.exe") or shutil.which("agy")):
        cands.append(Path(w))

    for c in cands:
        try:
            if (p := c.resolve()).is_file():
                return p
        except Exception:
            pass
    return None


def get_running_pids(bin_path: Path) -> list[int]:
    target_name = bin_path.name.lower()
    pids = []
    try:
        res = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {target_name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in res.stdout.strip().splitlines():
            parts = line.split(",")
            if len(parts) >= 2:
                p_name = parts[0].strip('"\r\n ').lower()
                p_pid = parts[1].strip('"\r\n ')
                if p_name == target_name and p_pid.isdigit():
                    pids.append(int(p_pid))
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


def _ensure_writable(path: Path):
    try:
        if path.exists():
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except Exception:
        pass


def apply_patch(bin_path: Path) -> tuple[bool, str]:
    if not bin_path.is_file():
        return False, f"Файл не найден: {bin_path}"

    pids = get_running_pids(bin_path)
    if pids:
        return False, f"Процесс {bin_path.name} в данный момент запущен (PID: {', '.join(map(str, pids))}). Пожалуйста, закройте Antigravity перед патчингом."

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
            _ensure_writable(bak)
            shutil.copy2(bin_path, bak)
        except Exception as e:
            return False, f"Не удалось создать бэкап {bak}: {e}"

    tmp = bin_path.with_name(f"{bin_path.name}.{os.getpid()}.tmp")
    try:
        _ensure_writable(bin_path)
        tmp.write_bytes(d.replace(SIG_ORIGINAL, SIG_PATCHED))
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

    pids = get_running_pids(bin_path)
    if pids:
        return False, f"Процесс {bin_path.name} запущен (PID: {', '.join(map(str, pids))}). Закройте приложение перед восстановлением."

    try:
        _ensure_writable(bin_path)
        shutil.copy2(bak, bin_path)
        bak.unlink()
        return True, f"Оригинальный бинарник восстановлен из {bak.name}"
    except Exception as e:
        return False, f"Ошибка восстановления бэкапа: {e}"

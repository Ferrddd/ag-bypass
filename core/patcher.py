import os
import shutil
from pathlib import Path

SIG_ORIGINAL = b"ineligible"
SIG_PATCHED = b"inexigible"

STANDARD_CANDIDATES = [
    Path.home() / ".local/bin/agy",
    Path.home() / ".local/share/agy/bin/agy",
    Path("/usr/local/bin/agy"),
    Path("/usr/bin/agy"),
]


def find_binary(configured_path: str = "auto") -> Path | None:
    if configured_path and configured_path != "auto":
        p = Path(os.path.expanduser(configured_path)).resolve()
        if p.is_file():
            return p
        return None

    for candidate in STANDARD_CANDIDATES:
        cand = candidate.resolve()
        if cand.is_file():
            return cand

    which_agy = shutil.which("agy")
    if which_agy:
        p = Path(which_agy).resolve()
        if p.is_file():
            return p

    return None


def get_status(bin_path: Path) -> str:
    if not bin_path.is_file():
        return "FILE_NOT_FOUND"

    try:
        data = bin_path.read_bytes()
        if SIG_PATCHED in data:
            return "ALREADY_PATCHED"
        if SIG_ORIGINAL in data:
            return "READY_TO_PATCH"
        return "SIGNATURE_NOT_FOUND"
    except Exception as e:
        return f"ERROR: {e}"


def apply_patch(bin_path: Path) -> tuple[bool, str]:
    if not bin_path.is_file():
        return False, f"Файл не найден: {bin_path}"

    try:
        data = bytearray(bin_path.read_bytes())
    except Exception as e:
        return False, f"Ошибка чтения {bin_path}: {e}"

    if SIG_PATCHED in data:
        return True, "Бинарник уже пропатчен (inexigible найден)"

    idx = data.find(SIG_ORIGINAL)
    if idx == -1:
        return False, "Сигнатура 'ineligible' не найдена в бинарнике"

    backup_path = bin_path.with_name(bin_path.name + ".bak")
    if not backup_path.exists():
        try:
            shutil.copy2(bin_path, backup_path)
        except Exception as e:
            return False, f"Не удалось создать бэкап {backup_path}: {e}"

    # Replace all occurrences of ineligible -> inexigible (exact 10 bytes)
    count = 0
    pos = 0
    while True:
        pos = data.find(SIG_ORIGINAL, pos)
        if pos == -1:
            break
        data[pos : pos + len(SIG_ORIGINAL)] = SIG_PATCHED
        pos += len(SIG_PATCHED)
        count += 1

    # Write atomically
    tmp_path = bin_path.with_name(f"{bin_path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_bytes(data)
        st = os.stat(bin_path)
        os.chmod(tmp_path, st.st_mode)
        os.replace(tmp_path, bin_path)
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        return False, f"Ошибка записи пропатченного бинарника: {e}"

    return True, f"Бинарник успешно пропатчен ({count} замен, бэкап сохранён в {backup_path.name})"


def restore_backup(bin_path: Path) -> tuple[bool, str]:
    backup_path = bin_path.with_name(bin_path.name + ".bak")
    if not backup_path.is_file():
        return False, f"Бэкап не найден: {backup_path}"

    try:
        st = os.stat(backup_path)
        shutil.copy2(backup_path, bin_path)
        os.chmod(bin_path, st.st_mode)
        backup_path.unlink()
        return True, f"Оригинальный бинарник восстановлен из {backup_path.name}"
    except Exception as e:
        return False, f"Ошибка восстановления бэкапа: {e}"
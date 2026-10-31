import os
import shutil
import subprocess
import sys
from pathlib import Path

TASK_NAME = "AntigravityBypass"


def get_python_runner() -> str:
    # Prefer pythonw.exe so background healthchecks don't flash a console window
    py_dir = Path(sys.executable).parent
    pythonw = py_dir / "pythonw.exe"
    if pythonw.is_file():
        return str(pythonw)
    return sys.executable


def install_task(interval_minutes: int = 15) -> tuple[bool, str]:
    script_path = (Path(__file__).resolve().parent.parent / "ag.py").resolve()
    runner = get_python_runner()

    cmd = (
        f'schtasks /create /tn "{TASK_NAME}" '
        f'/tr "\\"{runner}\\" \\"{script_path}\\" check --quiet" '
        f'/sc minute /mo {interval_minutes} /ru "SYSTEM" /f /rl HIGHEST'
    )
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if res.returncode == 0:
            return True, f"Задание '{TASK_NAME}' успешно создано в планировщике (каждые {interval_minutes} мин)"
        
        # Fallback to current user if SYSTEM fails
        cmd_user = (
            f'schtasks /create /tn "{TASK_NAME}" '
            f'/tr "\\"{runner}\\" \\"{script_path}\\" check --quiet" '
            f'/sc minute /mo {interval_minutes} /f /rl HIGHEST'
        )
        res_user = subprocess.run(cmd_user, shell=True, capture_output=True, text=True)
        if res_user.returncode == 0:
            return True, f"Задание '{TASK_NAME}' успешно создано в планировщике (каждые {interval_minutes} мин)"
        return False, f"Ошибка schtasks: {res.stderr or res.stdout or res_user.stderr}"
    except Exception as e:
        return False, f"Исключение при вызове schtasks: {e}"


def remove_task() -> tuple[bool, str]:
    try:
        res = subprocess.run(f'schtasks /delete /tn "{TASK_NAME}" /f', shell=True, capture_output=True, text=True)
        if res.returncode == 0 or "не существует" in (res.stderr + res.stdout) or "cannot find" in (res.stderr + res.stdout).lower():
            return True, f"Задание '{TASK_NAME}' удалено из планировщика"
        return False, f"Ошибка удаления задания: {res.stderr or res.stdout}"
    except Exception as e:
        return False, f"Исключение при удалении задания: {e}"


def is_task_active() -> bool:
    try:
        res = subprocess.run(f'schtasks /query /tn "{TASK_NAME}"', shell=True, capture_output=True, text=True)
        return res.returncode == 0
    except Exception:
        return False

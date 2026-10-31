import os
import subprocess
from pathlib import Path

DEFAULT_ENDPOINT = "https://daily-cloudcode-pa.googleapis.com"
VAR_NAME = "CLOUD_CODE_URL"


def _broadcast_change():
    try:
        import ctypes
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        res = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, ctypes.byref(res)
        )
    except Exception:
        pass


def apply_endpoint(url: str = DEFAULT_ENDPOINT) -> list[str]:
    applied = []
    # Set for Current User via winreg or setx
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VAR_NAME, 0, winreg.REG_SZ, url)
        applied.append("HKCU\\Environment")
    except Exception:
        try:
            res = subprocess.run(["setx", VAR_NAME, url], capture_output=True, check=False)
            if res.returncode == 0:
                applied.append("setx (User)")
        except Exception:
            pass

    # Try Machine scope if admin
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment", 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, VAR_NAME, 0, winreg.REG_SZ, url)
        applied.append("HKLM\\SYSTEM\\...\\Environment")
    except Exception:
        pass

    os.environ[VAR_NAME] = url
    _broadcast_change()
    return applied


def remove_endpoint() -> list[str]:
    removed = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VAR_NAME)
        removed.append("HKCU\\Environment")
    except Exception:
        pass

    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment", 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VAR_NAME)
        removed.append("HKLM\\SYSTEM\\...\\Environment")
    except Exception:
        pass

    os.environ.pop(VAR_NAME, None)
    _broadcast_change()
    return removed


def get_status() -> tuple[bool, str]:
    if v := os.environ.get(VAR_NAME):
        return True, f"В текущей сессии: {v}"

    try:
        import winreg
        for root, root_name in [
            (winreg.HKEY_CURRENT_USER, "User"),
            (winreg.HKEY_LOCAL_MACHINE, "System")
        ]:
            subkey = "Environment" if root_name == "User" else r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
            try:
                with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ) as key:
                    val, _ = winreg.QueryValueEx(key, VAR_NAME)
                    if val:
                        return True, f"В реестре Windows ({root_name}): {val}"
            except Exception:
                pass
    except Exception:
        pass

    return False, "Не настроен (клиент будет использовать дефолтный заблокированный адрес)"

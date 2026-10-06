import os
from pathlib import Path

DEFAULT_ENDPOINT = "https://daily-cloudcode-pa.googleapis.com"

SYSTEM_ENV_DIR = Path("/etc/environment.d")
SYSTEM_PROFILE_DIR = Path("/etc/profile.d")


def _get_user_env_file() -> Path:
    user_home = Path.home()
    if "SUDO_USER" in os.environ:
        try:
            import pwd
            user_home = Path(pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir)
        except Exception:
            pass
    return user_home / ".config" / "environment.d" / "10-antigravity.conf"


def apply_endpoint(url: str = DEFAULT_ENDPOINT) -> list[str]:
    applied: list[str] = []

    # 1. User environment (~/.config/environment.d/10-antigravity.conf)
    user_env = _get_user_env_file()
    try:
        user_env.parent.mkdir(parents=True, exist_ok=True)
        user_env.write_text(f"CLOUD_CODE_URL={url}\n", encoding="utf-8")
        if "SUDO_USER" in os.environ:
            try:
                import pwd
                pw = pwd.getpwnam(os.environ["SUDO_USER"])
                os.chown(user_env, pw.pw_uid, pw.pw_gid)
            except Exception:
                pass
        applied.append(str(user_env))
    except Exception:
        pass

    # 2. System-wide environment (if root)
    if os.geteuid() == 0:
        if SYSTEM_ENV_DIR.exists():
            sys_env = SYSTEM_ENV_DIR / "10-antigravity.conf"
            try:
                sys_env.write_text(f"CLOUD_CODE_URL={url}\n", encoding="utf-8")
                applied.append(str(sys_env))
            except Exception:
                pass

        if SYSTEM_PROFILE_DIR.exists():
            prof = SYSTEM_PROFILE_DIR / "antigravity.sh"
            try:
                prof.write_text(f'export CLOUD_CODE_URL="{url}"\n', encoding="utf-8")
                prof.chmod(0o755)
                applied.append(str(prof))
            except Exception:
                pass

    return applied


def remove_endpoint() -> list[str]:
    removed: list[str] = []

    user_env = _get_user_env_file()
    if user_env.is_file():
        try:
            user_env.unlink()
            removed.append(str(user_env))
        except Exception:
            pass

    if os.geteuid() == 0:
        sys_env = SYSTEM_ENV_DIR / "10-antigravity.conf"
        if sys_env.is_file():
            try:
                sys_env.unlink()
                removed.append(str(sys_env))
            except Exception:
                pass

        prof = SYSTEM_PROFILE_DIR / "antigravity.sh"
        if prof.is_file():
            try:
                prof.unlink()
                removed.append(str(prof))
            except Exception:
                pass

    return removed


def get_status() -> tuple[bool, str]:
    if os.environ.get("CLOUD_CODE_URL"):
        return True, f"В текущей сессии: {os.environ['CLOUD_CODE_URL']}"

    user_env = _get_user_env_file()
    if user_env.is_file():
        try:
            for line in user_env.read_text(encoding="utf-8").splitlines():
                if line.startswith("CLOUD_CODE_URL="):
                    val = line.split("=", 1)[1].strip().strip('"\'')
                    return True, f"В ~/.config/environment.d: {val}"
        except Exception:
            pass

    sys_env = SYSTEM_ENV_DIR / "10-antigravity.conf"
    if sys_env.is_file():
        try:
            for line in sys_env.read_text(encoding="utf-8").splitlines():
                if line.startswith("CLOUD_CODE_URL="):
                    val = line.split("=", 1)[1].strip().strip('"\'')
                    return True, f"В /etc/environment.d: {val}"
        except Exception:
            pass

    prof = SYSTEM_PROFILE_DIR / "antigravity.sh"
    if prof.is_file():
        return True, f"В /etc/profile.d/antigravity.sh"

    return False, "Не настроен (клиент будет использовать дефолтный заблокированный адрес)"


def get_export_command(url: str = DEFAULT_ENDPOINT) -> str:
    return f'export CLOUD_CODE_URL="{url}"'

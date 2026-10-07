import os
import pwd
from pathlib import Path

DEFAULT_ENDPOINT = "https://daily-cloudcode-pa.googleapis.com"
_SYS_ENV, _SYS_PROF = Path("/etc/environment.d/10-antigravity.conf"), Path("/etc/profile.d/antigravity.sh")


def _user_file() -> Path:
    if u := os.environ.get("SUDO_USER"):
        try:
            return Path(pwd.getpwnam(u).pw_dir) / ".config/environment.d/10-antigravity.conf"
        except Exception:
            pass
    return Path.home() / ".config/environment.d/10-antigravity.conf"


def apply_endpoint(url: str = DEFAULT_ENDPOINT) -> list[str]:
    out, uf = [], _user_file()
    try:
        uf.parent.mkdir(parents=True, exist_ok=True)
        uf.write_text(f"CLOUD_CODE_URL={url}\n")
        if u := os.environ.get("SUDO_USER"):
            try:
                os.chown(uf, pwd.getpwnam(u).pw_uid, pwd.getpwnam(u).pw_gid)
            except Exception:
                pass
        out.append(str(uf))
    except Exception:
        pass
    if os.geteuid() == 0:
        for t, text in [(_SYS_ENV, f"CLOUD_CODE_URL={url}\n"), (_SYS_PROF, f'export CLOUD_CODE_URL="{url}"\n')]:
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
    targets = [_user_file()] + ([_SYS_ENV, _SYS_PROF] if os.geteuid() == 0 else [])
    removed = []
    for p in targets:
        if p.is_file():
            try:
                p.unlink()
                removed.append(str(p))
            except Exception:
                pass
    return removed


def get_status() -> tuple[bool, str]:
    if v := os.environ.get("CLOUD_CODE_URL"):
        return True, f"В текущей сессии: {v}"
    for p, lbl in [(_user_file(), "~/.config/environment.d"), (_SYS_ENV, "/etc/environment.d")]:
        if p.is_file():
            try:
                for line in p.read_text().splitlines():
                    if line.startswith("CLOUD_CODE_URL="):
                        return True, f"В {lbl}: {line.split('=', 1)[1].strip('\"\'')}"
            except Exception:
                pass
    return (True, "В /etc/profile.d/antigravity.sh") if _SYS_PROF.is_file() else (
        False, "Не настроен (клиент будет использовать дефолтный заблокированный адрес)"
    )

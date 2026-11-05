"""Antigravity System Platform Dispatcher"""
import sys
from pathlib import Path

if sys.platform == "win32":
    from core.system_windows import (
        TAG_BEGIN,
        TAG_END,
        DEFAULT_HOSTS_PATH,
        is_admin,
        flush_dns_cache,
        get_active_entries,
        set_entries,
        clear_entries,
        apply_endpoint,
        remove_endpoint,
        get_endpoint_status,
        find_binary,
        get_binary_status,
        apply_patch,
        restore_backup,
        is_timer_active,
        user_home,
    )
    _SYS_ENV = None
    _SYS_PROF = None
else:
    from core.system_linux import (
        TAG_BEGIN,
        TAG_END,
        DEFAULT_HOSTS_PATH,
        is_admin,
        flush_dns_cache,
        get_active_entries,
        set_entries,
        clear_entries,
        apply_endpoint,
        remove_endpoint,
        get_endpoint_status,
        find_binary,
        get_binary_status,
        apply_patch,
        restore_backup,
        is_timer_active,
        user_home,
        _SYS_ENV,
        _SYS_PROF,
    )

__all__ = [
    "TAG_BEGIN",
    "TAG_END",
    "DEFAULT_HOSTS_PATH",
    "is_admin",
    "flush_dns_cache",
    "get_active_entries",
    "set_entries",
    "clear_entries",
    "apply_endpoint",
    "remove_endpoint",
    "get_endpoint_status",
    "find_binary",
    "get_binary_status",
    "apply_patch",
    "restore_backup",
    "is_timer_active",
]

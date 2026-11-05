#!/usr/bin/env python3
"""
Release packager for Antigravity Bypass.
Generates 2 release archives:
  1. ag-bypass-windows.zip      (for Windows 10/11)
  2. ag-bypass-linux-macos.tar.gz (for Linux & macOS)
Also generates SHA256SUMS.txt for integrity verification.
"""

import argparse
import hashlib
import os
import tarfile
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

COMMON_CORE_FILES = [
    "core/__init__.py",
    "core/config.py",
    "core/network.py",
    "core/system.py",
    "core/system_linux.py",
    "core/system_windows.py",
]

WINDOWS_FILES = [
    "ag",
    "ag.bat",
    "ag.ps1",
    "install.ps1",
    "uninstall.ps1",
    "config.json",
    "README.md",
] + COMMON_CORE_FILES

LINUX_MACOS_FILES = [
    "ag",
    "install.sh",
    "uninstall.sh",
    "config.json",
    "systemd/ag-check.service",
    "systemd/ag-check.timer",
    "README.md",
] + COMMON_CORE_FILES

EXECUTABLE_FILES = {
    "ag",
    "install.sh",
    "uninstall.sh",
}


def compute_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def package_windows_zip(output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel_path in WINDOWS_FILES:
            src = PROJECT_ROOT / rel_path
            if not src.is_file():
                raise FileNotFoundError(f"Missing required file for Windows archive: {src}")
            # Normalize to forward slash for zip archive
            arcname = rel_path.replace("\\", "/")
            zf.write(src, arcname=arcname)
    print(f"  [✓] Created Windows archive: {output_path.name} ({output_path.stat().st_size:,} bytes)")


def package_linux_macos_tar(output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    with tarfile.open(output_path, "w:gz") as tf:
        for rel_path in LINUX_MACOS_FILES:
            src = PROJECT_ROOT / rel_path
            if not src.is_file():
                raise FileNotFoundError(f"Missing required file for Linux/macOS archive: {src}")

            arcname = rel_path.replace("\\", "/")
            ti = tf.gettarinfo(src, arcname=arcname)
            ti.uid = 0
            ti.gid = 0
            ti.uname = ""
            ti.gname = ""
            if rel_path in EXECUTABLE_FILES:
                ti.mode = 0o755
            else:
                ti.mode = 0o644
            with open(src, "rb") as f:
                tf.addfile(ti, f)
    print(f"  [✓] Created Linux/macOS archive: {output_path.name} ({output_path.stat().st_size:,} bytes)")


def main():
    parser = argparse.ArgumentParser(description="Build release archives for Antigravity Bypass")
    parser.add_argument("--output-dir", "-o", default=str(PROJECT_ROOT / "dist"), help="Output directory (default: dist/)")
    parser.add_argument("--tag", "-t", default=None, help="Optional version tag suffix (e.g. v1.0.0)")
    args = parser.parse_args()

    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Output filenames: standard fixed names or with version tag if provided
    prefix = "ag-bypass"
    win_name = f"{prefix}-windows.zip"
    nix_name = f"{prefix}-linux-macos.tar.gz"

    win_path = out_dir / win_name
    nix_path = out_dir / nix_name

    print(f"[*] Packaging release artifacts into {out_dir}...")
    package_windows_zip(win_path)
    package_linux_macos_tar(nix_path)

    # Generate checksums
    checksums_file = out_dir / "SHA256SUMS.txt"
    checksums = [
        f"{compute_sha256(win_path)}  {win_name}",
        f"{compute_sha256(nix_path)}  {nix_name}",
    ]
    checksums_file.write_text("\n".join(checksums) + "\n", encoding="utf-8")
    print(f"  [✓] Generated checksums: {checksums_file.name}")
    print("\nRelease assets ready:")
    for line in checksums:
        print(f"  {line}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Build the single-file Windows executable.

    .venv\\Scripts\\python tools\\build_exe.py

Produces ``dist/NexusController.exe``. Nothing binary is kept in the repository:
the ViGEmBus installer is fetched from its official GitHub release at build time
and bundled into the executable, so the finished app can install the driver
without needing a network connection on the user's machine.

Pass ``--no-driver`` to skip that download (the app then opens the download page
instead of running an installer).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "build" / "vendor"
DRIVER_NAME = "ViGEmBusSetup.exe"
RELEASE_API = "https://api.github.com/repos/nefarius/ViGEmBus/releases/latest"
USER_AGENT = "NexusController-build"


def log(message: str) -> None:
    print(f"[build] {message}", flush=True)


def fetch_driver() -> Path | None:
    """Download the official ViGEmBus MSI, or return ``None`` if unavailable."""
    VENDOR.mkdir(parents=True, exist_ok=True)
    target = VENDOR / DRIVER_NAME
    if target.is_file() and target.stat().st_size > 100_000:
        log(f"driver already present: {target.name}")
        return target

    try:
        request = urllib.request.Request(RELEASE_API, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            release = json.load(response)
    except Exception as exc:  # noqa: BLE001
        log(f"could not reach the ViGEmBus release API ({exc}); building without the driver")
        return None

    # ViGEmBus ships a single self-contained .exe installer; older releases used
    # an .msi, so accept either rather than pinning to today's packaging.
    asset = next(
        (
            a for a in release.get("assets", [])
            if a.get("name", "").lower().endswith((".exe", ".msi"))
        ),
        None,
    )
    if asset is None:
        log("no installer asset in the latest ViGEmBus release; building without the driver")
        return None

    url = asset["browser_download_url"]
    if not url.startswith("https://github.com/nefarius/ViGEmBus/"):
        log(f"refusing an asset from an unexpected host: {url}")
        return None

    log(f"downloading {asset['name']} ({asset.get('size', 0) // 1024} KiB)")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            payload = response.read()
    except Exception as exc:  # noqa: BLE001
        log(f"download failed ({exc}); building without the driver")
        return None

    target = VENDOR / (DRIVER_NAME if asset['name'].lower().endswith('.exe')
                       else 'ViGEmBusSetup.msi')
    target.write_bytes(payload)
    log(f"driver bundled: {target.name}, sha256={hashlib.sha256(payload).hexdigest()[:16]}…")
    return target


def make_icon() -> Path | None:
    """Convert the PNG logo to the .ico format Windows needs for an exe icon."""
    source = ROOT / "docs" / "logo.png"
    target = ROOT / "build" / "icon.ico"
    if not source.is_file():
        return None
    if target.is_file():
        return target
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        log("Pillow not available; building without an icon")
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    image = Image.open(source).convert("RGBA")
    image.save(target, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    log("icon generated")
    return target


def build(driver: Path | None, icon: Path | None) -> int:
    separator = ";" if sys.platform == "win32" else ":"
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "NexusController",
        "--paths", str(ROOT / "server"),
        "--add-data", f"{ROOT / 'server' / 'nexus_server' / 'web'}{separator}web",
        "--collect-all", "vgamepad",
        "--hidden-import", "pynput.keyboard._win32",
        "--hidden-import", "pynput.mouse._win32",
        "--exclude-module", "tkinter",
        "--exclude-module", "pytest",
    ]
    if driver is not None:
        command += ["--add-data", f"{driver}{separator}vendor"]
    if icon is not None:
        command += ["--icon", str(icon)]
    command.append(str(ROOT / "server" / "run_server.py"))

    log("running PyInstaller")
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        return result.returncode

    produced = ROOT / "dist" / ("NexusController.exe" if sys.platform == "win32" else "NexusController")
    if not produced.is_file():
        log("PyInstaller reported success but produced no executable")
        return 1

    digest = hashlib.sha256(produced.read_bytes()).hexdigest()
    (ROOT / "dist" / "SHA256SUMS.txt").write_text(
        f"{digest}  {produced.name}\n", encoding="utf-8"
    )
    log(f"built {produced.name} — {produced.stat().st_size / 1_048_576:.1f} MiB")
    log(f"sha256 {digest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-driver", action="store_true", help="do not bundle ViGEmBus")
    args = parser.parse_args(argv)

    if shutil.which("pyinstaller") is None:
        try:
            import PyInstaller  # noqa: F401,PLC0415
        except ImportError:
            log("PyInstaller is missing — pip install pyinstaller")
            return 1

    driver = None if args.no_driver else fetch_driver()
    return build(driver, make_icon())


if __name__ == "__main__":
    sys.exit(main())

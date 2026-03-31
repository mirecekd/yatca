from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from helpers.errors import format_error
from helpers.print_style import PrintStyle


_LOCK = threading.Lock()
_CHECKED = False
_PLUGIN_DIR = Path(__file__).resolve().parents[1]
_REQUIREMENTS_FILE = _PLUGIN_DIR / "requirements.txt"


def has_aiogram() -> bool:
    return importlib.util.find_spec("aiogram") is not None


def ensure_dependencies() -> None:
    global _CHECKED

    if _CHECKED and has_aiogram():
        return

    with _LOCK:
        if _CHECKED and has_aiogram():
            return
        if has_aiogram():
            _CHECKED = True
            return

        _install_deps()
        importlib.invalidate_caches()

        if not has_aiogram():
            raise RuntimeError("YATCA dependency 'aiogram' is still unavailable after installation")

        _CHECKED = True


def _install_deps() -> None:
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("YATCA plugin requires 'uv' to install aiogram automatically")
    if not _REQUIREMENTS_FILE.is_file():
        raise RuntimeError(f"YATCA plugin requirements file not found: {_REQUIREMENTS_FILE}")

    cmd = [
        uv,
        "pip",
        "install",
        "--python",
        sys.executable,
        "-r",
        str(_REQUIREMENTS_FILE),
    ]

    PrintStyle.info("YATCA: aiogram not found, installing plugin dependencies")
    try:
        subprocess.check_call(cmd, cwd=str(_PLUGIN_DIR))
    except Exception as e:
        raise RuntimeError(f"Failed to install YATCA dependencies: {format_error(e)}") from e

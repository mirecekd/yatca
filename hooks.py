"""
YATCA Plugin Hooks.
Called by Agent Zero framework at plugin install/enable time.

NOTE: Cannot use 'from plugins.yatca.helpers...' here because
the plugin module path is not yet available during installation.
Uses direct subprocess call instead.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


_PLUGIN_DIR = Path(__file__).resolve().parent
_REQUIREMENTS_FILE = _PLUGIN_DIR / "requirements.txt"


def install():
    """Install plugin dependencies (called at plugin installation time)."""
    if not _REQUIREMENTS_FILE.is_file():
        return

    uv = shutil.which("uv")
    if not uv:
        return

    cmd = [
        uv,
        "pip",
        "install",
        "--python",
        sys.executable,
        "-r",
        str(_REQUIREMENTS_FILE),
    ]

    try:
        subprocess.check_call(cmd, cwd=str(_PLUGIN_DIR))
    except Exception as e:
        print(f"YATCA: failed to install dependencies: {e}")

"""
YATCA Plugin Hooks.
Called by Agent Zero framework at plugin install/enable time.
"""

from plugins.yatca.helpers.dependencies import ensure_dependencies


def install():
    """Install plugin dependencies (called at plugin installation time)."""
    ensure_dependencies()

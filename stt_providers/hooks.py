"""
STT Providers Plugin - Installation hooks.
Called by Agent Zero framework at plugin install time.
"""

def install():
    """Install plugin dependencies."""
    # httpx and aiohttp are already available in the Agent Zero environment.
    # No additional dependencies are required for this plugin.
    print("[stt_providers] Plugin installed. Configure your provider in Plugin Settings.")

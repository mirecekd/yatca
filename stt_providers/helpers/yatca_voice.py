"""
STT Providers Plugin - YATCA voice message integration.
Monkey-patches YATCA's handle_message to transcribe .ogg voice attachments
before they are sent to the agent.
"""

import os
from helpers.print_style import PrintStyle

_PATCH_ATTR = "_stt_providers_voice_patched"

VOICE_EXTENSIONS = {".ogg", ".oga", ".mp3", ".m4a", ".wav", ".flac", ".webm", ".opus"}


def is_voice_file(path: str) -> bool:
    """Check if a file is an audio/voice file that should be transcribed."""
    _, ext = os.path.splitext(path.lower())
    return ext in VOICE_EXTENSIONS


def resolve_local_path(file_path: str) -> str:
    """Resolve a possibly dockerized path to a local filesystem path."""
    if os.path.exists(file_path):
        return file_path
    # Try stripping /a0/ prefix -> real path
    if file_path.startswith("/a0/"):
        alt = file_path[4:]  # strip /a0/
        if os.path.exists(alt):
            return alt
    return file_path


async def transcribe_file(file_path: str) -> str | None:
    """
    Transcribe an audio file using the configured STT provider.
    Returns transcript text or None on failure.
    """
    from usr.plugins.stt_providers.helpers.transcribe import get_config, transcribe_deepgram, transcribe_openai

    cfg = get_config()
    provider = cfg.get("provider", "local")

    if provider == "local":
        return None  # Let agent handle it as attachment

    local_path = resolve_local_path(file_path)
    try:
        with open(local_path, "rb") as f:
            audio_bytes = f.read()
    except Exception as e:
        PrintStyle.error(f"[stt_providers] Cannot read voice file {file_path}: {e}")
        return None

    try:
        if provider == "deepgram":
            result = await transcribe_deepgram(audio_bytes, cfg)
        elif provider == "openai":
            result = await transcribe_openai(audio_bytes, cfg)
        else:
            return None

        transcript = result.get("text", "").strip()
        if transcript:
            PrintStyle.standard(
                f"[stt_providers] Voice transcribed ({provider}): '{transcript[:80]}{'...' if len(transcript) > 80 else ''}'"
            )
        return transcript if transcript else None

    except Exception as e:
        PrintStyle.error(f"[stt_providers] Voice transcription failed ({provider}): {e}")
        return None


def patch_yatca_handle_message():
    """
    Monkey-patch YATCA's handle_message to transcribe voice attachments.
    Called once at startup. Returns True if YATCA is present and patch was applied.
    """
    try:
        import usr.plugins.yatca.helpers.handler as yatca_handler
    except ImportError:
        return False  # YATCA not installed, silently skip

    if getattr(yatca_handler, _PATCH_ATTR, False):
        return True  # Already patched

    original_handle_message = yatca_handler.handle_message

    async def patched_handle_message(message, bot_name: str, bot_cfg: dict):
        """
        Wraps YATCA's handle_message.
        After the original call downloads voice files, this wrapper cannot
        intercept easily without reimplementing the full flow.
        Instead we patch at a lower level: we override _download_attachments
        to post-process voice files inline.
        """
        return await original_handle_message(message, bot_name, bot_cfg)

    # Patch at the lower level: wrap _download_attachments
    original_download = yatca_handler._download_attachments

    async def patched_download_attachments(bot, message, bot_name: str = ""):
        """Downloads attachments then transcribes any voice/audio files."""
        paths = await original_download(bot, message, bot_name=bot_name)

        from helpers import plugins as _plugins
        cfg = _plugins.get_plugin_config("stt_providers") or {}
        provider = cfg.get("provider", "local")

        if provider == "local" or not paths:
            return paths

        # Check if this message has voice/audio
        has_voice = (
            getattr(message, "voice", None) or
            getattr(message, "audio", None) or
            getattr(message, "video_note", None)
        )
        if not has_voice:
            return paths

        transcribed_paths = []
        for path in paths:
            if is_voice_file(path):
                transcript = await transcribe_file(path)
                if transcript:
                    # Write transcript to a .txt sidecar file
                    txt_path = path + ".transcript.txt"
                    try:
                        local_txt = resolve_local_path(txt_path) if not os.path.isabs(txt_path) else txt_path
                        # Actually write next to the original file
                        base_local = resolve_local_path(path)
                        local_txt = base_local + ".transcript.txt"
                        with open(local_txt, "w", encoding="utf-8") as f:
                            f.write(f"[Transcript: {transcript}]")
                        # Use same prefix convention as original path
                        if path.startswith("/a0/"):
                            transcribed_paths.append(local_txt)
                        else:
                            transcribed_paths.append(local_txt)
                        PrintStyle.standard(f"[stt_providers] Transcript written to {local_txt}")
                    except Exception as e:
                        PrintStyle.error(f"[stt_providers] Could not write transcript file: {e}")
                        # Inject transcript as a prepended attachment anyway
                else:
                    transcribed_paths.append(path)  # keep original if transcription failed
            else:
                transcribed_paths.append(path)

        return transcribed_paths

    yatca_handler._download_attachments = patched_download_attachments
    setattr(yatca_handler, _PATCH_ATTR, True)
    PrintStyle.standard("[stt_providers] YATCA voice integration active - voice messages will be auto-transcribed")
    return True

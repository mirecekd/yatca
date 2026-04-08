"""
STT Providers Plugin - Multi-provider speech-to-text transcription.
Supports: Deepgram, OpenAI Whisper API, local Whisper (fallback).
"""

import base64
import tempfile
import os
from typing import Any

from helpers import plugins
from helpers.print_style import PrintStyle

PLUGIN_NAME = "stt_providers"


def get_config() -> dict:
    """Load plugin config with defaults."""
    cfg = plugins.get_plugin_config(PLUGIN_NAME) or {}
    return cfg


async def transcribe_deepgram(audio_bytes: bytes, cfg: dict) -> dict:
    """Transcribe audio using Deepgram REST API."""
    import httpx

    dg_cfg = cfg.get("deepgram", {})
    api_key = dg_cfg.get("api_key", "")
    model = dg_cfg.get("model", "nova-2")
    language = dg_cfg.get("language", "")

    if not api_key:
        raise ValueError("Deepgram API key is not configured.")

    params = {"model": model, "punctuate": "true", "smart_format": "true"}
    if language:
        params["language"] = language

    url = "https://api.deepgram.com/v1/listen"
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "audio/wav",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, params=params, headers=headers, content=audio_bytes)
        response.raise_for_status()
        data = response.json()

    # Extract transcript from Deepgram response
    try:
        transcript = (
            data["results"]["channels"][0]["alternatives"][0]["transcript"]
        )
    except (KeyError, IndexError):
        transcript = ""

    PrintStyle.debug(f"[stt_providers] Deepgram transcription: '{transcript[:80]}'")

    # Return in whisper-compatible format
    return {"text": transcript}


async def transcribe_openai(audio_bytes: bytes, cfg: dict) -> dict:
    """Transcribe audio using OpenAI Whisper API."""
    import httpx

    oa_cfg = cfg.get("openai", {})
    api_key = oa_cfg.get("api_key", "")
    model = oa_cfg.get("model", "whisper-1")
    language = oa_cfg.get("language", "")

    if not api_key:
        raise ValueError("OpenAI API key is not configured.")

    url = "https://api.openai.com/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}

    # Write audio to temp file - OpenAI API requires multipart form upload
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            with open(tmp_path, "rb") as audio_file:
                files = {"file": ("audio.wav", audio_file, "audio/wav")}
                data = {"model": model}
                if language:
                    data["language"] = language
                response = await client.post(
                    url, headers=headers, files=files, data=data
                )
                response.raise_for_status()
                result = response.json()
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    transcript = result.get("text", "")
    PrintStyle.debug(f"[stt_providers] OpenAI transcription: '{transcript[:80]}'")

    # Return in whisper-compatible format
    return {"text": transcript}


async def transcribe_with_provider(model_name: str, audio_bytes_b64: str) -> dict:
    """
    Main transcription dispatcher. Called instead of whisper.transcribe().
    Routes to the configured provider or falls back to local Whisper.
    """
    cfg = get_config()
    provider = cfg.get("provider", "local")

    # Decode base64 audio
    audio_bytes = base64.b64decode(audio_bytes_b64)

    if provider == "deepgram":
        try:
            return await transcribe_deepgram(audio_bytes, cfg)
        except Exception as e:
            PrintStyle.error(f"[stt_providers] Deepgram error: {e} - falling back to local Whisper")
            # Fall back to local
            from helpers import whisper as _whisper
            return await _whisper._transcribe(model_name, audio_bytes_b64)

    elif provider == "openai":
        try:
            return await transcribe_openai(audio_bytes, cfg)
        except Exception as e:
            PrintStyle.error(f"[stt_providers] OpenAI STT error: {e} - falling back to local Whisper")
            from helpers import whisper as _whisper
            return await _whisper._transcribe(model_name, audio_bytes_b64)

    else:
        # local - use original whisper
        from helpers import whisper as _whisper
        return await _whisper._transcribe(model_name, audio_bytes_b64)

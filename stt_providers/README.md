# STT Providers — Multi-Provider Speech-to-Text for Agent Zero

Replaces the built-in local Whisper STT with cloud providers. Supports **Deepgram** and **OpenAI Whisper API**, with automatic fallback to local Whisper when unconfigured.

## Features

- 🎙️ **Deepgram** — nova-2, nova-2-general, nova-2-meeting, nova-2-phonecall, nova-3, enhanced, base
- 🤖 **OpenAI Whisper API** — whisper-1
- 🏠 **Local Whisper** — built-in fallback (no API key needed)
- 📱 **YATCA integration** — auto-transcribes Telegram voice messages (.ogg)
- 🌐 **Web UI mic** — works transparently with the Agent Zero voice input button
- ⚡ **Automatic fallback** — if a provider fails, falls back to local Whisper

## Setup

1. Enable the plugin in **Plugins** settings
2. Open **Plugin Settings → STT Providers**
3. Select your provider (Deepgram or OpenAI)
4. Enter your API key
5. Set language (e.g. `cs`, `en`) or leave empty for auto-detect
6. Save — changes take effect on the next voice input

## API Keys

| Provider | Where to get |
|---|---|
| Deepgram | [console.deepgram.com](https://console.deepgram.com) — free tier available |
| OpenAI | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |

## YATCA Voice Messages

When YATCA (Telegram bot) is installed alongside this plugin, voice messages sent via Telegram are automatically transcribed using the configured provider. The transcript is attached as a text file and passed to the agent.

## Supported Audio Formats

Web UI: WAV (recorded by browser microphone)
YATCA / Telegram: OGG, OGA, MP3, M4A, WAV, FLAC, WEBM, OPUS

## Configuration Reference

```yaml
provider: deepgram  # local | deepgram | openai

deepgram:
  api_key: "your-key-here"
  model: nova-2
  language: cs  # empty = auto-detect

openai:
  api_key: "sk-..."
  model: whisper-1
  language: cs  # empty = auto-detect
```

#!/bin/bash
# YATCA Startup Script
# Usage:
#   /a0/usr/workdir/yatca_startup.sh                  - manual run (supervisord already running)
#   /a0/usr/workdir/yatca_startup.sh --pre-supervisord - called from initialize.sh before supervisord starts

set -e

MODE="$1"

echo "[YATCA] Installing Python dependencies..."
/opt/venv/bin/pip install -q python-telegram-bot aiohttp python-dotenv 2>&1 | tail -1

echo "[YATCA] Checking supervisord config..."
if ! grep -q 'telegram_bridge' /etc/supervisor/conf.d/supervisord.conf; then
    echo "[YATCA] Adding telegram_bridge to supervisord config..."
    cat >> /etc/supervisor/conf.d/supervisord.conf << 'EOF'

[program:telegram_bridge]
command=/opt/venv/bin/python3 /a0/usr/workdir/telegram_bridge.py
environment=
user=root
stopwaitsecs=10
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
autorestart=true
startretries=3
stopasgroup=true
killasgroup=true
EOF

    if [ "$MODE" != "--pre-supervisord" ]; then
        echo "[YATCA] Reloading supervisord..."
        supervisorctl reread
        supervisorctl update
    fi
else
    if [ "$MODE" != "--pre-supervisord" ]; then
        echo "[YATCA] Config already present, ensuring process is running..."
        supervisorctl start telegram_bridge 2>/dev/null || true
    fi
fi

if [ "$MODE" = "--pre-supervisord" ]; then
    echo "[YATCA] Pre-supervisord setup complete. Bridge will start with supervisord."
else
    sleep 2
    supervisorctl status telegram_bridge
    echo "[YATCA] Done!"
fi

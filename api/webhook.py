"""
YATCA Webhook API endpoint.
Receives Telegram webhook updates. No auth/CSRF -- Telegram cannot send session cookies.
"""

from helpers.api import ApiHandler, Request, Response
from helpers.print_style import PrintStyle
from usr.plugins.yatca.helpers.dependencies import ensure_dependencies


class YatcaWebhook(ApiHandler):

    @classmethod
    def requires_auth(cls) -> bool:
        return False

    @classmethod
    def requires_csrf(cls) -> bool:
        return False

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["POST"]

    async def process(self, input: dict, request: Request) -> dict | Response:
        ensure_dependencies()
        from aiogram.types import Update

        from usr.plugins.yatca.helpers.bot_manager import get_bot

        bot_name = request.args.get("bot", "")
        if not bot_name:
            return Response("Missing ?bot= parameter", 400)

        instance = get_bot(bot_name)
        if not instance:
            return Response(f"Bot not found: {bot_name}", 404)

        # Verify webhook secret if configured
        secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if instance.webhook_secret and secret_header != instance.webhook_secret:
            return Response("Invalid secret token", 403)

        try:
            update = Update.model_validate(input, context={"bot": instance.bot})
            await instance.dispatcher.feed_update(instance.bot, update)
        except Exception as e:
            PrintStyle.error(f"YATCA webhook ({bot_name}): {e}")
            return Response("Internal error", 500)

        return {"ok": True}

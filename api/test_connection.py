"""
YATCA Test Connection API endpoint.
Token validation endpoint for the settings UI.
"""

from helpers.api import ApiHandler, Request, Response
from plugins.yatca.helpers.dependencies import ensure_dependencies


class YatcaTestConnection(ApiHandler):

    @classmethod
    def get_methods(cls) -> list[str]:
        return ["POST"]

    async def process(self, input: dict, request: Request) -> dict | Response:
        token = input.get("token", "") or (input.get("bot") or {}).get("token", "")
        if not token:
            return {"ok": False, "message": "Token is required"}

        ensure_dependencies()
        from plugins.yatca.helpers.bot_manager import test_token

        ok, message = await test_token(token)
        return {"ok": ok, "message": message}

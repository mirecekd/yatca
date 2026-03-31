"""
YATCA System Prompt Extension.
Injects Telegram-specific system prompt and per-bot agent instructions
when the context belongs to a YATCA Telegram session.
"""

from helpers.extension import Extension
from agent import LoopData
from plugins.yatca.helpers.constants import CTX_TG_BOT, CTX_TG_BOT_CFG


class YatcaContextPrompt(Extension):

    async def execute(
        self,
        system_prompt: list[str] = [],
        loop_data: LoopData = LoopData(),
        **kwargs,
    ):
        if not self.agent:
            return

        if self.agent.context.data.get(CTX_TG_BOT):
            system_prompt.append(
                self.agent.read_prompt("fw.yatca.system_context_reply.md")
            )

            # Inject per-bot agent instructions (once in system prompt)
            bot_cfg = self.agent.context.data.get(CTX_TG_BOT_CFG, {})
            instructions = bot_cfg.get("agent_instructions", "")
            if instructions:
                system_prompt.append(
                    self.agent.read_prompt(
                        "fw.yatca.user_message_instructions.md",
                        instructions=instructions,
                    )
                )

PLUGIN_NAME = "yatca"
DOWNLOAD_FOLDER = "usr/uploads"
STATE_FILE = "usr/plugins/yatca/state.json"

# Context data keys
CTX_TG_BOT = "yatca_bot"
CTX_TG_BOT_CFG = "yatca_bot_cfg"
CTX_TG_CHAT_ID = "yatca_chat_id"
CTX_TG_USER_ID = "yatca_user_id"
CTX_TG_USERNAME = "yatca_username"
CTX_TG_TYPING_STOP = "_yatca_typing_stop"
CTX_TG_REPLY_TO = "_yatca_reply_to_message_id"

# Transient (used between tool_execute_after and process_chain_end)
CTX_TG_ATTACHMENTS = "_yatca_response_attachments"
CTX_TG_KEYBOARD = "_yatca_response_keyboard"

# YATCA-specific context data keys
CTX_TG_PROJECT = "yatca_project"

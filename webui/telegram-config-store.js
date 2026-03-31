/**
 * YATCA WebUI Alpine.js store for the settings modal.
 */

import Alpine from "alpinejs";

const PLUGIN_NAME = "yatca";

Alpine.store("yatcaConfig", {
    config: null,

    init() {
        this.config = Alpine.store("pluginSettings")?.getPluginConfig(PLUGIN_NAME);
    },

    get bots() {
        return this.config?.bots || [];
    },

    addBot() {
        if (!this.config) return;
        if (!this.config.bots) this.config.bots = [];
        this.config.bots.push({
            name: `bot_${this.config.bots.length + 1}`,
            enabled: true,
            token: "",
            mode: "polling",
            webhook_url: "",
            webhook_secret: "",
            allowed_users: [],
            allowed_chats: [],
            group_mode: "mention",
            welcome_enabled: false,
            welcome_message: "",
            user_projects: {},
            default_project: "",
            attachment_max_age_hours: 0,
            max_file_size_mb: 20,
            a0_timeout: 300,
            notify_messages: false,
            agent_instructions: "",
        });
    },

    removeBot(index) {
        if (!this.config?.bots) return;
        this.config.bots.splice(index, 1);
    },

    async testToken(index) {
        const bot = this.config?.bots?.[index];
        if (!bot?.token) return;

        bot._testing = true;
        bot._testResult = "";
        try {
            const resp = await fetch(`/api/plugins/${PLUGIN_NAME}/test_connection`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ token: bot.token }),
            });
            const data = await resp.json();
            bot._testResult = data.ok ? `OK: ${data.message}` : `FAIL: ${data.message}`;
        } catch (e) {
            bot._testResult = `Error: ${e.message}`;
        }
        bot._testing = false;
    },

    allowedUsersStr(bot) {
        return (bot.allowed_users || []).join(", ");
    },

    setAllowedUsers(bot, str) {
        bot.allowed_users = str
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean);
    },

    allowedChatsStr(bot) {
        return (bot.allowed_chats || []).join(", ");
    },

    setAllowedChats(bot, str) {
        bot.allowed_chats = str
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean);
    },
});

export const store = Alpine.store("yatcaConfig");

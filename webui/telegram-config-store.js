import { createStore } from "/js/AlpineStore.js";

const API_BASE = "/plugins/yatca";

export const store = createStore("yatcaConfig", {
    projects: [],
    expandedIdx: null,
    testing: null,
    testResults: null,
    _loaded: false,

    async init() {
        if (this._loaded) return;
        try {
            const { callJsonApi } = await import("/js/api.js");
            const res = await callJsonApi("projects", { action: "list" });
            this.projects = res.data || [];
        } catch (_) {
            this.projects = [];
        }
        this._loaded = true;
    },

    defaultBot() {
        return {
            name: "",
            enabled: true,
            notify_messages: false,
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
            agent_instructions: "",
        };
    },

    addBot(config) {
        if (!config.bots) config.bots = [];
        const bot = this.defaultBot();
        bot.name = "bot_" + (config.bots.length + 1);
        config.bots.push(bot);
        this.expandedIdx = config.bots.length - 1;
    },

    removeBot(config, idx) {
        config.bots.splice(idx, 1);
        this.expandedIdx = null;
    },

    toggle(idx) {
        this.expandedIdx = this.expandedIdx === idx ? null : idx;
        this.testResults = null;
    },

    whitelistText(bot) {
        return (bot.allowed_users || []).join(", ");
    },

    setWhitelist(bot, val) {
        bot.allowed_users = val
            .split(",")
            .map((s) => s.trim())
            .filter((s) => s);
    },

    allowedChatsText(bot) {
        return (bot.allowed_chats || []).join(", ");
    },

    setAllowedChats(bot, val) {
        bot.allowed_chats = val
            .split(",")
            .map((s) => s.trim())
            .filter((s) => s);
    },

    userProjectsText(bot) {
        const up = bot.user_projects || {};
        return Object.entries(up)
            .map(([k, v]) => k + "=" + v)
            .join(", ");
    },

    setUserProjects(bot, val) {
        const obj = {};
        val
            .split(",")
            .map((s) => s.trim())
            .filter((s) => s)
            .forEach((item) => {
                const parts = item.split("=").map((p) => p.trim());
                const k = parts[0];
                if (k) obj[k] = parts[1] || "";
            });
        bot.user_projects = obj;
    },

    async testConnection(config, idx) {
        this.testing = idx;
        this.testResults = null;
        try {
            const { callJsonApi } = await import("/js/api.js");
            const { toastFrontendSuccess, toastFrontendError } = await import("/js/toast.js");
            const res = await callJsonApi(`${API_BASE}/test_connection`, {
                bot: config.bots[idx],
            });
            this.testResults = res;
            if (res.success !== false && res.ok !== false) {
                toastFrontendSuccess("YATCA: connection test passed");
            } else {
                toastFrontendError("YATCA: connection test failed");
            }
        } catch (e) {
            const { toastFrontendError } = await import("/js/toast.js");
            this.testResults = {
                success: false,
                results: [{ test: "Connection", ok: false, message: String(e) }],
            };
            toastFrontendError("YATCA: connection test error");
        }
        this.testing = null;
    },
});

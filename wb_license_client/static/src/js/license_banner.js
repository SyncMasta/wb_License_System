/** @odoo-module **/

import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";

const COOKIE_PREFIX = "wb_license_banner_dismissed_";
const COOKIE_TTL_HOURS = 24;

function getCookie(name) {
    const match = document.cookie.match(
        new RegExp("(?:^|; )" + name + "=([^;]+)")
    );
    return match ? decodeURIComponent(match[1]) : null;
}

function setCookie(name, value, hours) {
    const expires = new Date(Date.now() + hours * 3600 * 1000).toUTCString();
    document.cookie = `${name}=${encodeURIComponent(value)}; expires=${expires}; path=/; SameSite=Strict`;
}

function severityFor(state) {
    if (state === "expired") return "danger";
    if (state === "grace") return "warning";
    if (state === "unknown") return "info";
    return null;
}

function buildBanner(info, severity) {
    const banner = document.createElement("div");
    banner.className = `wb_license_banner wb_license_banner--${severity}`;
    banner.setAttribute("data-product-code", info.product_code);

    const text = document.createElement("span");
    text.className = "wb_license_banner__text";
    text.textContent = info.user_message || `Lizenz ${info.product_code}: ${info.state}`;

    const dismissBtn = document.createElement("button");
    dismissBtn.className = "wb_license_banner__dismiss";
    dismissBtn.type = "button";
    dismissBtn.textContent = "×";
    dismissBtn.setAttribute("aria-label", "Ausblenden");
    dismissBtn.addEventListener("click", () => {
        setCookie(COOKIE_PREFIX + info.product_code, "1", COOKIE_TTL_HOURS);
        banner.remove();
    });

    banner.appendChild(text);
    banner.appendChild(dismissBtn);
    return banner;
}

const licenseBannerService = {
    dependencies: [],

    async start() {
        let infos;
        try {
            infos = await rpc("/wb_license_client/get_all_infos", {});
        } catch {
            return;
        }
        if (!Array.isArray(infos) || infos.length === 0) return;

        const container = document.querySelector(".o_main_navbar")?.parentElement
            || document.querySelector(".o_action_manager")
            || document.body;

        for (const info of infos) {
            const severity = severityFor(info.state);
            if (!severity) continue;
            if (getCookie(COOKIE_PREFIX + info.product_code)) continue;
            const banner = buildBanner(info, severity);
            container.insertBefore(banner, container.firstChild);
        }
    },
};

registry.category("services").add("wb_license_banner", licenseBannerService);

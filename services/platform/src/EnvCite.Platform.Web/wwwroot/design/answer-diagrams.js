// 漸進式 AI 圖表（Mermaid）渲染器：偵測畫面上出現圖表區塊時才依指紋路徑延遲載入自託管 mermaid.min.js，
// 並從 DesignTokens CSS 變數即時解析淺／深色配色與系統字型（不發起任何第三方字型或 CDN 請求）。
(() => {
    const currentScript = document.currentScript;
    const mermaidSrc = currentScript?.dataset?.mermaidSrc || "/_content/EnvCite.Platform.Web/design/mermaid.min.js";

    let loadPromise = null;
    let renderChain = Promise.resolve();
    let diagramCounter = 0;
    let initializedThemeKey = null;
    let rafHandle = 0;

    function resolveCssValue(propertyName, expr, fallback) {
        let el = document.getElementById("ep-diagram-style-probe");
        if (!el && document.body) {
            el = document.createElement("span");
            el.id = "ep-diagram-style-probe";
            el.style.position = "absolute";
            el.style.visibility = "hidden";
            el.style.pointerEvents = "none";
            document.body.appendChild(el);
        }
        if (!el) return fallback;
        el.style.setProperty(propertyName, "");
        el.style.setProperty(propertyName, expr);
        const computed = getComputedStyle(el).getPropertyValue(propertyName);
        return computed || fallback;
    }

    function currentThemeKey() {
        const systemDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
        const explicitTheme = document.documentElement ? (document.documentElement.dataset.theme || "") : "";
        return `${explicitTheme}|${systemDark ? "dark" : "light"}`;
    }

    function isDarkScheme() {
        const explicitTheme = document.documentElement ? (document.documentElement.dataset.theme || "") : "";
        if (explicitTheme === "dark") return true;
        if (explicitTheme === "light") return false;
        return Boolean(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
    }

    function buildThemeVariables() {
        const resolveColor = (expr, fallback) => resolveCssValue("color", expr, fallback);
        const surface = resolveColor("var(--ep-surface)", "#FFFFFF");
        const surfaceMuted = resolveColor("var(--ep-surface-muted)", "#F0F0F3");
        const surfaceRaised = resolveColor("var(--ep-surface-raised)", "#FFFFFF");
        const text = resolveColor("var(--ep-text)", "#1D1D1F");
        const textSecondary = resolveColor("var(--ep-text-secondary)", "#55555B");
        const primary = resolveColor("var(--ep-primary)", "#005FC4");
        const primaryTint = resolveColor("color-mix(in srgb, var(--ep-primary) 12%, var(--ep-surface))", "#E8F1FB");
        const borderSubtle = resolveColor("var(--ep-border-subtle)", "#E3E3E8");
        const borderControl = resolveColor("var(--ep-border-control)", "#86868B");
        const dark = isDarkScheme();

        return {
            dark,
            fontFamily: resolveCssValue("font-family", "var(--ep-font)", "sans-serif"),
            variables: {
                darkMode: dark,
                background: surface,
                primaryColor: primaryTint,
                primaryTextColor: text,
                primaryBorderColor: primary,
                secondaryColor: surfaceMuted,
                secondaryTextColor: text,
                secondaryBorderColor: borderControl,
                tertiaryColor: surfaceRaised,
                tertiaryTextColor: textSecondary,
                tertiaryBorderColor: borderSubtle,
                lineColor: borderControl,
                textColor: text,
                mainBkg: surfaceRaised,
                nodeBorder: primary,
                clusterBkg: surfaceMuted,
                clusterBorder: borderSubtle,
                edgeLabelBackground: surface,
                actorBkg: primaryTint,
                actorBorder: primary,
                actorTextColor: text,
                signalColor: text,
                signalTextColor: text,
                labelBoxBkgColor: surfaceRaised,
                labelBoxBorderColor: borderSubtle,
                labelTextColor: text,
            },
        };
    }

    function loadMermaid() {
        if (window.mermaid) return Promise.resolve(window.mermaid);
        if (loadPromise) return loadPromise;

        loadPromise = new Promise((resolve, reject) => {
            const script = document.createElement("script");
            script.src = mermaidSrc;
            script.async = true;
            script.onload = () => {
                if (window.mermaid) resolve(window.mermaid);
                else {
                    loadPromise = null;
                    reject(new Error("mermaid global missing after script load"));
                }
            };
            script.onerror = (err) => {
                loadPromise = null;
                reject(err);
            };
            document.head.appendChild(script);
        });
        return loadPromise;
    }

    async function renderPendingDiagrams() {
        const hosts = Array.from(document.querySelectorAll(".ep-mermaid[data-mermaid-source]"));
        if (hosts.length === 0) return;

        const themeKey = currentThemeKey();
        const needsRender = hosts.filter(
            (el) => el.dataset.diagramState !== "ready" || el.dataset.renderedThemeKey !== themeKey
        );
        if (needsRender.length === 0) return;

        let mermaid;
        try {
            mermaid = await loadMermaid();
        } catch {
            // 載入失敗時保留 <pre> 原文降級呈現。
            return;
        }

        const latestKey = currentThemeKey();
        if (initializedThemeKey !== latestKey) {
            const palette = buildThemeVariables();
            mermaid.initialize({
                startOnLoad: false,
                securityLevel: "strict",
                suppressErrorRendering: true,
                theme: "base",
                fontFamily: palette.fontFamily,
                themeVariables: palette.variables,
            });
            initializedThemeKey = latestKey;
        }

        for (const host of hosts) {
            if (!host.isConnected) continue;
            if (host.dataset.diagramState === "ready" && host.dataset.renderedThemeKey === initializedThemeKey) {
                continue;
            }

            const source = host.getAttribute("data-mermaid-source") || "";
            const canvas = host.querySelector(".ep-mermaid-canvas");
            const fallback = host.querySelector(".ep-mermaid-fallback");
            if (!source.trim() || !canvas) continue;

            diagramCounter += 1;
            const renderId = `ep-mmd-${diagramCounter}`;
            try {
                const result = await mermaid.render(renderId, source);
                if (!host.isConnected) continue;
                canvas.innerHTML = result.svg;
                result.bindFunctions?.(canvas);
                canvas.removeAttribute("hidden");
                if (fallback) fallback.hidden = true;
                host.dataset.diagramState = "ready";
                host.dataset.renderedThemeKey = initializedThemeKey;
            } catch {
                if (!host.isConnected) continue;
                canvas.innerHTML = "";
                canvas.setAttribute("hidden", "");
                if (fallback) fallback.hidden = false;
                host.dataset.diagramState = "fallback";
            }
        }
    }

    function scheduleRender() {
        if (rafHandle) return;
        rafHandle = requestAnimationFrame(() => {
            rafHandle = 0;
            renderChain = renderChain.then(renderPendingDiagrams).catch(() => {});
        });
    }

    function startObservers() {
        if (document.body) {
            new MutationObserver((mutations) => {
                for (const m of mutations) {
                    if (m.addedNodes.length > 0) {
                        scheduleRender();
                        return;
                    }
                }
            }).observe(document.body, { childList: true, subtree: true });
        }

        if (document.documentElement) {
            new MutationObserver(() => {
                if (initializedThemeKey !== currentThemeKey()) scheduleRender();
            }).observe(document.documentElement, { attributes: true });
        }

        if (window.matchMedia) {
            window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", scheduleRender);
        }
    }

    window.epDiagrams = { renderAll: scheduleRender };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => {
            startObservers();
            scheduleRender();
        });
    } else {
        startObservers();
        scheduleRender();
    }

    document.addEventListener("enhancedload", scheduleRender);
})();

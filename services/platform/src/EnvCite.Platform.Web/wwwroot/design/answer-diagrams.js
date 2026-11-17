// 漸進式 AI 圖表（Mermaid）渲染器：偵測畫面上出現圖表區塊時才依指紋路徑延遲載入自託管 mermaid.min.js，
// 並以 Mermaid 官方的 base 主題與 themeVariables，從 DesignTokens CSS 變數取淺／深色配色與字型（不發起任何第三方字型或 CDN 請求）。
(() => {
    const currentScript = document.currentScript;
    const mermaidSrc = currentScript?.dataset?.mermaidSrc || "/_content/EnvCite.Platform.Web/design/mermaid.min.js";

    let loadPromise = null;
    let renderChain = Promise.resolve();
    let diagramCounter = 0;
    let initializedThemeKey = null;
    let rafHandle = 0;

    // 設計代幣只有一個來源（DesignTokens.cs 輸出的 CSS 變數）：這裡直接讀變數的值交給 Mermaid 官方的
    // themeVariables，不寫色碼也不備援；讀不到的變數就不給，由 Mermaid 用自己的預設。
    function token(name) {
        return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || undefined;
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

    function buildTheme() {
        const surface = token("--ep-surface");
        const surfaceMuted = token("--ep-surface-muted");
        const text = token("--ep-text");
        const textSecondary = token("--ep-text-secondary");
        const borderSubtle = token("--ep-border-subtle");
        const borderControl = token("--ep-border-control");

        const variables = {
            darkMode: isDarkScheme(),
            background: surface,
            primaryColor: surfaceMuted,
            primaryTextColor: text,
            primaryBorderColor: borderControl,
            secondaryColor: surfaceMuted,
            secondaryTextColor: text,
            secondaryBorderColor: borderControl,
            tertiaryColor: surface,
            tertiaryTextColor: textSecondary,
            tertiaryBorderColor: borderSubtle,
            lineColor: borderControl,
            textColor: text,
            mainBkg: surfaceMuted,
            nodeBorder: borderControl,
            clusterBkg: surfaceMuted,
            clusterBorder: borderSubtle,
            edgeLabelBackground: surface,
            fontFamily: token("--ep-font"),
        };
        return {
            fontFamily: variables.fontFamily,
            variables: Object.fromEntries(Object.entries(variables).filter(([, value]) => value !== undefined)),
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
            const palette = buildTheme();
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
